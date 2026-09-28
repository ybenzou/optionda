"""Per-contract qty and model PnL% for the run-desk strategy pane.

Built from journal mutations plus cached daily closes and EOD marks.
Missing a close or a mark leaves that day's PnL blank. The cache is derived
and can be deleted.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict, dataclass, replace
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from optionda.analytics import et_date
from optionda.journal import log_path, read_ledger_events
from optionda.marks import _read_close_cache, _read_mark_cache
from optionda.occ import OccError, parse_occ
from optionda.paths import ensure_home

_MUTATIONS = frozenset({"add", "merge", "sell", "delete", "undo", "refresh_iv"})
_MULTIPLIER = 100
_CACHE_SCHEMA = 8
_STACKED = frozenset({"add", "merge", "sell", "delete", "undo"})


@dataclass(frozen=True)
class StrategyPoint:
    day: date
    qty: float
    cost: float | None
    close_spot: float | None
    model: float | None
    pnl_pct: float | None


@dataclass(frozen=True)
class TradeMark:
    day: date
    side: str
    qty: float
    price: float | None


@dataclass(frozen=True)
class ContractSeries:
    occ: str
    points: list[StrategyPoint]
    trades: list[TradeMark]
    payback_days: int | None


def payback_days(points: list[StrategyPoint]) -> int | None:
    opened = next((point for point in points if point.qty > 0), None)
    if opened is None:
        return None
    for point in points:
        if point.day < opened.day or point.pnl_pct is None:
            continue
        if point.pnl_pct >= 0:
            return (point.day - opened.day).days
    return None


def week_bounds(day: date) -> tuple[date, date]:
    """Two weeks ending Sunday, so a Monday still shows the week before."""
    this_monday = day - timedelta(days=day.weekday())
    start = this_monday - timedelta(days=7)
    return start, this_monday + timedelta(days=6)


def shift_week(day: date, delta: int) -> date:
    return day + timedelta(days=7 * delta)


def month_bounds(day: date) -> tuple[date, date]:
    start = day.replace(day=1)
    if start.month == 12:
        next_month = date(start.year + 1, 1, 1)
    else:
        next_month = date(start.year, start.month + 1, 1)
    return start, next_month - timedelta(days=1)


def shift_month(day: date, delta: int) -> date:
    month_index = day.month - 1 + delta
    year = day.year + month_index // 12
    month = month_index % 12 + 1
    return date(year, month, 1)


def year_bounds(day: date) -> tuple[date, date]:
    return date(day.year, 1, 1), date(day.year, 12, 31)


def slice_window(series: ContractSeries, start: date, end: date) -> ContractSeries:
    prior = [point for point in series.points if point.day < start]
    inside = [point for point in series.points if start <= point.day <= end]
    carried: list[StrategyPoint] = []
    still_held = bool(series.points) and series.points[-1].day >= start
    if still_held and prior and (not inside or inside[0].day != start):
        previous = prior[-1]
        if previous.qty > 0:
            carried = [replace(previous, day=start)]
        else:
            carried = [replace(previous, day=start, pnl_pct=None, model=None, close_spot=None)]
    trades = [trade for trade in series.trades if start <= trade.day <= end]
    return ContractSeries(
        occ=series.occ,
        points=[*carried, *inside],
        trades=trades,
        payback_days=series.payback_days,
    )


def build_strategy(
    events: list[dict[str, Any]],
    *,
    closes: dict[tuple[str, date], float] | None = None,
    marks: dict[tuple[str, date], tuple[float, float]] | None = None,
    end: date | None = None,
) -> list[ContractSeries]:
    mutations = [event for event in events if event.get("event") in _MUTATIONS]
    if not mutations:
        return []
    last = end or datetime.now().date()
    spot = closes or {}
    priced = marks or {}
    daily, trades = _replay(mutations, last)
    if not daily:
        return []
    held_occs = {
        occ
        for lots in daily.values()
        for occ, lot in lots.items()
        if lot.qty > 0
    }
    if not held_occs:
        return []
    trades = {occ: marks_for for occ, marks_for in trades.items() if occ in held_occs}
    out: list[ContractSeries] = []
    first_day = min(daily)
    for occ in sorted(held_occs):
        points: list[StrategyPoint] = []
        held_cost: float | None = None
        live = False
        occ_trades = trades.get(occ, [])
        for day in _weekdays(first_day, last):
            held = (daily.get(day) or {}).get(occ)
            if held is not None and held.qty > 0:
                close_spot = spot.get((held.underlying, day))
                marked = priced.get((occ, day))
                model = marked[0] if marked else None
                if model is None:
                    model = _model_price(occ, held, day, close_spot)
                points.append(
                    StrategyPoint(
                        day=day,
                        qty=held.qty,
                        cost=held.cost,
                        close_spot=close_spot,
                        model=model,
                        pnl_pct=_pnl_pct(model, held.cost),
                    )
                )
                held_cost = held.cost
                live = True
                continue
            if not live:
                continue
            exit_px = _exit_price(occ_trades, day)
            points.append(
                StrategyPoint(
                    day=day,
                    qty=0,
                    cost=held_cost,
                    close_spot=spot.get((_underlying(occ), day)),
                    model=exit_px,
                    pnl_pct=_pnl_pct(exit_px, held_cost),
                )
            )
            live = False
            held_cost = None
        out.append(
            ContractSeries(
                occ=occ,
                points=points,
                trades=occ_trades,
                payback_days=payback_days(points),
            )
        )
    return out


@dataclass
class _Lot:
    occ: str
    underlying: str
    qty: float
    cost: float | None
    iv: float | None = None


@dataclass
class _Chip:
    day: date
    order: int
    pid: str
    occ: str
    underlying: str
    delta: float
    price: float | None
    iv: float | None = None
    dead: bool = False


def _replay(
    events: list[dict[str, Any]],
    last: date,
) -> tuple[dict[date, dict[str, _Lot]], dict[str, list[TradeMark]]]:
    """File order, then place surviving deltas on their calendar day.

    Undo drops the batch it reverts, including a later undo of that undo.
    A backdated add keeps its own day and still shifts every later day, so a
    sell that was logged earlier is not overwritten.
    """
    chips: list[_Chip] = []
    stack: list[tuple[str, Any]] = []
    order = 0
    for event in events:
        kind = event.get("event")
        if kind not in _STACKED:
            continue
        if kind == "undo":
            _undo_stack(stack, int(event.get("n_events") or 1))
            continue
        day = et_date(event.get("ts"))
        if kind == "delete":
            _delete_ids(chips, stack, event)
            continue
        if day is None:
            stack.append(("delta", ()))
            continue
        made = _chips_for(event, day, order)
        order += 1
        chips.extend(made)
        stack.append(("delta", tuple(made)))
    live = [chip for chip in chips if not chip.dead and chip.day <= last]
    if not live:
        return {}, {}
    return _daily_from_chips(live, last), _trade_marks(live)


def _undo_stack(stack: list[tuple[str, Any]], n: int) -> None:
    killed: list[_Chip] = []
    restored: list[_Chip] = []
    for _ in range(max(n, 0)):
        if not stack:
            break
        action, payload = stack.pop()
        if action == "delta":
            for chip in payload:
                chip.dead = True
                killed.append(chip)
        elif action == "delete":
            for chip in payload:
                chip.dead = False
                restored.append(chip)
        elif action == "undo":
            prev_killed, prev_restored = payload
            for chip in prev_killed:
                chip.dead = False
                restored.append(chip)
            for chip in prev_restored:
                chip.dead = True
                killed.append(chip)
    stack.append(("undo", (killed, restored)))


def _delete_ids(
    chips: list[_Chip],
    stack: list[tuple[str, Any]],
    event: dict[str, Any],
) -> None:
    removed = event.get("removed") if isinstance(event.get("removed"), list) else []
    ids = {str(row.get("id") or "") for row in removed if isinstance(row, dict)}
    ids.discard("")
    killed = [chip for chip in chips if chip.pid in ids and not chip.dead]
    for chip in killed:
        chip.dead = True
    stack.append(("delete", tuple(killed)))


def _chips_for(event: dict[str, Any], day: date, order: int) -> list[_Chip]:
    kind = event.get("event")
    position_id = str(event.get("id") or event.get("occ") or "")
    occ = str(event.get("occ") or "").upper()
    if kind in {"add", "merge"} and position_id and occ:
        added = _float(event.get("qty_added"))
        if added is None or added <= 0:
            added = _float(event.get("qty")) or 0.0
        if added <= 0:
            return []
        price = _float(event.get("cost_added"))
        if price is None:
            price = _float(event.get("cost"))
        return [
            _Chip(
                day,
                order,
                position_id,
                occ,
                _underlying(occ),
                added,
                price,
                _float(event.get("iv")),
            )
        ]
    if kind == "sell" and position_id and occ:
        sold = _float(event.get("qty_sold")) or 0.0
        if sold <= 0:
            return []
        return [_Chip(day, order, position_id, occ, _underlying(occ), -sold, _float(event.get("exit")))]
    return []


def _daily_from_chips(
    live: list[_Chip],
    last: date,
) -> dict[date, dict[str, _Lot]]:
    grouped: dict[str, list[_Chip]] = {}
    for chip in live:
        grouped.setdefault(chip.pid, []).append(chip)
    folded: dict[str, tuple[dict[date, tuple[float, float | None, float | None]], str, str]] = {}
    for pid, group in grouped.items():
        group.sort(key=lambda chip: (chip.day, chip.order))
        qty = 0.0
        cost: float | None = None
        iv: float | None = None
        occ = group[-1].occ
        underlying = group[-1].underlying
        snapped: dict[date, tuple[float, float | None, float | None]] = {}
        index = 0
        while index < len(group):
            day = group[index].day
            while index < len(group) and group[index].day == day:
                chip = group[index]
                if chip.delta > 0 and chip.price is not None and qty > 0 and cost is not None:
                    cost = (qty * cost + chip.delta * chip.price) / (qty + chip.delta)
                elif chip.delta > 0 and chip.price is not None:
                    cost = chip.price
                if chip.iv is not None and chip.iv > 0:
                    iv = chip.iv
                qty += chip.delta
                if qty <= 1e-9:
                    qty = 0.0
                    cost = None
                    iv = None
                occ = chip.occ or occ
                underlying = chip.underlying or underlying
                index += 1
            snapped[day] = (qty, cost, iv)
        folded[pid] = (snapped, occ, underlying)
    first = min(chip.day for chip in live)
    carried: dict[str, tuple[float, float | None, float | None, str, str]] = {}
    daily: dict[date, dict[str, _Lot]] = {}
    for day in _weekdays(first, last):
        for pid, (snapped, occ, underlying) in folded.items():
            latest = max((stamp for stamp in snapped if stamp <= day), default=None)
            if latest is None:
                continue
            qty, cost, iv = snapped[latest]
            carried[pid] = (qty, cost, iv, occ, underlying)
        by_occ: dict[str, list[_Lot]] = {}
        for qty, cost, iv, occ, underlying in carried.values():
            if qty <= 0 or not occ:
                continue
            by_occ.setdefault(occ, []).append(_Lot(occ, underlying, qty, cost, iv))
        daily[day] = {occ: _merge_lots(lots) for occ, lots in by_occ.items()}
    return daily


def _merge_lots(lots: list[_Lot]) -> _Lot:
    qty = sum(lot.qty for lot in lots)
    weighted = [
        (lot.qty, lot.cost) for lot in lots if lot.cost is not None and lot.qty > 0
    ]
    cost = None
    if weighted and qty > 0:
        cost = sum(part * price for part, price in weighted) / sum(part for part, _ in weighted)
    iv_lot = max(lots, key=lambda lot: lot.qty if lot.iv else -1)
    return _Lot(lots[0].occ, lots[0].underlying, qty, cost, iv_lot.iv)


def _trade_marks(live: list[_Chip]) -> dict[str, list[TradeMark]]:
    ordered = sorted(live, key=lambda chip: (chip.day, chip.order))
    out: dict[str, list[TradeMark]] = {}
    for chip in ordered:
        if chip.delta == 0 or not chip.occ:
            continue
        side = "add" if chip.delta > 0 else "sell"
        out.setdefault(chip.occ, []).append(
            TradeMark(chip.day, side, abs(chip.delta), chip.price)
        )
    return out


def _underlying(occ: str) -> str:
    try:
        return parse_occ(occ).underlying
    except OccError:
        return ""


def read_strategy_store(
    account: str,
    home: Path | None = None,
    *,
    allow_stale: bool = False,
) -> list[ContractSeries] | None:
    """Charts already on disk. Does not read the journal or the network."""
    root = ensure_home(home)
    stored = _read_sqlite(root, account, allow_stale=allow_stale)
    if stored is not None:
        return stored
    cached = _read_cache(root, account)
    if cached is None or cached.get("schema") != _CACHE_SCHEMA:
        return None
    return _series_from_cache(cached)


def refresh_strategy(
    account: str,
    home: Path | None = None,
    *,
    end: date | None = None,
) -> tuple[list[ContractSeries], bool]:
    """Bring the sqlite store up to date. Run snapshots do not force a rebuild."""
    root = ensure_home(home)
    last = end or datetime.now().date()
    path = log_path(account, root)
    meta = _read_meta(root, account)
    cached = _read_sqlite(root, account)
    stamp = _stamp(root, account)
    if cached is not None and _store_fresh(meta, stamp, last):
        return cached, False
    if cached is not None and _run_tail_only(path, meta, stamp, last):
        _touch_log_size(root, account, stamp)
        return cached, False
    events = [
        event
        for event in read_ledger_events(path)
        if event.get("event") in _MUTATIONS
    ]
    closes = _load_closes(root, events, last)
    series = build_strategy(
        events,
        closes=closes,
        marks=_load_marks(root, account),
        end=last,
    )
    _write_sqlite(root, account, _stamp(root, account), last, series)
    return series, True


def load_strategy(
    account: str,
    home: Path | None = None,
    *,
    end: date | None = None,
) -> list[ContractSeries]:
    series, _changed = refresh_strategy(account, home, end=end)
    return series


def _exit_price(marks: list[TradeMark], day: date) -> float | None:
    price = None
    for trade in marks:
        if trade.day == day and trade.side == "sell" and trade.price is not None:
            price = trade.price
    return price


def _closes_through(end: date, now: datetime | None = None) -> date:
    """Last session that can have an official close. Today is excluded before 16:00 ET."""
    clock = now or datetime.now(ZoneInfo("America/New_York"))
    if clock.tzinfo is None:
        clock = clock.replace(tzinfo=ZoneInfo("America/New_York"))
    cap = end
    if clock.date() <= end:
        session_close = datetime(
            clock.year, clock.month, clock.day, 16, 0, tzinfo=clock.tzinfo
        )
        if clock < session_close:
            cap = min(cap, clock.date() - timedelta(days=1))
    while cap.weekday() >= 5:
        cap -= timedelta(days=1)
    return cap


def _pnl_pct(model: float | None, cost: float | None) -> float | None:
    """Percent change from entry premium to that day's option price."""
    if model is None or cost is None or cost <= 0:
        return None
    return (model - cost) / cost * 100.0


