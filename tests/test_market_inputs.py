from datetime import date, datetime, timezone

import pytest

from optionda.config import dividend_for_symbol, rate_for_days
from optionda.models import AppConfig
from optionda.pricing.bs import years_to_expiry
from optionda.pricing.surface import (
    ExpirySmile,
    IvSurface,
    is_surface_fresh,
    last_completed_session_date,
)


def test_rate_curve_interpolates_by_term() -> None:
    config = AppConfig(rate_curve=[(30, 0.04), (90, 0.05)])
    assert rate_for_days(config, 60) == pytest.approx(0.045)


def test_dividend_yield_is_symbol_specific() -> None:
    config = AppConfig(q=0.01, dividend_yields={"XOM": 0.035})
    assert dividend_for_symbol(config, "xom") == pytest.approx(0.035)
    assert dividend_for_symbol(config, "AAPL") == pytest.approx(0.01)


def test_expiry_time_uses_new_york_daylight_saving() -> None:
    # 16:00 ET on this winter expiry is 21:00 UTC, not a fixed 20:00 UTC.
    now = datetime(2026, 1, 16, 20, 0, tzinfo=timezone.utc)
    assert years_to_expiry(date(2026, 1, 16), now) == pytest.approx(1 / (365 * 24))


def test_friday_surface_remains_usable_over_weekend() -> None:
    surface = IvSurface(
        underlying="AAPL",
        spot=100,
        as_of=datetime(2026, 8, 7, 20, tzinfo=timezone.utc),  # Friday
        source="test",
        smiles=[ExpirySmile(expiry=date(2026, 9, 18), nodes=[])],
        quality={},
    )
    assert is_surface_fresh(surface, datetime(2026, 8, 9, 20, tzinfo=timezone.utc))


def _surface(as_of: datetime) -> IvSurface:
    return IvSurface(
        underlying="AAPL",
        spot=100,
        as_of=as_of,
        source="test",
        smiles=[ExpirySmile(expiry=date(2026, 9, 18), nodes=[])],
        quality={},
    )


def test_last_completed_session_is_prior_weekday_before_rth_close() -> None:
    # Friday 02:40 ET (14:40 UTC+8) — Thursday has closed, Friday has not.
    friday_pre = datetime(2026, 8, 14, 6, 40, tzinfo=timezone.utc)
    assert last_completed_session_date(friday_pre) == date(2026, 8, 13)

    # Friday 16:00 ET — Friday session is complete.
    friday_close = datetime(2026, 8, 14, 20, 0, tzinfo=timezone.utc)
    assert last_completed_session_date(friday_close) == date(2026, 8, 14)

    # Saturday and Sunday stay on Friday.
    saturday = datetime(2026, 8, 15, 18, 0, tzinfo=timezone.utc)
    sunday = datetime(2026, 8, 16, 18, 0, tzinfo=timezone.utc)
    assert last_completed_session_date(saturday) == date(2026, 8, 14)
    assert last_completed_session_date(sunday) == date(2026, 8, 14)

    # Monday 10:00 ET is still Friday's close.
    monday_open = datetime(2026, 8, 17, 14, 0, tzinfo=timezone.utc)
    assert last_completed_session_date(monday_open) == date(2026, 8, 14)


def test_surface_freshness_follows_last_completed_session() -> None:
    wed_close = _surface(datetime(2026, 8, 12, 20, tzinfo=timezone.utc))
    thu_close = _surface(datetime(2026, 8, 13, 20, tzinfo=timezone.utc))
    friday_pre = datetime(2026, 8, 14, 6, 40, tzinfo=timezone.utc)
    friday_close = datetime(2026, 8, 14, 20, 0, tzinfo=timezone.utc)

    assert is_surface_fresh(thu_close, friday_pre)
    assert not is_surface_fresh(wed_close, friday_pre)
    assert not is_surface_fresh(thu_close, friday_close)


def test_preopen_same_calendar_day_is_not_the_close() -> None:
    # 8/13 10:00 ET — after midnight, before the 16:00 close.
    thu_preopen = _surface(datetime(2026, 8, 13, 14, 0, tzinfo=timezone.utc))
    # Beijing 8/14 15:19 == 8/14 03:19 ET; last completed close is 8/13 16:00 ET.
    beijing_afternoon = datetime(2026, 8, 14, 7, 19, tzinfo=timezone.utc)
    assert last_completed_session_date(beijing_afternoon) == date(2026, 8, 13)
    assert not is_surface_fresh(thu_preopen, beijing_afternoon)
    assert is_surface_fresh(
        _surface(datetime(2026, 8, 13, 20, 0, tzinfo=timezone.utc)),
        beijing_afternoon,
    )


def test_close_prints_just_before_1600_count_as_the_close() -> None:
    # Alpaca last RTH prints are typically 15:59:59 ET, not 16:00:00.
    thu_last_print = _surface(
        datetime(2026, 8, 13, 19, 59, 59, 536675, tzinfo=timezone.utc)
    )
    beijing_afternoon = datetime(2026, 8, 14, 7, 49, tzinfo=timezone.utc)
    assert last_completed_session_date(beijing_afternoon) == date(2026, 8, 13)
    assert is_surface_fresh(thu_last_print, beijing_afternoon)
