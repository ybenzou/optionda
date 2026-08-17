from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from optionda.market.session import (
    MarketSession,
    SessionError,
    parse_calendar_days,
    parse_clock,
    resolve_completed_session,
)

_ET = ZoneInfo("America/New_York")


def _session(day: date, close_hour: int = 16, close_minute: int = 0) -> dict:
    return {
        "date": day.isoformat(),
        "open": "09:30",
        "close": f"{close_hour:02d}:{close_minute:02d}",
    }


AUG = [
    _session(date(2026, 8, 10)),  # Mon
    _session(date(2026, 8, 11)),
    _session(date(2026, 8, 12)),
    _session(date(2026, 8, 13)),
    _session(date(2026, 8, 14)),  # Fri
    _session(date(2026, 8, 17)),  # Mon
]


def _clock(ts: datetime, *, next_close: datetime | None = None) -> dict:
    return {
        "timestamp": ts.isoformat(),
        "is_open": False,
        "next_open": "2026-08-17T13:30:00+00:00",
        "next_close": (
            next_close.isoformat()
            if next_close is not None
            else "2026-08-17T20:00:00+00:00"
        ),
    }


def test_weekday_before_close_uses_prior_session() -> None:
    # Friday 02:40 ET — Thursday has closed, Friday has not.
    clock = parse_clock(_clock(datetime(2026, 8, 14, 6, 40, tzinfo=timezone.utc)))
    days = parse_calendar_days(AUG)
    state = resolve_completed_session(clock, days)
    assert state.completed.session_date == date(2026, 8, 13)
    assert state.completed.close_at == datetime(2026, 8, 13, 16, 0, tzinfo=_ET)
    assert state.next_close_at == datetime(2026, 8, 14, 16, 0, tzinfo=_ET)


def test_weekday_after_close_uses_today() -> None:
    clock = parse_clock(_clock(datetime(2026, 8, 14, 20, 5, tzinfo=timezone.utc)))
    state = resolve_completed_session(clock, parse_calendar_days(AUG))
    assert state.completed.session_date == date(2026, 8, 14)


def test_weekend_stays_on_friday() -> None:
    saturday = parse_clock(_clock(datetime(2026, 8, 15, 18, 0, tzinfo=timezone.utc)))
    sunday = parse_clock(_clock(datetime(2026, 8, 16, 18, 0, tzinfo=timezone.utc)))
    days = parse_calendar_days(AUG)
    assert resolve_completed_session(saturday, days).completed.session_date == date(
        2026, 8, 14
    )
    assert resolve_completed_session(sunday, days).completed.session_date == date(
        2026, 8, 14
    )


def test_beijing_monday_morning_is_still_friday() -> None:
    # Beijing Monday 10:36 == Sunday 22:36 ET.
    clock = parse_clock(_clock(datetime(2026, 8, 17, 2, 36, tzinfo=timezone.utc)))
    state = resolve_completed_session(clock, parse_calendar_days(AUG))
    assert state.completed.session_date == date(2026, 8, 14)
    assert state.next_close_at == datetime(2026, 8, 17, 16, 0, tzinfo=_ET)


def test_holiday_falls_back_to_prior_trading_day() -> None:
    # Labor Day 2026-09-07 is absent from the calendar.
    days = parse_calendar_days(
        [
            _session(date(2026, 9, 3)),
            _session(date(2026, 9, 4)),
            _session(date(2026, 9, 8)),
        ]
    )
    monday_evening = parse_clock(
        _clock(datetime(2026, 9, 7, 21, 0, tzinfo=timezone.utc))
    )
    state = resolve_completed_session(monday_evening, days)
    assert state.completed.session_date == date(2026, 9, 4)
    assert state.next_close_at == datetime(2026, 9, 8, 16, 0, tzinfo=_ET)


def test_early_close_becomes_completed_at_calendar_close() -> None:
    days = parse_calendar_days(
        [
            _session(date(2026, 11, 27)),
            _session(date(2026, 11, 28), close_hour=13),
            _session(date(2026, 11, 30)),
        ]
    )
    after_early = parse_clock(
        _clock(datetime(2026, 11, 28, 18, 5, tzinfo=timezone.utc))
    )
    state = resolve_completed_session(after_early, days)
    assert state.completed.session_date == date(2026, 11, 28)
    assert state.completed.close_at == datetime(2026, 11, 28, 13, 0, tzinfo=_ET)