def _model_price(occ: str, lot: _Lot, day: date, close: float | None) -> float | None:
    if close is None or lot.iv is None or lot.iv <= 0 or lot.cost is None:
        return None
    try:
        parts = parse_occ(occ)
    except OccError:
        return None
    from optionda.config import dividend_for_symbol, load_config, rate_for_days
    from optionda.marks import HeldLot, mark_lot
    from optionda.pricing.bs import years_to_expiry
    from optionda.pricing.surface import load_surface_for_session

    years = max(years_to_expiry(parts.expiry, datetime(day.year, day.month, day.day)), 1 / 365)
    ctx = getattr(_model_price, "_ctx", None)
    if ctx is None:
        ctx = {"cfg": load_config(None), "surfaces": {}}
        setattr(_model_price, "_ctx", ctx)
    cfg = ctx["cfg"]
    key = (parts.underlying, day)
    if key not in ctx["surfaces"]:
        ctx["surfaces"][key] = load_surface_for_session(parts.underlying, day)
    surface = ctx["surfaces"][key]
    held = HeldLot(
        position_id=occ,
        occ=occ,
        underlying=parts.underlying,
        expiry=parts.expiry,
        strike=parts.strike,
        option_type=parts.option_type,
        qty=lot.qty,
        side="long",
        cost=lot.cost,
        iv=lot.iv,
    )
    try:
        marked = mark_lot(
            held,
            close=close,
            day=day,
            surface=surface,
            rate=rate_for_days(cfg, years * 365),
            dividend=dividend_for_symbol(cfg, parts.underlying),
            style=cfg.option_style,
            sticky_delta_weight=cfg.sticky_delta_weight,
        )
    except (TypeError, ValueError):
        return None
    return marked.model


