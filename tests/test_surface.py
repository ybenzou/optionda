from datetime import date, datetime, timedelta, timezone

import pytest

from optionda.engine import (
    apply_surface_reference_ivs,
    calibrate_surfaces,
    ensure_surfaces,
    mark_account,
)
from optionda.models import Account, Position, SpotQuote
from optionda.pricing.bs import price_option, years_to_expiry
from optionda.pricing.surface import (
    ExpirySmile,
    IvSurface,
    SurfaceNode,
    build_surface,
    load_surface,
    save_surface,
    sticky_delta_iv,
)


def test_build_surface_filters_invalid_and_wide_nodes() -> None:
    as_of = datetime(2026, 8, 4, 20, 0, tzinfo=timezone.utc)
    snapshots = {
        "SPCX260918P00100000": {
            "impliedVolatility": 0.86,
            "greeks": {"delta": -0.25},
            "latestQuote": {"bp": 6.14, "ap": 6.32, "t": "2026-08-04T20:00:00Z"},
        },
        "SPCX260918P00110000": {
            "impliedVolatility": 0.91,
            "greeks": {"delta": -0.40},
            "latestQuote": {"bp": 10.0, "ap": 10.4, "t": "2026-08-04T20:00:00Z"},
        },
        # Bad: zero bid, so it is not a usable calibration quote.
        "SPCX260918P00120000": {
            "impliedVolatility": 1.02,
            "greeks": {"delta": -0.58},
            "latestQuote": {"bp": 0, "ap": 15.0, "t": "2026-08-04T20:00:00Z"},
        },
        # Bad: 100% spread exceeds the permitted width.
        "SPCX260918P00130000": {
            "impliedVolatility": 1.13,
            "greeks": {"delta": -0.72},
            "latestQuote": {"bp": 5.0, "ap": 10.0, "t": "2026-08-04T20:00:00Z"},
        },
    }

    surface = build_surface(
        "SPCX",
        spot=116.0,
        snapshots=snapshots,
        as_of=as_of,
        source="alpaca/indicative",
    )

    assert surface.underlying == "SPCX"
    assert len(surface.smiles) == 1
    assert [node.strike for node in surface.smiles[0].nodes] == [110.0, 100.0]
    assert surface.quality["accepted"] == 2
    assert surface.quality["rejected"] == 2


def test_surface_keeps_call_and_put_wings_separate() -> None:
    as_of = datetime(2026, 8, 4, 20, 0, tzinfo=timezone.utc)
    surface = build_surface(
        "SPCX",
        spot=100.0,
        snapshots={
            "SPCX260918P00100000": {
                "impliedVolatility": 0.80,
                "greeks": {"delta": -0.05},
                "latestQuote": {
                    "bp": 4.9,
                    "ap": 5.1,
                    "t": "2026-08-04T20:00:00Z",
                },
            },
            "SPCX260918P00095000": {
                "impliedVolatility": 0.90,
                "greeks": {"delta": -0.20},
                "latestQuote": {
                    "bp": 2.9,
                    "ap": 3.1,
                    "t": "2026-08-04T20:00:00Z",
                },
            },
            "SPCX260918C00100000": {
                "impliedVolatility": 0.40,
                "greeks": {"delta": 0.05},
                "latestQuote": {
                    "bp": 4.9,
                    "ap": 5.1,
                    "t": "2026-08-04T20:00:00Z",
                },
            },
            "SPCX260918C00105000": {
                "impliedVolatility": 0.50,
                "greeks": {"delta": 0.20},
                "latestQuote": {
                    "bp": 2.9,
                    "ap": 3.1,
                    "t": "2026-08-04T20:00:00Z",
                },
            },
        },
        as_of=as_of,
        source="alpaca/indicative",
    )
    assert {node.option_type for node in surface.smiles[0].nodes} == {"call", "put"}
    # Use an explicit merged node list to ensure the lookup never crosses from
    # the put wing into the call wing near delta zero.
    surface = IvSurface(
        underlying="SPCX",
        spot=100.0,
        as_of=as_of,
        source="test",
        smiles=[
            ExpirySmile(
                expiry=date(2026, 9, 18),
                nodes=[
                    SurfaceNode(95.0, -0.60, 0.90, "put"),
                    SurfaceNode(100.0, -0.10, 0.80, "put"),
                    SurfaceNode(100.0, 0.10, 0.40, "call"),
                    SurfaceNode(105.0, 0.60, 0.50, "call"),
                ],
            )
        ],
        quality={"accepted": 4, "rejected": 0},
    )
    put = Position(
        occ_symbol="SPCX260918P00100000",
        underlying="SPCX",
        expiry=date(2026, 9, 18),
        strike=100.0,
        option_type="put",
        iv_frozen=0.8,
        iv_as_of=as_of,
    )
    # A put delta near zero must use the put wing, not interpolate to call IV.
    iv = sticky_delta_iv(
        surface,
        put,
        spot=115.0,
        years=0.12,
        rate=0.04,
        dividend=0.0,
    )
    assert iv is not None
    assert iv > 0.75


