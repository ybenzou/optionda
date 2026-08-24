from __future__ import annotations

import re
from datetime import date, datetime
from zoneinfo import ZoneInfo

_ET = ZoneInfo("America/New_York")
_DATE_RE = re.compile(
    r"^(?:asof\s+)?"
    r"(?:"
    r"(?P<iso>\d{4}-\d{2}-\d{2})"
    r"|"
    r"(?P<m>\d{1,2})/(?P<d>\d{1,2})(?:/(?P<y>\d{2,4}))?"
    r")"
    r":?\s*",
    re.IGNORECASE,
)


def parse_asof_date(text: str, *, today: date | None = None) -> date | None:
    raw = (text or "").strip().rstrip(":").strip()
    if not raw:
        return None
    match = _DATE_RE.fullmatch(raw)
    if match is None:
        return None
    return _date_from_match(match, today=today or date.today())


def split_asof_prefix(
    line: str,
    *,
    today: date | None = None,
) -> tuple[date | None, str]:
    raw = (line or "").strip()
    if not raw:
        return None, ""
    match = _DATE_RE.match(raw)
    if match is None:
        return None, raw
    day = _date_from_match(match, today=today or date.today())
    rest = raw[match.end() :].strip()
    if rest.startswith(":"):
        rest = rest[1:].strip()
    return day, rest


def session_close_at(day: date) -> datetime:
    return datetime(day.year, day.month, day.day, 16, 0, tzinfo=_ET)


def apply_asof(
    lines: list[str],
    *,
    default: date | None = None,
    today: date | None = None,
) -> list[tuple[str, datetime | None]]:
    """Attach a session-close timestamp to each trade line.

    A leading ``8/20`` / ``2026-08-20`` (optional ``asof`` / trailing ``:``)
    sets the date for that line and following lines. A date-only segment
    only switches the date.
    """
    current = today or date.today()
    asof = session_close_at(default) if default is not None else None
    dated: list[tuple[str, datetime | None]] = []
    for raw in lines:
        segments = [part.strip() for part in raw.replace("\n", ";").split(";")]
        for line in segments:
            if not line or line.startswith("#"):
                continue
            day, rest = split_asof_prefix(line, today=current)
            if day is not None:
                asof = session_close_at(day)
            if not rest:
                continue
            dated.append((rest, asof))
    return dated


def _date_from_match(match: re.Match[str], *, today: date) -> date | None:
    iso = match.group("iso")
    if iso:
        try:
            return date.fromisoformat(iso)
        except ValueError:
            return None
    month = match.group("m")
    day = match.group("d")
    if month is None or day is None:
        return None
    year_raw = match.group("y")
    if year_raw:
        year = int(year_raw)
        if year < 100:
            year += 2000
    else:
        year = today.year
    try:
        parsed = date(year, int(month), int(day))
    except ValueError:
        return None
    if year_raw is None and parsed > today:
        try:
            parsed = date(today.year - 1, int(month), int(day))
        except ValueError:
            return None
    return parsed