def _weekdays(start: date, end: date):
    day = start
    while day <= end:
        if day.weekday() < 5:
            yield day
        day += timedelta(days=1)


def _float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _load_closes(
    home: Path,
    events: list[dict[str, Any]],
    end: date,
) -> dict[tuple[str, date], float]:
    from optionda.marks import _resolve_closes
    from optionda.occ import OccError, parse_occ

    end = _closes_through(end)
    symbols: set[str] = set()
    first: date | None = None
    for event in events:
        occ = str(event.get("occ") or "")
        if not occ:
            continue
        try:
            symbols.add(parse_occ(occ).underlying)
        except OccError:
            continue
        day = et_date(event.get("ts"))
        if day is not None and (first is None or day < first):
            first = day
    if not symbols or first is None:
        return {}

    def closer(missing: list[str], start: date, stop: date):
        from optionda.market.router import MarketRouter

        return MarketRouter(home).get_daily_closes_range(missing, start, stop)

    try:
        priced = _resolve_closes(sorted(symbols), first, end, home, closer)
    except Exception:  # noqa: BLE001 — a missed close leaves that day blank
        priced = _resolve_closes(sorted(symbols), first, end, home, None)
    return {(symbol, day): item.close for (symbol, day), item in priced.items()}


def _load_marks(home: Path, account: str) -> dict[tuple[str, date], tuple[float, float]]:
    found: dict[tuple[str, date], tuple[float, float]] = {}
    for raw in _read_mark_cache(account, home).values():
        try:
            day = date.fromisoformat(str(raw.get("day")))
        except ValueError:
            continue
        for row in raw.get("rows") or []:
            if not isinstance(row, dict):
                continue
            occ = str(row.get("occ") or "").upper()
            if not occ:
                continue
            model = _float(row.get("model"))
            upnl = _float(row.get("upnl"))
            if model is None or upnl is None:
                continue
            found[(occ, day)] = (model, upnl)
    return found