def test_dst_winter_close_uses_eastern_offset() -> None:
    days = parse_calendar_days([_session(date(2026, 1, 16))])
    clock = parse_clock(_clock(datetime(2026, 1, 16, 21, 5, tzinfo=timezone.utc)))
    state = resolve_completed_session(clock, days)
    assert state.completed.close_at == datetime(2026, 1, 16, 16, 0, tzinfo=_ET)
    assert state.completed.close_at.utcoffset().total_seconds() == -5 * 3600


def test_empty_calendar_is_an_error() -> None:
    clock = parse_clock(_clock(datetime(2026, 8, 14, 20, 0, tzinfo=timezone.utc)))
    with pytest.raises(SessionError, match="calendar"):
        resolve_completed_session(clock, [])


def test_clock_must_have_timestamp() -> None:
    with pytest.raises(SessionError, match="clock"):
        parse_clock({})


def test_session_reference_round_trip(tmp_path) -> None:
    from optionda.market.session import (
        SessionReference,
        load_session_reference,
        save_session_reference,
    )

    ref = SessionReference(
        underlying="aapl",
        session_date=date(2026, 8, 14),
        session_close_at=datetime(2026, 8, 14, 16, 0, tzinfo=_ET),
        close_spot=305.33,
        source="alpaca/sip/1Day",
        updated_at=datetime(2026, 8, 14, 20, 5, tzinfo=timezone.utc),
    )
    path = save_session_reference(ref, tmp_path)
    loaded = load_session_reference("AAPL", tmp_path)
    assert path == tmp_path / "session_refs" / "AAPL.json"
    assert loaded is not None
    assert loaded.underlying == "AAPL"
    assert loaded.close_spot == 305.33
    assert loaded.session_date == date(2026, 8, 14)


def test_close_premiums_round_trip_and_merge(tmp_path) -> None:
    from optionda.market.session import (
        ClosePremiums,
        load_close_premiums,
        merge_close_premiums,
        save_close_premiums,
    )

    first = ClosePremiums(
        underlying="skhy",
        session_date=date(2026, 8, 14),
        premiums={"SKHY261218C00020000": 10.80},
        source="alpaca/chain",
        updated_at=datetime(2026, 8, 14, 20, 5, tzinfo=timezone.utc),
    )
    path = save_close_premiums(first, tmp_path)
    loaded = load_close_premiums("SKHY", tmp_path)
    assert path == tmp_path / "close_mids" / "SKHY.json"
    assert loaded is not None
    assert loaded.premiums["SKHY261218C00020000"] == pytest.approx(10.80)

    merged = merge_close_premiums(
        loaded,
        ClosePremiums(
            underlying="SKHY",
            session_date=date(2026, 8, 14),
            premiums={"SKHY261218C00025000": 8.10},
            source="alpaca/chain",
            updated_at=datetime(2026, 8, 14, 20, 6, tzinfo=timezone.utc),
        ),
    )
    assert merged.premiums["SKHY261218C00020000"] == pytest.approx(10.80)
    assert merged.premiums["SKHY261218C00025000"] == pytest.approx(8.10)

    replaced = merge_close_premiums(
        loaded,
        ClosePremiums(
            underlying="SKHY",
            session_date=date(2026, 8, 17),
            premiums={"SKHY261218C00020000": 12.00},
            source="alpaca/chain",
            updated_at=datetime(2026, 8, 17, 20, 5, tzinfo=timezone.utc),
        ),
    )
    assert replaced.session_date == date(2026, 8, 17)
    assert replaced.premiums == {"SKHY261218C00020000": 12.00}


def test_market_session_identity() -> None:
    session = MarketSession(
        session_date=date(2026, 8, 14),
        open_at=datetime(2026, 8, 14, 9, 30, tzinfo=_ET),
        close_at=datetime(2026, 8, 14, 16, 0, tzinfo=_ET),
    )
    assert session.session_date.isoformat() == "2026-08-14"