def test_surface_rejects_quote_without_timestamp() -> None:
    with pytest.raises(ValueError, match="no usable surface nodes"):
        build_surface(
            "SPCX",
            spot=100.0,
            snapshots={
                "SPCX260918P00100000": {
                    "impliedVolatility": 0.8,
                    "greeks": {"delta": -0.2},
                    "latestQuote": {"bp": 4.9, "ap": 5.1},
                }
            },
            as_of=datetime(2026, 8, 4, 20, 0, tzinfo=timezone.utc),
            source="test",
        )


def test_surface_round_trips_through_local_store(tmp_path) -> None:
    as_of = datetime(2026, 8, 4, 20, 0, tzinfo=timezone.utc)
    surface = build_surface(
        "SPCX",
        spot=116.0,
        snapshots={
            "SPCX260918P00100000": {
                "impliedVolatility": 0.86,
                "greeks": {"delta": -0.25},
                "latestQuote": {
                    "bp": 6.14,
                    "ap": 6.32,
                    "t": "2026-08-04T20:00:00Z",
                },
            }
        },
        as_of=as_of,
        source="alpaca/indicative",
    )

    path = save_surface(surface, tmp_path)
    loaded = load_surface("SPCX", tmp_path)

    assert path == tmp_path / "surfaces" / "SPCX.json"
    assert loaded is not None
    assert loaded.as_of == as_of
    node = loaded.smiles[0].nodes[0]
    assert node.option_type == "put"
    assert node.quote_time == as_of
    assert node.premium == pytest.approx(6.23)
    assert node.vendor_iv == pytest.approx(0.86)


def test_build_surface_rejects_stale_quotes() -> None:
    as_of = datetime(2026, 8, 4, 20, 30, tzinfo=timezone.utc)
    snapshots = {
        "SPCX260918P00100000": {
            "impliedVolatility": 0.86,
            "greeks": {"delta": -0.25},
            "latestQuote": {"bp": 6.14, "ap": 6.32, "t": "2026-08-04T20:00:00Z"},
        }
    }

    with pytest.raises(ValueError, match="no usable surface nodes"):
        build_surface(
            "SPCX",
            spot=116.0,
            snapshots=snapshots,
            as_of=as_of,
            source="alpaca/indicative",
            max_quote_age=timedelta(minutes=20),
        )


def test_calibrate_surfaces_persists_each_held_underlying(tmp_path) -> None:
    as_of = datetime(2026, 8, 4, 20, 0, tzinfo=timezone.utc)
    position = Position(
        occ_symbol="SPCX260918P00100000",
        underlying="SPCX",
        expiry=date(2026, 9, 18),
        strike=100.0,
        option_type="put",
        iv_frozen=0.86,
        iv_as_of=as_of,
        entry_premium=6.7,
    )

    class Router:
        feed_name = "alpaca"

        def get_spots(self, symbols):
            assert symbols == ["SPCX"]
            return {
                "SPCX": SpotQuote(
                    symbol="SPCX",
                    price=116.0,
                    as_of=as_of,
                    source="alpaca/iex",
                )
            }

        def get_option_chain_snapshots(self, underlying):
            assert underlying == "SPCX"
            return {
                "SPCX260918P00100000": {
                    "impliedVolatility": 0.86,
                    "greeks": {"delta": -0.25},
                    "latestQuote": {
                        "bp": 6.14,
                        "ap": 6.32,
                        "t": "2026-08-04T20:00:00Z",
                    },
                }
            }

    result = calibrate_surfaces(
        Account(name="demo", positions=[position]),
        router=Router(),
        home=tmp_path,
        now=as_of,
    )

    assert list(result.surfaces) == ["SPCX"]
    assert result.errors == {}
    stored = load_surface("SPCX", tmp_path)
    assert stored is not None
    assert stored.source == "alpaca/chain"