def _ledger_seq(home: Path, account: str) -> int:
    path = home / "ledger" / f"{account}.sqlite"
    if not path.exists():
        return 0
    import sqlite3

    conn = sqlite3.connect(path)
    try:
        row = conn.execute("SELECT MAX(seq) FROM events").fetchone()
    except sqlite3.OperationalError:
        return 0
    finally:
        conn.close()
    if row is None or row[0] is None:
        return 0
    return int(row[0])


def _stamp(home: Path, account: str) -> dict[str, int]:
    log = log_path(account, home)
    marks = home / "marks" / f"{account}.jsonl"
    closes = home / "closes"
    newest = 0
    if closes.is_dir():
        for path in closes.glob("*.json"):
            newest = max(newest, path.stat().st_mtime_ns)
    return {
        "log_mtime": log.stat().st_mtime_ns if log.exists() else 0,
        "log_size": log.stat().st_size if log.exists() else 0,
        "ledger_seq": _ledger_seq(home, account),
        "marks_mtime": marks.stat().st_mtime_ns if marks.exists() else 0,
        "closes_mtime": newest,
    }


_MUTATION_MARKS = tuple(
    marker
    for name in ("add", "merge", "sell", "delete", "undo")
    for marker in (
        f'"event":"{name}"'.encode(),
        f'"event": "{name}"'.encode(),
    )
)


