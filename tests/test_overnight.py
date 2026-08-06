from datetime import date, datetime, timezone

import pytest

from optionda.models import Position
from optionda.pricing.surface import (
    ExpirySmile,
    IvSurface,
    SurfaceNode,
    estimate_overnight_iv,
    sticky_strike_iv,
)


def _surface() -> IvSurface:
    return IvSurface(
        underlying="SPCX",
        spot=100.0,
        as_of=datetime(2026, 8, 4, 20, tzinfo=timezone.utc),
        source="test",
        smiles=[
            ExpirySmile(
                expiry=date(2026, 9, 18),
                nodes=[
                    SurfaceNode(90.0, -0.15, 0.70, "put"),
                    SurfaceNode(100.0, -0.35, 0.90, "put"),
                    SurfaceNode(110.0, -0.60, 1.10, "put"),
                ],
            )
        ],
        quality={"accepted": 3, "rejected": 0},
    )


def _position() -> Position:
    return Position(
        occ_symbol="SPCX260918P00100000",
        underlying="SPCX",
        expiry=date(2026, 9, 18),
        strike=100.0,
        option_type="put",
        iv_frozen=0.90,
        iv_as_of=datetime(2026, 8, 4, 20, tzinfo=timezone.utc),
    )


def test_sticky_strike_reads_same_strike_iv() -> None:
    iv = sticky_strike_iv(_surface(), _position())
    assert iv == pytest.approx(0.90)


def test_hybrid_iv_is_bounded_by_sticky_scenarios() -> None:
    estimate = estimate_overnight_iv(
        _surface(),
        _position(),
        spot=105.0,
        years=0.12,
        rate=0.04,
        dividend=0.0,
        sticky_delta_weight=0.5,
    )
    assert estimate.sticky_strike == pytest.approx(0.90)
    assert estimate.sticky_delta is not None
    assert min(estimate.sticky_strike, estimate.sticky_delta) <= estimate.base <= max(
        estimate.sticky_strike, estimate.sticky_delta
    )
    assert estimate.low <= estimate.base <= estimate.high