def test_ensure_surfaces_skips_fresh_and_calibrates_missing(tmp_path) -> None:
    as_of = datetime(2026, 8, 12, 20, 0, tzinfo=timezone.utc)
    aapl = Position(
        occ_symbol="AAPL261120C00350000",
        underlying="AAPL",
        expiry=date(2026, 11, 20),
        strike=350.0,
        option_type="call",
        iv_frozen=0.25,
        iv_as_of=as_of,
        entry_premium=4.76,
    )
    goog = Position(
        occ_symbol="GOOG261218C00400000",
        underlying="GOOG",
        expiry=date(2026, 12, 18),
        strike=400.0,
        option_type="call",
        iv_frozen=0.33,
        iv_as_of=as_of,
        entry_premium=9.9,
    )
    save_surface(
        IvSurface(
            underlying="AAPL",
            spot=302.0,
            as_of=as_of,
            source="alpaca/chain",
            smiles=[
                ExpirySmile(
                    expiry=date(2026, 11, 20),
                    nodes=[SurfaceNode(strike=350.0, delta=0.16, iv=0.25)],
                )
            ],
            quality={"accepted": 1, "rejected": 0},
        ),
        tmp_path,
    )
    fetched: list[str] = []

    class Router:
        feed_name = "alpaca"

        def get_option_chain_snapshots(self, underlying):
            fetched.append(underlying)
            return {
                "GOOG261218C00400000": {
                    "impliedVolatility": 0.33,
                    "latestQuote": {
                        "bp": 9.8,
                        "ap": 10.0,
                        "t": "2026-08-12T20:00:00Z",
                    },
                }
            }

        def get_spot_at(self, symbol, as_of_t):
            return SpotQuote(
                symbol=symbol,
                price=338.0,
                as_of=as_of_t,
                source="alpaca/sip/historical-trade",
            )

    result = ensure_surfaces(
        Account(name="demo", positions=[aapl, goog]),
        ["AAPL", "GOOG"],
        router=Router(),
        home=tmp_path,
        now=as_of,
    )
    assert fetched == ["GOOG"]
    assert "GOOG" in result.surfaces
    assert "AAPL" not in result.surfaces
    stored = load_surface("GOOG", tmp_path)
    assert stored is not None
    assert stored.spot == pytest.approx(338.0)


def test_calibrate_surface_uses_spot_at_option_quote_time(tmp_path) -> None:
    quote_time = datetime(2026, 8, 11, 19, 59, 59, tzinfo=timezone.utc)
    refresh_time = datetime(2026, 8, 12, 8, 13, 25, tzinfo=timezone.utc)
    position = Position(
        occ_symbol="SKHY261016C00200000",
        underlying="SKHY",
        expiry=date(2026, 10, 16),
        strike=200.0,
        option_type="call",
        iv_frozen=0.70,
        iv_as_of=quote_time,
        entry_premium=6.925,
    )

    class Router:
        feed_name = "alpaca"

        def get_spots(self, symbols):
            # This is the later pre-market price and must not anchor close IV.
            return {
                "SKHY": SpotQuote(
                    symbol="SKHY",
                    price=146.48,
                    as_of=refresh_time,
                    source="alpaca/overnight",
                )
            }

        def get_spot_at(self, symbol, as_of):
            assert symbol == "SKHY"
            assert as_of == quote_time
            return SpotQuote(
                symbol="SKHY",
                price=141.65,
                as_of=quote_time,
                source="alpaca/sip/bar",
            )

        def get_option_chain_snapshots(self, underlying):
            assert underlying == "SKHY"
            return {
                "SKHY261016C00200000": {
                    "impliedVolatility": 0.795,
                    "latestQuote": {
                        "bp": 4.22,
                        "ap": 5.19,
                        "t": "2026-08-11T19:59:59Z",
                    },
                }
            }

    result = calibrate_surfaces(
        Account(name="main", positions=[position]),
        router=Router(),
        home=tmp_path,
        now=refresh_time,
    )

    surface = result.surfaces["SKHY"]
    assert surface.spot == pytest.approx(141.65)
    assert surface.as_of == quote_time
    assert surface.smiles[0].nodes[0].iv == pytest.approx(0.7946, abs=0.001)


