"""NYSE sessions already fetched from Alpaca, stored so the desk can skip holidays."""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

from optionda.paths import ensure_home


def calendar_path(home: Path | None = None) -> Path:
    folder = ensure_home(home) / "closes"
    folder.mkdir(parents=True, exist_ok=True)
    return folder / "calendar.json"


def _parse_day(value: object) -> date | None:
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        return None


def _merge_spans(spans: list[tuple[date, date]]) -> list[tuple[date, date]]:
    ordered = sorted((start, end) if start <= end else (end, start) for start, end in spans)
    if not ordered:
        return []
    merged: list[tuple[date, date]] = [ordered[0]]
    for start, end in ordered[1:]:
        prev_start, prev_end = merged[-1]
        if start <= prev_end + timedelta(days=1):
            merged[-1] = (prev_start, max(prev_end, end))
        else:
            merged.append((start, end))
    return merged


def load_calendar(home: Path | None = None) -> tuple[set[date], list[tuple[date, date]]]:
    """Open sessions, and the date ranges those sessions were fetched for.

    A weekday inside a fetched range that is not an open session is a holiday.
    A weekday outside every fetched range is still drawn. An older file that
    only stored the open days covers just from its first day to its last.
    """
    path = ensure_home(home) / "closes" / "calendar.json"
    if not path.exists():
        return set(), []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return set(), []
    if not isinstance(payload, dict):
        return set(), []
    days: set[date] = set()
    raw = payload.get("days")
    if isinstance(raw, list):
        for item in raw:
            parsed = _parse_day(item)
            if parsed is not None:
                days.add(parsed)
    spans: list[tuple[date, date]] = []
    raw_spans = payload.get("spans")
    if isinstance(raw_spans, list):
        for item in raw_spans:
            if not isinstance(item, (list, tuple)) or len(item) != 2:
                continue
            start = _parse_day(item[0])
            end = _parse_day(item[1])
            if start is not None and end is not None:
                spans.append((start, end))
    if not spans and days:
        spans = [(min(days), max(days))]
    return days, _merge_spans(spans)


def load_calendar_days(home: Path | None = None) -> set[date]:
    days, _spans = load_calendar(home)
    return days


def known_holiday(
    day: date,
    sessions: set[date] | None,
    spans: list[tuple[date, date]] | None = None,
) -> bool:
    """True when this weekday was inside a fetched calendar and was closed."""
    if day.weekday() >= 5 or not sessions:
        return False
    covered = spans
    if covered is None:
        covered = [(min(sessions), max(sessions))]
    if not any(start <= day <= end for start, end in covered):
        return False
    return day not in sessions


def remember_calendar_days(
    home: Path | None,
    days,
    *,
    start: date | None = None,
    end: date | None = None,
) -> None:
    fresh = []
    for item in days:
        if isinstance(item, date):
            fresh.append(item)
            continue
        try:
            fresh.append(date.fromisoformat(str(item)))
        except ValueError:
            continue
    if not fresh:
        return
    previous, spans = load_calendar(home)
    if start is not None and end is not None:
        spans = _merge_spans([*spans, (start, end)])
    else:
        spans = _merge_spans([*spans, (min(fresh), max(fresh))])
    merged = previous | set(fresh)
    path = calendar_path(home)
    payload = {
        "days": [day.isoformat() for day in sorted(merged)],
        "spans": [[a.isoformat(), b.isoformat()] for a, b in spans],
    }
    path.write_text(json.dumps(payload), encoding="utf-8")
