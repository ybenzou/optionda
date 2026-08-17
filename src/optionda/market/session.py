from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from optionda.paths import ensure_home

_ET = ZoneInfo("America/New_York")
SESSION_REF_SCHEMA = 1
CLOSE_GRACE = timedelta(minutes=2)
CLOSE_QUOTE_WINDOW = timedelta(minutes=30)
MAX_NODE_QUOTE_SKEW = timedelta(minutes=30)
RETRY_BACKOFF = (
    timedelta(minutes=2),
    timedelta(minutes=5),
    timedelta(minutes=15),
)
CALENDAR_LOOKBACK_DAYS = 7
CALENDAR_LOOKAHEAD_DAYS = 7


class SessionError(RuntimeError):
    """Clock or calendar is unavailable; do not guess a weekday close."""


@dataclass(frozen=True)
class MarketSession:
    session_date: date
    open_at: datetime
    close_at: datetime


@dataclass(frozen=True)
class CompletedSessionState:
    completed: MarketSession
    next_close_at: datetime | None
    source_timestamp: datetime


@dataclass(frozen=True)
class MarketClock:
    timestamp: datetime
    is_open: bool
    next_open: datetime | None
    next_close: datetime | None


@dataclass(frozen=True)
class DailyClose:
    symbol: str
    session_date: date
    close: float
    source: str
    as_of: datetime | None = None


@dataclass(frozen=True)
class SessionReference:
    underlying: str
    session_date: date
    session_close_at: datetime
    close_spot: float
    source: str
    updated_at: datetime
    schema_version: int = SESSION_REF_SCHEMA


@dataclass
class SessionSyncResult:
    completed_session: MarketSession | None = None
    next_close_at: datetime | None = None
    references_saved: dict[str, SessionReference] = field(default_factory=dict)
    surfaces_saved: dict[str, Any] = field(default_factory=dict)
    pending_surfaces: dict[str, str] = field(default_factory=dict)
    pending_closes: dict[str, str] = field(default_factory=dict)
    errors: dict[str, str] = field(default_factory=dict)
    next_retry_at: datetime | None = None
    unavailable: str | None = None


def _utc(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _parse_ts(value: str) -> datetime:
    return _utc(datetime.fromisoformat(value.replace("Z", "+00:00")))


def parse_clock(payload: dict[str, Any]) -> MarketClock:
    raw = payload.get("timestamp")
    if not raw:
        raise SessionError("clock has no timestamp")
    next_open = payload.get("next_open")
    next_close = payload.get("next_close")
    return MarketClock(
        timestamp=_parse_ts(str(raw)),
        is_open=bool(payload.get("is_open")),
        next_open=_parse_ts(str(next_open)) if next_open else None,
        next_close=_parse_ts(str(next_close)) if next_close else None,
    )


def parse_calendar_days(rows: list[dict[str, Any]]) -> list[MarketSession]:
    sessions: list[MarketSession] = []
    for row in rows:
        raw_date = row.get("date")
        raw_open = row.get("open") or "09:30"
        raw_close = row.get("close") or "16:00"
        if not raw_date:
            continue
        day = date.fromisoformat(str(raw_date)[:10])
        open_h, open_m = _hhmm(str(raw_open))
        close_h, close_m = _hhmm(str(raw_close))
        sessions.append(
            MarketSession(
                session_date=day,
                open_at=datetime(day.year, day.month, day.day, open_h, open_m, tzinfo=_ET),
                close_at=datetime(
                    day.year, day.month, day.day, close_h, close_m, tzinfo=_ET
                ),
            )
        )
    sessions.sort(key=lambda item: item.close_at)
    return sessions


def _hhmm(value: str) -> tuple[int, int]:
    cleaned = value.strip().replace(":", "")
    if len(cleaned) == 4 and cleaned.isdigit():
        return int(cleaned[:2]), int(cleaned[2:])
    parts = value.strip().split(":")
    return int(parts[0]), int(parts[1]) if len(parts) > 1 else 0


def resolve_completed_session(
    clock: MarketClock,
    sessions: list[MarketSession],
) -> CompletedSessionState:
    if not sessions:
        raise SessionError("calendar returned no trading sessions")
    now = _utc(clock.timestamp)
    completed = [item for item in sessions if _utc(item.close_at) <= now]
    if not completed:
        raise SessionError("calendar has no completed session before clock timestamp")
    upcoming = [item for item in sessions if _utc(item.close_at) > now]
    return CompletedSessionState(
        completed=completed[-1],
        next_close_at=upcoming[0].close_at if upcoming else clock.next_close,
        source_timestamp=now,
    )


def quote_in_close_window(quote_time: datetime, session: MarketSession) -> bool:
    instant = _utc(quote_time)
    close_at = _utc(session.close_at)
    return close_at - CLOSE_QUOTE_WINDOW <= instant <= close_at + timedelta(minutes=1)


def session_due(
    now: datetime,
    *,
    next_close_at: datetime | None,
    next_retry_at: datetime | None,
    grace: timedelta = CLOSE_GRACE,
) -> bool:
    current = _utc(now)
    if next_retry_at is not None and current >= _utc(next_retry_at):
        return True
    if next_close_at is not None and current >= _utc(next_close_at) + grace:
        return True
    return False


def next_retry_at(attempt: int, now: datetime) -> datetime:
    index = min(max(attempt, 1), len(RETRY_BACKOFF)) - 1
    return _utc(now) + RETRY_BACKOFF[index]


def session_refs_dir(home: Path | None = None) -> Path:
    root = ensure_home(home)
    path = root / "session_refs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def session_ref_path(underlying: str, home: Path | None = None) -> Path:
    return session_refs_dir(home) / f"{underlying.strip().upper()}.json"


def save_session_reference(reference: SessionReference, home: Path | None = None) -> Path:
    path = session_ref_path(reference.underlying, home)
    payload = {
        "schema_version": reference.schema_version,
        "underlying": reference.underlying,
        "session_date": reference.session_date.isoformat(),
        "session_close_at": reference.session_close_at.isoformat(),
        "close_spot": reference.close_spot,
        "source": reference.source,
        "updated_at": reference.updated_at.isoformat(),
    }
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)
    return path