def test_calibrate_surfaces_skips_failed_underlying(tmp_path) -> None:
    as_of = datetime(2026, 8, 4, 20, 0, tzinfo=timezone.utc)
    positions = [
        Position(
            occ_symbol="AAPL261120C00350000",
            underlying="AAPL",
            expiry=date(2026, 11, 20),
            strike=350.0,
            option_type="call",
            iv_frozen=0.26,
            iv_as_of=as_of,
            entry_premium=4.95,
        ),
        Position(
            occ_symbol="SPCX260918P00100000",
            underlying="SPCX",
            expiry=date(2026, 9, 18),
            strike=100.0,
            option_type="put",
            iv_frozen=0.86,
            iv_as_of=as_of,
            entry_premium=6.7,
        ),
    ]

    class Router:
        feed_name = "alpaca"

        def get_spots(self, symbols):
            return {
                "AAPL": SpotQuote(symbol="AAPL", price=310.0, as_of=as_of, source="t"),
                "SPCX": SpotQuote(symbol="SPCX", price=116.0, as_of=as_of, source="t"),
            }

        def get_option_chain_snapshots(self, underlying):
            if underlying == "AAPL":
                return {}  # triggers no usable nodes
            return {
                "SPCX260918P00100000": {
                    "impliedVolatility": 0.86,
                    "greeks": {"delta": -0.25},
                    "latestQuote": {
                        "bp": 6.14,
                        "ap": 6.32,
                        "t": "2026-08-04T20:00:00Z",
                    },
                }
            }

    events: list[tuple[str, int, int]] = []
    result = calibrate_surfaces(
        Account(name="demo", positions=positions),
        router=Router(),
        home=tmp_path,
        now=as_of,
        on_progress=lambda label, done, total: events.append((label, done, total)),
    )
    assert "SPCX" in result.surfaces
    assert "AAPL" in result.errors
    assert "no usable surface nodes" in result.errors["AAPL"]
    assert events[0][0] == "spots…"
    assert any("AAPL chain" in label for label, _, _ in events)
    assert events[-1] == ("done", 2, 2)


def test_sticky_delta_iv_iterates_on_current_spot() -> None:
    as_of = datetime(2026, 8, 4, 20, 0, tzinfo=timezone.utc)
    surface = IvSurface(
        underlying="SPCX",
        spot=116.0,
        as_of=as_of,
        source="alpaca/chain",
        quality={"accepted": 3, "rejected": 0},
        smiles=[
            ExpirySmile(
                expiry=date(2026, 9, 18),
                nodes=[
                    SurfaceNode(strike=90.0, delta=-0.70, iv=1.10),
                    SurfaceNode(strike=100.0, delta=-0.45, iv=0.95),
                    SurfaceNode(strike=110.0, delta=-0.20, iv=0.80),
                ],
            )
        ],
    )
    position = Position(
        occ_symbol="SPCX260918P00100000",
        underlying="SPCX",
        expiry=date(2026, 9, 18),
        strike=100.0,
        option_type="put",
        iv_frozen=0.86,
        iv_as_of=as_of,
        entry_premium=6.7,
    )

    iv = sticky_delta_iv(
        surface,
        position,
        spot=110.0,
        years=45 / 365,
        rate=0.04,
        dividend=0.0,
    )

    assert iv is not None
    assert 0.80 < iv < 0.95


def test_sticky_delta_returns_none_outside_smile_range() -> None:
    as_of = datetime(2026, 8, 4, 20, 0, tzinfo=timezone.utc)
    surface = IvSurface(
        underlying="SPCX",
        spot=116.0,
        as_of=as_of,
        source="alpaca/chain",
        quality={"accepted": 2, "rejected": 0},
        smiles=[
            ExpirySmile(
                expiry=date(2026, 9, 18),
                nodes=[
                    SurfaceNode(strike=100.0, delta=-0.45, iv=0.95),
                    SurfaceNode(strike=110.0, delta=-0.20, iv=0.80),
                ],
            )
        ],
    )
    position = Position(
        occ_symbol="SPCX260918P00100000",
        underlying="SPCX",
        expiry=date(2026, 9, 18),
        strike=100.0,
        option_type="put",
        iv_frozen=0.86,
        iv_as_of=as_of,
        entry_premium=6.7,
    )

    assert (
        sticky_delta_iv(
            surface,
            position,
            spot=70.0,
            years=45 / 365,
            rate=0.04,
            dividend=0.0,
        )
        is None
    )


