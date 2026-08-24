from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any
from uuid import uuid4

from optionda.analytics import parse_ts, read_events
from optionda.journal import append_undo_event, log_path, sync_book
from optionda.models import Position
from optionda.occ import OccError, parse_occ
from optionda.store import AccountStore, StoreError

MUTATIONS = frozenset({"add", "merge", "sell", "delete", "undo"})
CLUSTER_SEC = 30.0


@dataclass
class UndoResult:
    n_events: int
    realized: float
    undone_batch_id: str | None
    labels: list[str] = field(default_factory=list)


def new_batch_id() -> str:
    return uuid4().hex[:12]


def last_operation_times(events: list[dict[str, Any]]) -> dict[str, datetime]:
    """Latest add/merge/sell time per position id or OCC, after applying undos."""
    last: dict[str, datetime] = {}
    for index, event in enumerate(events):
        kind = event.get("event")
        if kind in {"add", "merge", "sell"}:
            ts = parse_ts(event.get("ts"))
            if ts is None:
                continue
            for key in (event.get("id"), event.get("occ")):
                if key:
                    last[str(key)] = ts
            continue
        if kind == "undo":
            start, _ = last_batch_span(events[:index])
            last = last_operation_times(events[:start] if start >= 0 else [])
    return last


def last_batch(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    start, end = last_batch_span(events)
    if start < 0:
        return []
    return events[start : end + 1]


def last_batch_span(events: list[dict[str, Any]]) -> tuple[int, int]:
    index = len(events) - 1
    while index >= 0 and events[index].get("event") not in MUTATIONS:
        index -= 1
    if index < 0:
        return -1, -1
    if events[index].get("event") == "undo":
        return index, index
    batch_id = events[index].get("batch_id")
    start = index
    if batch_id:
        while start > 0 and events[start - 1].get("batch_id") == batch_id:
            start -= 1
        return start, index
    while start > 0:
        previous = events[start - 1]
        if previous.get("event") not in MUTATIONS or previous.get("event") == "undo":
            break
        if previous.get("batch_id"):
            break
        if _gap_seconds(previous, events[start]) > CLUSTER_SEC:
            break
        start -= 1
    return start, index


def book_before(
    events: list[dict[str, Any]],
    start: int,
) -> list[dict[str, Any]]:
    for index in range(start - 1, -1, -1):
        book = events[index].get("book")
        if isinstance(book, list):
            return [row for row in book if isinstance(row, dict)]
    return []


def positions_from_book(book: list[dict[str, Any]]) -> list[Position]:
    positions: list[Position] = []
    now = datetime.now(timezone.utc)
    for row in book:
        pos = _position_from_brief(row, now)
        if pos is not None:
            positions.append(pos)
    return positions


def undo_last(store: AccountStore) -> UndoResult:
    account = store.require_current()
    events = read_events(log_path(account.name, store.home))
    start, end = last_batch_span(events)
    if start < 0:
        raise StoreError("nothing to undo")
    batch = events[start : end + 1]
    previous = book_before(events, start)
    if len(batch) == 1 and batch[0].get("event") == "undo":
        realized = -(_as_float(batch[0].get("realized")) or 0.0)
        reverses, by_occ = _flip_reverses(batch[0])
        undone_batch_id = batch[0].get("undone_batch_id") or batch[0].get("batch_id")
    else:
        realized = -sum(_as_float(item.get("realized")) or 0.0 for item in batch)
        reverses = []
        by_occ = {}
        for item in batch:
            occ = str(item.get("occ") or "").upper()
            amount = _as_float(item.get("realized"))
            if amount is None:
                continue
            if occ:
                by_occ[occ] = by_occ.get(occ, 0.0) - amount
            reverses.append(
                {
                    "event": item.get("event"),
                    "id": item.get("id"),
                    "occ": occ or None,
                    "realized": amount,
                }
            )
        undone_batch_id = str(batch[0].get("batch_id") or "") or None
    account.positions = positions_from_book(previous)
    store.save(account)
    sync_book(account, store.home)
    append_undo_event(
        account,
        realized=realized,
        by_occ=by_occ,
        reverses=reverses,
        n_events=len(batch),
        undone_batch_id=undone_batch_id,
        batch_id=new_batch_id(),
        home=store.home,
    )
    return UndoResult(
        n_events=len(batch),
        realized=realized,
        undone_batch_id=undone_batch_id,
        labels=[_label(item) for item in batch],
    )


def _flip_reverses(
    event: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, float]]:
    reverses: list[dict[str, Any]] = []
    for item in event.get("reverses") or []:
        if not isinstance(item, dict):
            continue
        amount = _as_float(item.get("realized"))
        if amount is None:
            continue
        flipped = dict(item)
        flipped["realized"] = -amount
        reverses.append(flipped)
    by_occ: dict[str, float] = {}
    extra = event.get("by_occ")
    if isinstance(extra, dict):
        for occ, amount in extra.items():
            try:
                by_occ[str(occ).upper()] = -float(amount)
            except (TypeError, ValueError):
                continue
    return reverses, by_occ


def _label(event: dict[str, Any]) -> str:
    kind = str(event.get("event") or "?")
    occ = str(event.get("occ") or "")
    if kind in {"add", "merge"}:
        qty = event.get("qty_added")
        return f"{kind} {occ} x{qty:g}" if qty is not None else f"{kind} {occ}"
    if kind == "sell":
        qty = event.get("qty_sold")
        return f"sell {occ} x{qty:g}" if qty is not None else f"sell {occ}"
    if kind == "delete":
        return "delete"
    if kind == "undo":
        return "undo"
    return kind


def _position_from_brief(row: dict[str, Any], now: datetime) -> Position | None:
    occ = str(row.get("occ") or "").upper()
    if not occ:
        return None
    try:
        parts = parse_occ(occ)
    except OccError:
        parts = None
    expiry = _as_date(row.get("expiry")) or (parts.expiry if parts else None)
    strike = _as_float(row.get("strike"))
    if strike is None and parts is not None:
        strike = parts.strike
    option_type = row.get("option_type") or (parts.option_type if parts else None)
    if expiry is None or strike is None or option_type not in {"call", "put"}:
        return None
    iv = _as_float(row.get("iv"))
    if iv is None or iv <= 0:
        iv = 0.01
    opened = parse_ts(row.get("opened_at"))
    return Position(
        id=str(row.get("id") or uuid4().hex[:10]),
        occ_symbol=occ,
        underlying=str(row.get("underlying") or (parts.underlying if parts else occ[:4])),
        expiry=expiry,
        strike=strike,
        option_type=option_type,
        qty=_as_float(row.get("qty"), 0.0) or 0.0,
        side="short" if row.get("side") == "short" else "long",
        iv_frozen=iv,
        iv_as_of=opened or now,
        iv_source=row.get("iv_source"),
        entry_premium=_as_float(row.get("cost")),
        opened_at=opened,
    )


def _gap_seconds(first: dict[str, Any], second: dict[str, Any]) -> float:
    left = parse_ts(first.get("ts"))
    right = parse_ts(second.get("ts"))
    if left is None or right is None:
        return 0.0
    return abs((right - left).total_seconds())


def _as_float(value: Any, default: float | None = None) -> float | None:
    if value is None or value == "":
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _as_date(value: Any) -> date | None:
    if value is None or value == "":
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None

