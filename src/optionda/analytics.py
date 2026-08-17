"""Read-only journal analytics for the Stats desk.

Realized curves still come from ``sell`` events. Daily floating P&L is
official close + that day's IV (see ``optionda.marks``), not 15s ``run`` marks.
"""

from __future__ import annotations

import calendar
import json
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Literal
from zoneinfo import ZoneInfo

from optionda.journal import log_path
from optionda.occ import OccError, parse_occ
from optionda.paths import ensure_home

ET = ZoneInfo("America/New_York")
Period = Literal["1m", "3m", "6m", "all"]
PERIODS: tuple[Period, ...] = ("1m", "3m", "6m", "all")
DTE_BUCKETS = ("0-7", "8-30", "31-90", "91+")


def parse_ts(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def et_date(value: datetime | str | None) -> date | None:
    instant = parse_ts(value)
    if instant is None:
        return None
    return instant.astimezone(ET).date()


def calendar_dte(expiry: date | str | None, when: datetime | str | None) -> int | None:
    if expiry is None or when is None:
        return None
    if isinstance(expiry, str):
        try:
            expiry = date.fromisoformat(expiry)
        except ValueError:
            return None
    day = et_date(when)
    if day is None:
        return None
    return (expiry - day).days


def hold_days(opened: datetime | str | None, closed: datetime | str | None) -> float | None:
    start = parse_ts(opened)
    end = parse_ts(closed)
    if start is None or end is None:
        return None
    return max((end - start).total_seconds() / 86400.0, 0.0)


def shift_months(day: date, months: int) -> date:
    year = day.year
    month = day.month - months
    while month <= 0:
        month += 12
        year -= 1
    last = calendar.monthrange(year, month)[1]
    return date(year, month, min(day.day, last))


def period_start(end: date, period: Period) -> date | None:
    if period == "all":
        return None
    months = {"1m": 1, "3m": 3, "6m": 6}[period]
    return shift_months(end, months)


def in_period(day: date | None, start: date | None, end: date) -> bool:
    if day is None:
        return False
    if day > end:
        return False
    if start is None:
        return True
    return day >= start


def dte_bucket(dte: int | float | None) -> str | None:
    if dte is None:
        return None
    days = int(dte)
    if days <= 7:
        return "0-7"
    if days <= 30:
        return "8-30"
    if days <= 90:
        return "31-90"
    return "91+"


def _as_float(value: Any, default: float | None = None) -> float | None:
    if value is None or value == "":
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _as_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _occ_meta(occ: str | None) -> tuple[str, date | None, str | None]:
    if not occ:
        return "?", None, None
    try:
        parts = parse_occ(str(occ))
    except OccError:
        return str(occ).split("2")[0] or "?", None, None
    return parts.underlying, parts.expiry, parts.option_type


def _book_row(book: Any, position_id: str | None) -> dict[str, Any] | None:
    if not position_id or not isinstance(book, list):
        return None
    for row in book:
        if isinstance(row, dict) and str(row.get("id") or "") == position_id:
            return row
    return None


def read_events(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    events: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict):
            events.append(event)
    return events


@dataclass(frozen=True)
class WinRate:
    wins: int = 0
    total: int = 0

    @property
    def rate(self) -> float | None:
        if self.total <= 0:
            return None
        return self.wins / self.total

    def label(self) -> str:
        if self.total <= 0:
            return "—/—  —"
        pct = round(self.rate * 100) if self.rate is not None else 0
        return f"{self.wins}/{self.total} {pct}%"


@dataclass
class SellRecord:
    ts: datetime
    et_date: date
    position_id: str
    occ: str
    underlying: str
    side: str
    option_type: str | None
    qty_sold: float
    exit_premium: float | None
    avg_cost: float | None
    realized: float
    closed: bool
    hold_days: float | None
    dte_at_exit: int | None


@dataclass
class EntryRecord:
    ts: datetime
    et_date: date
    position_id: str
    occ: str
    underlying: str
    side: str
    option_type: str | None
    qty_added: float
    merged: bool
    dte_at_entry: int | None
    expiry: date | None


@dataclass
class ClosedLot:
    position_id: str
    occ: str
    underlying: str
    side: str
    option_type: str | None
    opened_at: datetime | None
    closed_at: datetime
    realized: float
    hold_days: float | None
    n_sells: int
    et_date: date


@dataclass
class OpenLot:
    position_id: str
    occ: str
    underlying: str
    side: str
    option_type: str | None
    opened_at: datetime | None
    hold_days: float | None
    qty: float
    upnl: float | None
    notional: float | None
    dte: float | None
    cost: float | None = None
    model: float | None = None


@dataclass
class DailyPnl:
    day: date
    realized: float
    n_sells: int
    sells: list[SellRecord] = field(default_factory=list)
    mark_delta: float | None = None
    open_upnl: float | None = None
    total: float | None = None


@dataclass
class GroupStat:
    key: str
    realized: float = 0.0
    n_sells: int = 0
    n_closed: int = 0
    sell_wins: int = 0
    lot_wins: int = 0


@dataclass
class BookSnapshot:
    ts: datetime | None = None
    source: str | None = None
    sum_upnl: float | None = None
    sum_model: float | None = None
    n: int = 0
    avg_dte: float | None = None
    rows: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class Behavior:
    n_adds: int = 0
    n_merges: int = 0
    n_deletes: int = 0
    call_qty: float = 0.0
    put_qty: float = 0.0
    long_qty: float = 0.0
    short_qty: float = 0.0
    dte_buckets: dict[str, int] = field(
        default_factory=lambda: {name: 0 for name in DTE_BUCKETS}
    )
    by_ticker: list[tuple[str, float]] = field(default_factory=list)
    avg_hold_days: float | None = None
    avg_entry_dte: float | None = None


@dataclass
class StatsReport:
    account: str
    period: Period
    as_of: date
    period_start: date | None
    realized: float = 0.0
    n_sells: int = 0
    n_closed: int = 0
    n_deletes: int = 0
    sell_win: WinRate = field(default_factory=WinRate)
    lot_win: WinRate = field(default_factory=WinRate)
    avg_hold_days: float | None = None
    open_upnl: float | None = None
    calendar: list[DailyPnl] = field(default_factory=list)
    cumulative: list[tuple[date, float]] = field(default_factory=list)
    by_ticker: list[GroupStat] = field(default_factory=list)
    by_occ: list[GroupStat] = field(default_factory=list)
    closed_lots: list[ClosedLot] = field(default_factory=list)
    open_lots: list[OpenLot] = field(default_factory=list)
    behavior: Behavior = field(default_factory=Behavior)
    book: BookSnapshot = field(default_factory=BookSnapshot)
    selected_month: date = field(default_factory=lambda: date.today())
    marks: list[Any] = field(default_factory=list)
    mark_curve: list[tuple[date, float]] = field(default_factory=list)
    position_curves: dict[str, list[tuple[date, float]]] = field(default_factory=dict)
    total_pnl: float | None = None


def _win_rate(values: Iterable[float]) -> WinRate:
    total = 0
    wins = 0
    for value in values:
        total += 1
        if value > 0:
            wins += 1
    return WinRate(wins=wins, total=total)


def _parse_sell(event: dict[str, Any], first_open: dict[str, datetime]) -> SellRecord | None:
    ts = parse_ts(event.get("ts"))
    day = et_date(ts)
    if ts is None or day is None:
        return None
    realized = _as_float(event.get("realized"))
    if realized is None:
        return None
    occ = str(event.get("occ") or "?").upper()
    position_id = str(event.get("id") or occ)
    underlying, expiry, option_type = _occ_meta(occ)
    book_row = _book_row(event.get("book"), position_id)
    if book_row:
        underlying = str(book_row.get("underlying") or underlying).upper()
        option_type = book_row.get("option_type") or option_type
        if expiry is None and book_row.get("expiry"):
            try:
                expiry = date.fromisoformat(str(book_row["expiry"]))
            except ValueError:
                expiry = None
    stored_hold = _as_float(event.get("hold_days"))
    inferred_hold = hold_days(first_open.get(position_id), ts)
    dte_exit = _as_int(event.get("dte_at_exit"))
    if dte_exit is None:
        dte_exit = calendar_dte(expiry, ts)
    return SellRecord(
        ts=ts,
        et_date=day,
        position_id=position_id,
        occ=occ,
        underlying=underlying,
        side=str(event.get("side") or "long"),
        option_type=option_type if option_type in {"call", "put"} else None,
        qty_sold=_as_float(event.get("qty_sold"), 0.0) or 0.0,
        exit_premium=_as_float(event.get("exit")),
        avg_cost=_as_float(event.get("avg_cost")),
        realized=realized,
        closed=bool(event.get("closed")),
        hold_days=stored_hold if stored_hold is not None else inferred_hold,
        dte_at_exit=dte_exit,
    )


def _parse_entry(event: dict[str, Any]) -> EntryRecord | None:
    ts = parse_ts(event.get("ts"))
    day = et_date(ts)
    if ts is None or day is None:
        return None
    occ = str(event.get("occ") or "?").upper()
    position_id = str(event.get("id") or occ)
    underlying, expiry, option_type = _occ_meta(occ)
    book_row = _book_row(event.get("book"), position_id)
    if book_row:
        underlying = str(book_row.get("underlying") or underlying).upper()
        option_type = book_row.get("option_type") or option_type
        if expiry is None and book_row.get("expiry"):
            try:
                expiry = date.fromisoformat(str(book_row["expiry"]))
            except ValueError:
                expiry = None
    dte = _as_int(event.get("dte_at_entry"))
    if dte is None:
        dte = calendar_dte(expiry, ts)
    return EntryRecord(
        ts=ts,
        et_date=day,
        position_id=position_id,
        occ=occ,
        underlying=underlying,
        side=str(event.get("side") or "long"),
        option_type=option_type if option_type in {"call", "put"} else None,
        qty_added=_as_float(event.get("qty_added"), 0.0) or 0.0,
        merged=event.get("event") == "merge",
        dte_at_entry=dte,
        expiry=expiry,
    )


def _parse_book(event: dict[str, Any]) -> BookSnapshot:
    rows_in = event.get("rows") if isinstance(event.get("rows"), list) else []
    rows: list[dict[str, Any]] = []
    dtes: list[float] = []
    for row in rows_in:
        if not isinstance(row, dict):
            continue
        occ = str(row.get("occ") or "?").upper()
        underlying, _, option_type = _occ_meta(occ)
        dte = _as_float(row.get("dte"))
        if dte is not None:
            dtes.append(dte)
        rows.append(
            {
                "occ": occ,
                "underlying": underlying,
                "side": row.get("side") or "long",
                "option_type": option_type,
                "qty": _as_float(row.get("qty"), 0.0) or 0.0,
                "upnl": _as_float(row.get("upnl")),
                "notional": _as_float(row.get("notional")),
                "dte": dte,
                "cost": _as_float(row.get("cost")),
                "model": _as_float(row.get("model")),
            }
        )
    return BookSnapshot(
        ts=parse_ts(event.get("ts")),
        source=str(event.get("event") or ""),
        sum_upnl=_as_float(event.get("sum_upnl")),
        sum_model=_as_float(event.get("sum_model")),
        n=int(event.get("n") or len(rows)),
        avg_dte=(sum(dtes) / len(dtes)) if dtes else None,
        rows=rows,
    )


def _closed_lots(
    sells: list[SellRecord],
    first_open: dict[str, datetime],
) -> list[ClosedLot]:
    grouped: dict[str, list[SellRecord]] = defaultdict(list)
    for sell in sells:
        grouped[sell.position_id].append(sell)
    lots: list[ClosedLot] = []
    for position_id, group in grouped.items():
        group.sort(key=lambda item: item.ts)
        if not any(item.closed for item in group):
            continue
        last = group[-1]
        realized = sum(item.realized for item in group)
        opened = first_open.get(position_id)
        stored = next((item.hold_days for item in reversed(group) if item.hold_days is not None), None)
        lots.append(
            ClosedLot(
                position_id=position_id,
                occ=last.occ,
                underlying=last.underlying,
                side=last.side,
                option_type=last.option_type,
                opened_at=opened,
                closed_at=last.ts,
                realized=realized,
                hold_days=stored if stored is not None else hold_days(opened, last.ts),
                n_sells=len(group),
                et_date=last.et_date,
            )
        )
    lots.sort(key=lambda item: item.closed_at, reverse=True)
    return lots


def _group_stats(
    sells: list[SellRecord],
    lots: list[ClosedLot],
    key_fn,
) -> list[GroupStat]:
    stats: dict[str, GroupStat] = {}
    for sell in sells:
        key = key_fn(sell)
        stat = stats.setdefault(key, GroupStat(key=key))
        stat.realized += sell.realized
        stat.n_sells += 1
        if sell.realized > 0:
            stat.sell_wins += 1
    for lot in lots:
        key = key_fn(lot)
        stat = stats.setdefault(key, GroupStat(key=key))
        stat.n_closed += 1
        if lot.realized > 0:
            stat.lot_wins += 1
    return sorted(stats.values(), key=lambda item: abs(item.realized), reverse=True)


def _behavior(
    entries: list[EntryRecord],
    lots: list[ClosedLot],
    n_deletes: int,
) -> Behavior:
    buckets = {name: 0 for name in DTE_BUCKETS}
    ticker_qty: dict[str, float] = defaultdict(float)
    entry_dtes: list[int] = []
    call_qty = put_qty = long_qty = short_qty = 0.0
    n_adds = n_merges = 0
    for entry in entries:
        if entry.merged:
            n_merges += 1
        else:
            n_adds += 1
        qty = entry.qty_added
        if entry.option_type == "put":
            put_qty += qty
        else:
            call_qty += qty
        if entry.side == "short":
            short_qty += qty
        else:
            long_qty += qty
        ticker_qty[entry.underlying] += qty
        if entry.dte_at_entry is not None:
            entry_dtes.append(entry.dte_at_entry)
            bucket = dte_bucket(entry.dte_at_entry)
            if bucket:
                buckets[bucket] += 1
    holds = [lot.hold_days for lot in lots if lot.hold_days is not None]
    return Behavior(
        n_adds=n_adds,
        n_merges=n_merges,
        n_deletes=n_deletes,
        call_qty=call_qty,
        put_qty=put_qty,
        long_qty=long_qty,
        short_qty=short_qty,
        dte_buckets=buckets,
        by_ticker=sorted(ticker_qty.items(), key=lambda item: item[1], reverse=True),
        avg_hold_days=(sum(holds) / len(holds)) if holds else None,
        avg_entry_dte=(sum(entry_dtes) / len(entry_dtes)) if entry_dtes else None,
    )


def _open_lots_from_ids(
    book: BookSnapshot,
    first_open: dict[str, datetime],
    last_occ_for_id: dict[str, str],
    as_of: datetime,
) -> list[OpenLot]:
    occ_to_id: dict[str, str] = {}
    for position_id, occ in last_occ_for_id.items():
        occ_to_id.setdefault(occ, position_id)
    lots: list[OpenLot] = []
    for row in book.rows:
        occ = str(row.get("occ") or "?").upper()
        position_id = occ_to_id.get(occ, occ)
        opened = first_open.get(position_id)
        lots.append(
            OpenLot(
                position_id=position_id,
                occ=occ,
                underlying=str(row.get("underlying") or _occ_meta(occ)[0]),
                side=str(row.get("side") or "long"),
                option_type=row.get("option_type"),
                opened_at=opened,
                hold_days=hold_days(opened, as_of) if opened else None,
                qty=float(row.get("qty") or 0.0),
                upnl=_as_float(row.get("upnl")),
                notional=_as_float(row.get("notional")),
                dte=_as_float(row.get("dte")),
                cost=_as_float(row.get("cost")),
                model=_as_float(row.get("model")),
            )
        )
    return lots


def build_report(
    account: str,
    home: Path | None = None,
    *,
    period: Period = "all",
    as_of: datetime | None = None,
    events: list[dict[str, Any]] | None = None,
    closer: Any = None,
    with_marks: bool = True,
) -> StatsReport:
    """Aggregate one account journal into a Stats snapshot."""
    if period not in PERIODS:
        raise ValueError(f"period must be one of {PERIODS}")
    root = ensure_home(home)
    instant = parse_ts(as_of) or datetime.now(timezone.utc)
    end = instant.astimezone(ET).date()
    start = period_start(end, period)
    raw = events if events is not None else read_events(log_path(account, root))

    first_open: dict[str, datetime] = {}
    last_occ_for_id: dict[str, str] = {}
    entries_all: list[EntryRecord] = []
    sells_all: list[SellRecord] = []
    book = BookSnapshot()

    # First pass: first-open times so sell hold_days can be inferred.
    for event in raw:
        kind = event.get("event")
        if kind not in {"add", "merge"}:
            continue
        ts = parse_ts(event.get("ts"))
        position_id = str(event.get("id") or event.get("occ") or "")
        if ts is None or not position_id:
            continue
        first_open.setdefault(position_id, ts)
        occ = str(event.get("occ") or "").upper()
        if occ:
            last_occ_for_id[position_id] = occ

    for event in raw:
        kind = event.get("event")
        if kind in {"add", "merge"}:
            entry = _parse_entry(event)
            if entry is not None:
                entries_all.append(entry)
                last_occ_for_id[entry.position_id] = entry.occ
        elif kind == "sell":
            sell = _parse_sell(event, first_open)
            if sell is not None:
                sells_all.append(sell)
                last_occ_for_id[sell.position_id] = sell.occ
        elif kind in {"run", "export"}:
            book = _parse_book(event)

    if book.source is None:
        from optionda.marks import book_on

        replayed = book_on(raw, end)
        book = BookSnapshot(
            source="replay",
            n=len(replayed.lots),
            rows=[
                {
                    "occ": lot.occ,
                    "underlying": lot.underlying,
                    "side": lot.side,
                    "option_type": lot.option_type,
                    "qty": lot.qty,
                    "cost": lot.cost,
                    "upnl": None,
                    "notional": None,
                    "dte": None,
                    "model": None,
                }
                for lot in replayed.lots
            ],
        )

    sells = [item for item in sells_all if in_period(item.et_date, start, end)]
    entries = [item for item in entries_all if in_period(item.et_date, start, end)]
    lots = [item for item in _closed_lots(sells_all, first_open) if in_period(item.et_date, start, end)]

    by_day: dict[date, DailyPnl] = {}
    for sell in sorted(sells, key=lambda item: item.ts):
        daily = by_day.setdefault(
            sell.et_date,
            DailyPnl(day=sell.et_date, realized=0.0, n_sells=0),
        )
        daily.realized += sell.realized
        daily.n_sells += 1
        daily.sells.append(sell)
    calendar_days = [by_day[day] for day in sorted(by_day)]
    running = 0.0
    cumulative: list[tuple[date, float]] = []
    for daily in calendar_days:
        running += daily.realized
        cumulative.append((daily.day, running))

    hold_values = [lot.hold_days for lot in lots if lot.hold_days is not None]
    deletes_in_period = 0
    for event in raw:
        if event.get("event") != "delete":
            continue
        day = et_date(event.get("ts"))
        if not in_period(day, start, end):
            continue
        removed = event.get("removed") if isinstance(event.get("removed"), list) else []
        deletes_in_period += len(removed) if removed else 1

    month_anchor = date(end.year, end.month, 1)
    marks: list[Any] = []
    mark_curve: list[tuple[date, float]] = []
    position_curves: dict[str, list[tuple[date, float]]] = {}
    if with_marks:
        from optionda.marks import build_mark_series, default_closer, take_position_curves

        buy_days = [
            day
            for day in (
                et_date(event.get("ts"))
                for event in raw
                if event.get("event") in {"add", "merge"}
            )
            if day is not None
        ]
        event_days = [day for day in (et_date(event.get("ts")) for event in raw) if day is not None]
        mark_start = start or (min(buy_days) if buy_days else min(event_days, default=end))
        try:
            marks = build_mark_series(
                account,
                root,
                events=raw,
                start=mark_start,
                end=end,
                closer=closer if closer is not None else default_closer(root),
            )
            position_curves = take_position_curves()
            mark_curve = [(item.day, item.total) for item in marks]
            for item in marks:
                daily = by_day.setdefault(
                    item.day,
                    DailyPnl(day=item.day, realized=0.0, n_sells=0),
                )
                daily.mark_delta = item.delta
                daily.open_upnl = item.open_upnl
                daily.total = item.total
            calendar_days = [by_day[day] for day in sorted(by_day)]
        except Exception:  # noqa: BLE001
            marks = []
            mark_curve = []
            position_curves = {}

    return StatsReport(
        account=account,
        period=period,
        as_of=end,
        period_start=start,
        realized=sum(item.realized for item in sells),
        n_sells=len(sells),
        n_closed=len(lots),
        n_deletes=deletes_in_period,
        sell_win=_win_rate(item.realized for item in sells),
        lot_win=_win_rate(item.realized for item in lots),
        avg_hold_days=(sum(hold_values) / len(hold_values)) if hold_values else None,
        open_upnl=book.sum_upnl,
        calendar=calendar_days,
        cumulative=cumulative,
        by_ticker=_group_stats(sells, lots, lambda item: item.underlying),
        by_occ=_group_stats(sells, lots, lambda item: item.occ),
        closed_lots=lots,
        open_lots=_open_lots_from_ids(book, first_open, last_occ_for_id, instant),
        behavior=_behavior(entries, lots, deletes_in_period),
        book=book,
        selected_month=month_anchor,
        marks=marks,
        mark_curve=mark_curve,
        position_curves=position_curves,
        total_pnl=mark_curve[-1][1] if mark_curve else None,
    )


def month_cells(year: int, month: int) -> list[date | None]:
    """Sunday-first month grid (6x7), None for leading/trailing blanks."""
    first_weekday, n_days = calendar.monthrange(year, month)
    # calendar.monthrange: Monday=0 … Sunday=6. Convert to Sunday-first.
    leading = (first_weekday + 1) % 7
    cells: list[date | None] = [None] * leading
    for day in range(1, n_days + 1):
        cells.append(date(year, month, day))
    while len(cells) % 7:
        cells.append(None)
    while len(cells) < 42:
        cells.append(None)
    return cells


def daily_map(report: StatsReport) -> dict[date, DailyPnl]:
    return {item.day: item for item in report.calendar}
