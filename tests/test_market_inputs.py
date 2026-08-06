from datetime import date, datetime, timezone

import pytest

from optionda.config import dividend_for_symbol, rate_for_days
from optionda.models import AppConfig
from optionda.pricing.bs import years_to_expiry
from optionda.pricing.surface import ExpirySmile, IvSurface, is_surface_fresh


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