def load_session_reference(
    underlying: str, home: Path | None = None
) -> SessionReference | None:
    path = session_ref_path(underlying, home)
    if not path.exists():
        return None
    raw = json.loads(path.read_text(encoding="utf-8"))
    return SessionReference(
        underlying=str(raw["underlying"]).upper(),
        session_date=date.fromisoformat(str(raw["session_date"])),
        session_close_at=_parse_ts(str(raw["session_close_at"])),
        close_spot=float(raw["close_spot"]),
        source=str(raw["source"]),
        updated_at=_parse_ts(str(raw["updated_at"])),
        schema_version=int(raw.get("schema_version", SESSION_REF_SCHEMA)),
    )


CLOSE_PREMIUM_SCHEMA = 1


@dataclass(frozen=True)
class ClosePremiums:
    underlying: str
    session_date: date
    premiums: dict[str, float]
    source: str
    updated_at: datetime
    schema_version: int = CLOSE_PREMIUM_SCHEMA


def close_mids_dir(home: Path | None = None) -> Path:
    root = ensure_home(home)
    path = root / "close_mids"
    path.mkdir(parents=True, exist_ok=True)
    return path


def close_mids_path(underlying: str, home: Path | None = None) -> Path:
    return close_mids_dir(home) / f"{underlying.strip().upper()}.json"


def save_close_premiums(book: ClosePremiums, home: Path | None = None) -> Path:
    path = close_mids_path(book.underlying, home)
    payload = {
        "schema_version": book.schema_version,
        "underlying": book.underlying,
        "session_date": book.session_date.isoformat(),
        "premiums": {occ: float(mid) for occ, mid in sorted(book.premiums.items())},
        "source": book.source,
        "updated_at": book.updated_at.isoformat(),
    }
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)
    return path


def load_close_premiums(
    underlying: str, home: Path | None = None
) -> ClosePremiums | None:
    path = close_mids_path(underlying, home)
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    if not isinstance(raw, dict) or not raw.get("session_date"):
        return None
    premiums: dict[str, float] = {}
    for occ, mid in (raw.get("premiums") or {}).items():
        try:
            value = float(mid)
        except (TypeError, ValueError):
            continue
        if value > 0:
            premiums[str(occ).strip().upper()] = value
    return ClosePremiums(
        underlying=str(raw.get("underlying", underlying)).upper(),
        session_date=date.fromisoformat(str(raw["session_date"])),
        premiums=premiums,
        source=str(raw.get("source") or "unknown"),
        updated_at=_parse_ts(str(raw.get("updated_at") or datetime.now(timezone.utc))),
        schema_version=int(raw.get("schema_version", CLOSE_PREMIUM_SCHEMA)),
    )


def merge_close_premiums(
    existing: ClosePremiums | None,
    incoming: ClosePremiums,
) -> ClosePremiums:
    if existing is None or existing.session_date != incoming.session_date:
        return incoming
    merged = dict(existing.premiums)
    merged.update(incoming.premiums)
    return ClosePremiums(
        underlying=incoming.underlying,
        session_date=incoming.session_date,
        premiums=merged,
        source=incoming.source,
        updated_at=incoming.updated_at,
        schema_version=incoming.schema_version,
    )


def pending_state_path(home: Path | None = None) -> Path:
    return ensure_home(home) / "session_sync.json"


def load_pending_state(home: Path | None = None) -> dict[str, Any]:
    path = pending_state_path(home)
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return raw if isinstance(raw, dict) else {}


def save_pending_state(state: dict[str, Any], home: Path | None = None) -> Path:
    path = pending_state_path(home)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)
    return path
