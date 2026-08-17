"""EOD marks: official close + that day's IV, never today's smile on old days."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo

from optionda.analytics import et_date, parse_ts, read_events
from optionda.config import dividend_for_symbol, load_config, rate_for_days
from optionda.journal import log_path
from optionda.market.session import DailyClose
from optionda.models import Position
from optionda.occ import OccError, parse_occ
from optionda.paths import ensure_home
from optionda.pricing.bs import price_option, years_to_expiry
from optionda.pricing.surface import estimate_overnight_iv, load_surface_for_session

ET = ZoneInfo("America/New_York")
MARK_SCHEMA = 1
Closer = Callable[[list[str], date, date], dict[str, dict[date, DailyClose]]]


@dataclass
class HeldLot:
    position_id: str
    occ: str
    underlying: str
    expiry: date
    strike: float
    option_type: str
    qty: float
    side: str
    cost: float | None
    iv: float
    multiplier: int = 100


@dataclass
class DayBook:
    day: date
    realized_cum: float
    lots: list[HeldLot] = field(default_factory=list)
    realized_by_id: dict[str, float] = field(default_factory=dict)


@dataclass
class PositionMark:
    position_id: str
    occ: str
    close_spot: float
    model: float
    upnl: float
    iv: float
    valuation_mode: str


@dataclass
class DailyMark:
    day: date
    realized: float
    realized_cum: float
    open_upnl: float
    total: float
    delta: float | None
    n_open: int
    rows: list[PositionMark] = field(default_factory=list)


def book_on(events: list[dict[str, Any]], day: date) -> DayBook:
    lots: dict[str, HeldLot] = {}
    realized_cum = 0.0
    realized_by_id: dict[str, float] = {}
    for event in events:
        event_day = et_date(event.get("ts"))
        if event_day is None or event_day > day:
            continue
        _apply_event(lots, realized_by_id, event)
        realized_cum = sum(realized_by_id.values())
    return DayBook(
        day=day,
        realized_cum=realized_cum,
        lots=list(lots.values()),
        realized_by_id=dict(realized_by_id),
    )


def _apply_event(
    lots: dict[str, HeldLot],
    realized_by_id: dict[str, float],
    event: dict[str, Any],
) -> None:
    kind = event.get("event")
    if kind in {"add", "merge"}:
        lot = _lot_from_event(event)
        if lot is None:
            return
        current = lots.get(lot.position_id)
        if current is None:
            lots[lot.position_id] = lot
            return
        current.qty = lot.qty
        if lot.cost is not None:
            current.cost = lot.cost
        if lot.iv:
            current.iv = lot.iv
        current.occ = lot.occ
        current.underlying = lot.underlying
        return
    if kind == "sell":
        realized = _as_float(event.get("realized"))
        if realized is None:
            return
        position_id = str(event.get("id") or event.get("occ") or "")
        realized_by_id[position_id] = realized_by_id.get(position_id, 0.0) + realized
        current = lots.get(position_id)
        if current is None:
            return
        remaining = _as_float(event.get("qty_remaining"))
        if event.get("closed") or (remaining is not None and remaining <= 0):
            lots.pop(position_id, None)
            return
        if remaining is not None:
            current.qty = remaining
            return
        sold = _as_float(event.get("qty_sold"), 0.0) or 0.0
        current.qty -= sold
        if current.qty <= 0:
            lots.pop(position_id, None)
        return
    if kind == "delete":
        removed = event.get("removed") if isinstance(event.get("removed"), list) else []
        for row in removed:
            if isinstance(row, dict):
                lots.pop(str(row.get("id") or ""), None)
        return
    if kind == "refresh_iv":
        book = event.get("book") if isinstance(event.get("book"), list) else []
        for row in book:
            if not isinstance(row, dict):
                continue
            position_id = str(row.get("id") or "")
            current = lots.get(position_id)
            if current is None:
                continue
            iv = _as_float(row.get("iv"))
            if iv is not None and iv > 0:
                current.iv = iv
            cost = _as_float(row.get("cost"))
            if cost is not None:
                current.cost = cost
            qty = _as_float(row.get("qty"))
            if qty is not None:
                current.qty = qty


def _lot_from_event(event: dict[str, Any]) -> HeldLot | None:
    occ = str(event.get("occ") or "").upper()
    position_id = str(event.get("id") or occ)
    if not position_id or not occ:
        return None
    try:
        parts = parse_occ(occ)
    except OccError:
        return None
    iv = _as_float(event.get("iv"))
    if iv is None or iv <= 0:
        row = _book_row(event.get("book"), position_id)
        iv = _as_float(row.get("iv")) if row else None
    if iv is None or iv <= 0:
        return None
    qty = _as_float(event.get("qty"))
    if qty is None:
        qty = _as_float(event.get("qty_added"), 0.0) or 0.0
    cost = _as_float(event.get("cost"))
    if cost is None:
        row = _book_row(event.get("book"), position_id)
        cost = _as_float(row.get("cost")) if row else None
    return HeldLot(
        position_id=position_id,
        occ=occ,
        underlying=parts.underlying,
        expiry=parts.expiry,
        strike=parts.strike,
        option_type=parts.option_type,
        qty=qty,
        side=str(event.get("side") or "long"),
        cost=cost,
        iv=iv,
        multiplier=int(_as_float(event.get("multiplier"), 100) or 100),
    )


def _book_row(book: Any, position_id: str) -> dict[str, Any] | None:
    if not position_id or not isinstance(book, list):
        return None
    for row in book:
        if isinstance(row, dict) and str(row.get("id") or "") == position_id:
            return row
    return None


def _as_float(value: Any, default: float | None = None) -> float | None:
    if value is None or value == "":
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def mark_lot(
    lot: HeldLot,
    *,
    close: float,
    day: date,
    surface=None,
    rate: float = 0.045,
    dividend: float = 0.0,
    style: str = "american",
    sticky_delta_weight: float = 0.5,
) -> PositionMark:
    now = datetime(day.year, day.month, day.day, 16, 0, tzinfo=ET)
    years = years_to_expiry(lot.expiry, now)
    model_iv = lot.iv
    mode = "frozen"
    if (
        surface is not None
        and getattr(surface, "session_date", None) == day
        and lot.cost is not None
    ):
        position = Position(
            id=lot.position_id,
            occ_symbol=lot.occ,
            underlying=lot.underlying,
            expiry=lot.expiry,
            strike=lot.strike,
            option_type=lot.option_type,  # type: ignore[arg-type]
            qty=lot.qty,
            side=lot.side,  # type: ignore[arg-type]
            iv_frozen=lot.iv,
            iv_as_of=now,
            entry_premium=lot.cost,
            multiplier=lot.multiplier,
        )
        try:
            estimate = estimate_overnight_iv(
                surface,
                position,
                spot=close,
                years=years,
                rate=rate,
                dividend=dividend,
                sticky_delta_weight=sticky_delta_weight,
            )
            model_iv = estimate.base
            mode = "surface"
        except (ValueError, TypeError):
            mode = "frozen"
    result = price_option(
        spot=close,
        strike=lot.strike,
        years=years,
        iv=model_iv,
        rate=rate,
        dividend=dividend,
        option_type=lot.option_type,  # type: ignore[arg-type]
        style=style,  # type: ignore[arg-type]
        greeks=False,
    )
    sign = 1.0 if lot.side == "long" else -1.0
    upnl = 0.0
    if lot.cost is not None:
        upnl = (result.price - lot.cost) * lot.multiplier * lot.qty * sign
    return PositionMark(
        position_id=lot.position_id,
        occ=lot.occ,
        close_spot=close,
        model=result.price,
        upnl=upnl,
        iv=model_iv,
        valuation_mode=mode,
    )


def build_mark_series(
    account: str,
    home: Path | None = None,
    *,
    events: list[dict[str, Any]] | None = None,
    start: date | None = None,
    end: date | None = None,
    closer: Closer | None = None,
) -> list[DailyMark]:
    root = ensure_home(home)
    raw = events if events is not None else read_events(log_path(account, root))
    if not raw:
        return []
    last = end or datetime.now(ET).date()
    first = start
    if first is None:
        days = [et_date(event.get("ts")) for event in raw]
        first = min((day for day in days if day is not None), default=last)
    symbols = _underlyings(raw)
    closes = _resolve_closes(symbols, first, last, root, closer)
    session_days = sorted(
        {day for (_symbol, day) in closes if first <= day <= last}
    )
    cached = _read_mark_cache(account, root)
    cfg = load_config(root)
    series: list[DailyMark] = []
    prev_total: float | None = None
    prev_realized = 0.0
    prev_ids: set[str] = set()
    position_curves: dict[str, list[tuple[date, float]]] = {}
    for day in session_days:
        book = book_on(raw, day)
        day_closes = {
            lot.underlying: closes[(lot.underlying, day)]
            for lot in book.lots
            if (lot.underlying, day) in closes
        }
        if book.lots and not day_closes:
            continue
        surfaces = {
            symbol: load_surface_for_session(symbol, day, root)
            for symbol in {lot.underlying for lot in book.lots}
        }
        fingerprint = _fingerprint(book, day_closes, surfaces)
        hit = cached.get(day.isoformat())
        if hit is not None and hit.get("fingerprint") == fingerprint:
            mark = _mark_from_cache(hit)
        else:
            mark = _compute_day(book, day_closes, surfaces, cfg, prev_realized)
            cached[day.isoformat()] = _mark_to_cache(mark, fingerprint)
        if prev_total is None:
            mark.delta = mark.total
        else:
            mark.delta = mark.total - prev_total
        series.append(mark)
        prev_total = mark.total
        prev_realized = mark.realized_cum
        open_ids = {row.position_id for row in mark.rows}
        for row in mark.rows:
            position_curves.setdefault(row.position_id, []).append((day, row.upnl))
        for position_id in prev_ids - open_ids:
            realized = book.realized_by_id.get(position_id)
            if realized is not None:
                position_curves.setdefault(position_id, []).append((day, realized))
        prev_ids = open_ids
    _write_mark_cache(account, root, cached)
    build_mark_series.last_position_curves = position_curves  # type: ignore[attr-defined]
    return series


def take_position_curves() -> dict[str, list[tuple[date, float]]]:
    return getattr(build_mark_series, "last_position_curves", {})


def _compute_day(
    book: DayBook,
    day_closes: dict[str, DailyClose],
    surfaces: dict[str, Any],
    cfg,
    prev_realized: float,
) -> DailyMark:
    rows: list[PositionMark] = []
    for lot in book.lots:
        close = day_closes.get(lot.underlying)
        if close is None:
            continue
        years = max((lot.expiry - book.day).days, 1)
        rows.append(
            mark_lot(
                lot,
                close=close.close,
                day=book.day,
                surface=surfaces.get(lot.underlying),
                rate=rate_for_days(cfg, float(years)),
                dividend=dividend_for_symbol(cfg, lot.underlying),
                style=cfg.option_style,
                sticky_delta_weight=cfg.sticky_delta_weight,
            )
        )
    open_upnl = sum(row.upnl for row in rows)
    total = book.realized_cum + open_upnl
    return DailyMark(
        day=book.day,
        realized=book.realized_cum - prev_realized,
        realized_cum=book.realized_cum,
        open_upnl=open_upnl,
        total=total,
        delta=None,
        n_open=len(rows),
        rows=rows,
    )


def _underlyings(events: list[dict[str, Any]]) -> list[str]:
    symbols: set[str] = set()
    for event in events:
        occ = event.get("occ")
        if occ:
            try:
                symbols.add(parse_occ(str(occ)).underlying)
            except OccError:
                pass
        book = event.get("book") if isinstance(event.get("book"), list) else []
        for row in book:
            if isinstance(row, dict) and row.get("underlying"):
                symbols.add(str(row["underlying"]).upper())
            elif isinstance(row, dict) and row.get("occ"):
                try:
                    symbols.add(parse_occ(str(row["occ"])).underlying)
                except OccError:
                    pass
    return sorted(symbols)


def _resolve_closes(
    symbols: list[str],
    start: date,
    end: date,
    home: Path,
    closer: Closer | None,
) -> dict[tuple[str, date], DailyClose]:
    found: dict[tuple[str, date], DailyClose] = {}
    missing: list[str] = []
    for symbol in symbols:
        cached, covered = _read_close_cache(home, symbol)
        for day, item in cached.items():
            if start <= day <= end:
                found[(symbol, day)] = item
        if not covered or start < covered[0] or end > covered[1]:
            missing.append(symbol)
    if missing and closer is not None:
        fetched = closer(missing, start, end)
        for symbol, series in fetched.items():
            for day, item in series.items():
                found[(symbol, day)] = item
            _merge_close_cache(home, symbol, series, start, end)
        for symbol in missing:
            if symbol not in fetched:
                _merge_close_cache(home, symbol, {}, start, end)
    return found


def _closes_path(home: Path, symbol: str) -> Path:
    folder = ensure_home(home) / "closes"
    folder.mkdir(parents=True, exist_ok=True)
    return folder / f"{symbol.strip().upper()}.json"


def _read_close_cache(
    home: Path,
    symbol: str,
) -> tuple[dict[date, DailyClose], tuple[date, date] | None]:
    path = _closes_path(home, symbol)
    if not path.exists():
        return {}, None
    raw = json.loads(path.read_text(encoding="utf-8"))
    bars = raw.get("bars") if isinstance(raw.get("bars"), dict) else raw
    out: dict[date, DailyClose] = {}
    if isinstance(bars, dict):
        for key, item in bars.items():
            if key.startswith("_") or not isinstance(item, dict):
                continue
            try:
                day = date.fromisoformat(key)
            except ValueError:
                continue
            close = item.get("close")
            if close is None:
                continue
            out[day] = DailyClose(
                symbol=symbol,
                session_date=day,
                close=float(close),
                source=str(item.get("source") or "cache"),
            )
    covered = None
    start_s = raw.get("range_start")
    end_s = raw.get("range_end")
    if start_s and end_s:
        try:
            covered = (date.fromisoformat(str(start_s)), date.fromisoformat(str(end_s)))
        except ValueError:
            covered = None
    return out, covered


def _merge_close_cache(
    home: Path,
    symbol: str,
    series: dict[date, DailyClose],
    start: date,
    end: date,
) -> None:
    cached, covered = _read_close_cache(home, symbol)
    cached.update(series)
    range_start = start
    range_end = end
    if covered is not None:
        range_start = min(covered[0], start)
        range_end = max(covered[1], end)
    payload = {
        "range_start": range_start.isoformat(),
        "range_end": range_end.isoformat(),
        "bars": {
            day.isoformat(): {"close": item.close, "source": item.source}
            for day, item in sorted(cached.items())
        },
    }
    path = _closes_path(home, symbol)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _marks_path(home: Path, account: str) -> Path:
    folder = ensure_home(home) / "marks"
    folder.mkdir(parents=True, exist_ok=True)
    return folder / f"{account}.jsonl"


def _read_mark_cache(account: str, home: Path) -> dict[str, dict[str, Any]]:
    path = _marks_path(home, account)
    if not path.exists():
        return {}
    out: dict[str, dict[str, Any]] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict) and item.get("day"):
            out[str(item["day"])] = item
    return out


def _write_mark_cache(account: str, home: Path, rows: dict[str, dict[str, Any]]) -> None:
    path = _marks_path(home, account)
    lines = [
        json.dumps(rows[key], ensure_ascii=False, separators=(",", ":"))
        for key in sorted(rows)
    ]
    path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")


def _fingerprint(
    book: DayBook,
    day_closes: dict[str, DailyClose],
    surfaces: dict[str, Any],
) -> str:
    lots = [
        (
            lot.position_id,
            lot.qty,
            lot.cost,
            lot.iv,
            lot.occ,
        )
        for lot in sorted(book.lots, key=lambda item: item.position_id)
    ]
    closes = [
        (symbol, item.close)
        for symbol, item in sorted(day_closes.items())
    ]
    surface_keys = [
        f"{symbol}:{getattr(surface, 'session_date', None)}"
        for symbol, surface in sorted(surfaces.items())
        if surface is not None
    ]
    raw = repr((MARK_SCHEMA, book.realized_cum, lots, closes, surface_keys))
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def _mark_to_cache(mark: DailyMark, fingerprint: str) -> dict[str, Any]:
    return {
        "schema": MARK_SCHEMA,
        "day": mark.day.isoformat(),
        "fingerprint": fingerprint,
        "realized": mark.realized,
        "realized_cum": mark.realized_cum,
        "open_upnl": mark.open_upnl,
        "total": mark.total,
        "n_open": mark.n_open,
        "rows": [asdict(row) for row in mark.rows],
    }


def _mark_from_cache(raw: dict[str, Any]) -> DailyMark:
    rows = []
    for item in raw.get("rows") or []:
        if not isinstance(item, dict):
            continue
        rows.append(
            PositionMark(
                position_id=str(item.get("position_id") or ""),
                occ=str(item.get("occ") or ""),
                close_spot=float(item.get("close_spot") or 0.0),
                model=float(item.get("model") or 0.0),
                upnl=float(item.get("upnl") or 0.0),
                iv=float(item.get("iv") or 0.0),
                valuation_mode=str(item.get("valuation_mode") or "frozen"),
            )
        )
    return DailyMark(
        day=date.fromisoformat(str(raw["day"])),
        realized=float(raw.get("realized") or 0.0),
        realized_cum=float(raw.get("realized_cum") or 0.0),
        open_upnl=float(raw.get("open_upnl") or 0.0),
        total=float(raw.get("total") or 0.0),
        delta=None,
        n_open=int(raw.get("n_open") or len(rows)),
        rows=rows,
    )


def default_closer(home: Path | None = None) -> Closer | None:
    try:
        from optionda.market.router import MarketRouter

        router = MarketRouter(home)
        if router._alpaca is None:
            return None

        def fetch(
            symbols: list[str],
            start: date,
            end: date,
        ) -> dict[str, dict[date, DailyClose]]:
            return router.get_daily_closes_range(symbols, start, end)

        return fetch
    except Exception:  # noqa: BLE001
        return None