def _db_path(home: Path, account: str) -> Path:
    folder = home / "strategy"
    folder.mkdir(parents=True, exist_ok=True)
    return folder / f"{account}.sqlite"


def _connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS contracts (
            occ TEXT PRIMARY KEY,
            payback INTEGER
        );
        CREATE TABLE IF NOT EXISTS points (
            occ TEXT NOT NULL,
            day TEXT NOT NULL,
            qty REAL NOT NULL,
            cost REAL,
            close_spot REAL,
            model REAL,
            pnl_pct REAL,
            PRIMARY KEY (occ, day)
        );
        CREATE TABLE IF NOT EXISTS trades (
            occ TEXT NOT NULL,
            day TEXT NOT NULL,
            side TEXT NOT NULL,
            qty REAL NOT NULL,
            price REAL,
            seq INTEGER NOT NULL
        );
        """
    )


def _read_meta(home: Path, account: str) -> dict[str, str]:
    path = home / "strategy" / f"{account}.sqlite"
    if not path.exists():
        return {}
    conn = _connect(path)
    try:
        _ensure_schema(conn)
        rows = conn.execute("SELECT key, value FROM meta").fetchall()
    finally:
        conn.close()
    return {str(row["key"]): str(row["value"]) for row in rows}


def _store_fresh(meta: dict[str, str], stamp: dict[str, int], end: date) -> bool:
    if meta.get("schema") != str(_CACHE_SCHEMA) or meta.get("end") != end.isoformat():
        return False
    return (
        meta.get("log_size") == str(stamp["log_size"])
        and meta.get("ledger_seq") == str(stamp["ledger_seq"])
        and meta.get("marks_mtime") == str(stamp["marks_mtime"])
        and meta.get("closes_mtime") == str(stamp["closes_mtime"])
    )


def _run_tail_only(
    path: Path,
    meta: dict[str, str],
    stamp: dict[str, int],
    end: date,
) -> bool:
    if meta.get("schema") != str(_CACHE_SCHEMA) or meta.get("end") != end.isoformat():
        return False
    if (
        meta.get("marks_mtime") != str(stamp["marks_mtime"])
        or meta.get("closes_mtime") != str(stamp["closes_mtime"])
        or meta.get("ledger_seq") != str(stamp["ledger_seq"])
    ):
        return False
    try:
        stored = int(meta.get("log_size") or "0")
    except ValueError:
        return False
    size = stamp["log_size"]
    if size < stored or not path.exists():
        return False
    if size == stored:
        return True
    with path.open("rb") as handle:
        handle.seek(stored)
        tail = handle.read()
    return not any(mark in tail for mark in _MUTATION_MARKS)


def _touch_log_size(home: Path, account: str, stamp: dict[str, int]) -> None:
    path = _db_path(home, account)
    conn = _connect(path)
    try:
        conn.execute(
            "INSERT INTO meta(key, value) VALUES('log_size', ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (str(stamp["log_size"]),),
        )
        conn.commit()
    finally:
        conn.close()


def _read_sqlite(
    home: Path,
    account: str,
    *,
    allow_stale: bool = False,
) -> list[ContractSeries] | None:
    path = home / "strategy" / f"{account}.sqlite"
    if not path.exists():
        return None
    conn = _connect(path)
    try:
        _ensure_schema(conn)
        meta = {
            str(row["key"]): str(row["value"])
            for row in conn.execute("SELECT key, value FROM meta")
        }
        if meta.get("schema") != str(_CACHE_SCHEMA) and not allow_stale:
            return None
        contracts = conn.execute(
            "SELECT occ, payback FROM contracts ORDER BY occ"
        ).fetchall()
        out: list[ContractSeries] = []
        for contract in contracts:
            occ = str(contract["occ"])
            points = [
                StrategyPoint(
                    day=date.fromisoformat(str(row["day"])),
                    qty=float(row["qty"] or 0),
                    cost=row["cost"],
                    close_spot=row["close_spot"],
                    model=row["model"],
                    pnl_pct=row["pnl_pct"],
                )
                for row in conn.execute(
                    "SELECT day, qty, cost, close_spot, model, pnl_pct "
                    "FROM points WHERE occ = ? ORDER BY day",
                    (occ,),
                )
            ]
            trades = [
                TradeMark(
                    day=date.fromisoformat(str(row["day"])),
                    side=str(row["side"]),
                    qty=float(row["qty"]),
                    price=row["price"],
                )
                for row in conn.execute(
                    "SELECT day, side, qty, price FROM trades "
                    "WHERE occ = ? ORDER BY seq",
                    (occ,),
                )
            ]
            payback = contract["payback"]
            out.append(
                ContractSeries(
                    occ=occ,
                    points=points,
                    trades=trades,
                    payback_days=int(payback) if payback is not None else None,
                )
            )
    finally:
        conn.close()
    return out


def _write_sqlite(
    home: Path,
    account: str,
    stamp: dict[str, int],
    end: date,
    series: list[ContractSeries],
) -> None:
    path = _db_path(home, account)
    conn = _connect(path)
    try:
        _ensure_schema(conn)
        conn.execute("DELETE FROM contracts")
        conn.execute("DELETE FROM points")
        conn.execute("DELETE FROM trades")
        conn.execute("DELETE FROM meta")
        meta = {
            "schema": str(_CACHE_SCHEMA),
            "end": end.isoformat(),
            "log_size": str(stamp["log_size"]),
            "ledger_seq": str(stamp["ledger_seq"]),
            "marks_mtime": str(stamp["marks_mtime"]),
            "closes_mtime": str(stamp["closes_mtime"]),
        }
        conn.executemany(
            "INSERT INTO meta(key, value) VALUES(?, ?)",
            list(meta.items()),
        )
        for item in series:
            conn.execute(
                "INSERT INTO contracts(occ, payback) VALUES(?, ?)",
                (item.occ, item.payback_days),
            )
            conn.executemany(
                "INSERT INTO points(occ, day, qty, cost, close_spot, model, pnl_pct) "
                "VALUES(?, ?, ?, ?, ?, ?, ?)",
                [
                    (
                        item.occ,
                        point.day.isoformat(),
                        point.qty,
                        point.cost,
                        point.close_spot,
                        point.model,
                        point.pnl_pct,
                    )
                    for point in item.points
                ],
            )
            conn.executemany(
                "INSERT INTO trades(occ, day, side, qty, price, seq) "
                "VALUES(?, ?, ?, ?, ?, ?)",
                [
                    (item.occ, trade.day.isoformat(), trade.side, trade.qty, trade.price, index)
                    for index, trade in enumerate(item.trades)
                ],
            )
        conn.commit()
    finally:
        conn.close()


def _cache_path(home: Path, account: str) -> Path:
    folder = home / "strategy"
    folder.mkdir(parents=True, exist_ok=True)
    return folder / f"{account}.json"


def _read_cache(home: Path, account: str) -> dict[str, Any] | None:
    path = home / "strategy" / f"{account}.json"
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    return raw if isinstance(raw, dict) else None


def _write_cache(
    home: Path,
    account: str,
    stamp: dict[str, int],
    end: date,
    series: list[ContractSeries],
) -> None:
    payload = {
        "schema": _CACHE_SCHEMA,
        "stamp": stamp,
        "end": end.isoformat(),
        "series": [_series_payload(item) for item in series],
    }
    _cache_path(home, account).write_text(
        json.dumps(payload, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def _series_payload(series: ContractSeries) -> dict[str, Any]:
    return {
        "occ": series.occ,
        "payback_days": series.payback_days,
        "points": [
            {
                "day": point.day.isoformat(),
                "qty": point.qty,
                "cost": point.cost,
                "close_spot": point.close_spot,
                "model": point.model,
                "pnl_pct": point.pnl_pct,
            }
            for point in series.points
        ],
        "trades": [asdict(trade) | {"day": trade.day.isoformat()} for trade in series.trades],
    }


def _series_from_cache(raw: dict[str, Any]) -> list[ContractSeries]:
    out: list[ContractSeries] = []
    for item in raw.get("series") or []:
        if not isinstance(item, dict):
            continue
        points = []
        for row in item.get("points") or []:
            if not isinstance(row, dict):
                continue
            points.append(
                StrategyPoint(
                    day=date.fromisoformat(str(row["day"])),
                    qty=float(row.get("qty") or 0),
                    cost=_float(row.get("cost")),
                    close_spot=_float(row.get("close_spot")),
                    model=_float(row.get("model")),
                    pnl_pct=_float(row.get("pnl_pct")),
                )
            )
        trades = []
        for row in item.get("trades") or []:
            if not isinstance(row, dict):
                continue
            qty = _float(row.get("qty"))
            if qty is None:
                continue
            trades.append(
                TradeMark(
                    day=date.fromisoformat(str(row["day"])),
                    side=str(row.get("side") or "add"),
                    qty=qty,
                    price=_float(row.get("price")),
                )
            )
        payback = item.get("payback_days")
        out.append(
            ContractSeries(
                occ=str(item.get("occ") or ""),
                points=points,
                trades=trades,
                payback_days=int(payback) if payback is not None else None,
            )
        )
    return out