def test_mark_account_uses_saved_surface_before_frozen_iv(tmp_path) -> None:
    now = datetime(2026, 8, 4, 20, 0, tzinfo=timezone.utc)
    position = Position(
        occ_symbol="SPCX260918P00100000",
        underlying="SPCX",
        expiry=date(2026, 9, 18),
        strike=100.0,
        option_type="put",
        iv_frozen=0.50,
        iv_as_of=now,
        entry_premium=6.7,
    )
    surface = IvSurface(
        underlying="SPCX",
        spot=116.0,
        as_of=now,
        source="alpaca/chain",
        quality={"accepted": 2, "rejected": 0},
        smiles=[
            ExpirySmile(
                expiry=date(2026, 9, 18),
                nodes=[
                    SurfaceNode(strike=90.0, delta=-0.70, iv=1.20),
                    SurfaceNode(strike=110.0, delta=-0.20, iv=1.20),
                ],
            )
        ],
    )
    save_surface(surface, tmp_path)

    class Router:
        def get_spots(self, _symbols):
            return {
                "SPCX": SpotQuote(
                    symbol="SPCX", price=116.0, as_of=now, source="mock"
                )
            }

        def get_option_mid(self, _occ):
            return None

    row = mark_account(
        Account(name="demo", positions=[position]),
        home=tmp_path,
        router=Router(),
        now=now,
    )[0]

    expected = price_option(
        spot=116.0,
        strike=100.0,
        years=years_to_expiry(position.expiry, now),
        iv=1.20,
        rate=0.045,
        option_type="put",
        style="american",
    ).price
    assert row.theo == pytest.approx(expected, abs=1e-4)
    assert row.valuation_mode == "surface"
    assert row.surface_iv == pytest.approx(1.20)
    assert row.surface_as_of == now


def test_apply_surface_reference_ivs_updates_fallback_iv() -> None:
    now = datetime(2026, 8, 4, 20, 0, tzinfo=timezone.utc)
    position = Position(
        occ_symbol="SPCX260918P00100000",
        underlying="SPCX",
        expiry=date(2026, 9, 18),
        strike=100.0,
        option_type="put",
        iv_frozen=0.50,
        iv_as_of=now,
        entry_premium=6.7,
    )
    surface = IvSurface(
        underlying="SPCX",
        spot=116.0,
        as_of=now,
        source="alpaca/chain",
        quality={"accepted": 2, "rejected": 0},
        smiles=[
            ExpirySmile(
                expiry=date(2026, 9, 18),
                nodes=[
                    SurfaceNode(strike=90.0, delta=-0.70, iv=1.20),
                    SurfaceNode(strike=110.0, delta=-0.20, iv=1.20),
                ],
            )
        ],
    )

    refreshed = apply_surface_reference_ivs(
        [position],
        {"SPCX": surface},
        spots={"SPCX": 116.0},
        rate=0.045,
        dividend=0.0,
        now=now,
    )

    assert refreshed[0].iv_frozen == pytest.approx(1.20)
    assert refreshed[0].iv_source == "surface/alpaca/chain"


def test_mark_account_falls_back_when_surface_is_stale(tmp_path) -> None:
    calibrated = datetime(2026, 8, 4, 20, 0, tzinfo=timezone.utc)
    now = datetime(2026, 8, 7, 20, 0, tzinfo=timezone.utc)
    position = Position(
        occ_symbol="SPCX260918P00100000",
        underlying="SPCX",
        expiry=date(2026, 9, 18),
        strike=100.0,
        option_type="put",
        iv_frozen=0.50,
        iv_as_of=calibrated,
        entry_premium=6.7,
    )
    save_surface(
        IvSurface(
            underlying="SPCX",
            spot=116.0,
            as_of=calibrated,
            source="alpaca/chain",
            quality={"accepted": 2, "rejected": 0},
            smiles=[
                ExpirySmile(
                    expiry=date(2026, 9, 18),
                    nodes=[
                        SurfaceNode(strike=90.0, delta=-0.70, iv=1.20),
                        SurfaceNode(strike=110.0, delta=-0.20, iv=1.20),
                    ],
                )
            ],
        ),
        tmp_path,
    )

    class Router:
        def get_spots(self, _symbols):
            return {
                "SPCX": SpotQuote(
                    symbol="SPCX", price=116.0, as_of=now, source="mock"
                )
            }

        def get_option_mid(self, _occ):
            return None

    row = mark_account(
        Account(name="demo", positions=[position]),
        home=tmp_path,
        router=Router(),
        now=now,
    )[0]

    expected = price_option(
        spot=116.0,
        strike=100.0,
        years=years_to_expiry(position.expiry, now),
        iv=0.50,
        rate=0.045,
        option_type="put",
        style="american",
    ).price
    assert row.theo == pytest.approx(expected, abs=1e-4)
    assert row.valuation_mode == "frozen"
