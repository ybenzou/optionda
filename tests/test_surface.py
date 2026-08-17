from datetime import date, datetime, timedelta, timezone

import pytest

from optionda.engine import (
    apply_surface_reference_ivs,
    calibrate_surfaces,
    ensure_surfaces,
    mark_account,
    resolve_close_premium,
)
from optionda.models import Account, Position, SpotQuote
from optionda.pricing.bs import price_option, years_to_expiry
from optionda.pricing.surface import (
    ExpirySmile,
    IvSurface,
    SurfaceNode,
    build_surface,
    close_premium_from_surface,
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


def test_weekend_align_keeps_friday_close_prints() -> None:
    # Beijing Monday 10:36 == Sunday 22:36 ET. Last close is Friday 16:00 ET.
    # Alpaca still serves Friday 15:59:59 prints; age them vs that snapshot,
    # not vs wall-clock now, or every name fails with no usable nodes.
    friday_print = datetime(2026, 8, 14, 19, 59, 59, tzinfo=timezone.utc)
    sunday_night = datetime(2026, 8, 17, 2, 36, tzinfo=timezone.utc)
    surface = build_surface(
        "AAPL",
        spot=305.33,
        snapshots={
            "AAPL261120C00350000": {
                "impliedVolatility": 0.25,
                "greeks": {"delta": 0.19},
                "latestQuote": {
                    "bp": 3.70,
                    "ap": 3.90,
                    "t": friday_print.isoformat(),
                },
            }
        },
        as_of=sunday_night,
        quote_as_of=friday_print,
        source="alpaca/chain",
    )
    assert surface.quality["accepted"] == 1
    assert surface.as_of == friday_print


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
    from optionda.market.session import load_close_premiums

    book = load_close_premiums("SPCX", tmp_path)
    assert book is not None
    assert book.premiums["SPCX260918P00100000"] == pytest.approx(6.23)


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


def test_v2_surface_loads_as_legacy_v3(tmp_path) -> None:
    path = tmp_path / "surfaces"
    path.mkdir()
    (path / "AAPL.json").write_text(
        """
{
  "schema_version": 2,
  "underlying": "AAPL",
  "spot": 210.5,
  "as_of": "2026-08-14T19:59:59+00:00",
  "source": "alpaca/chain",
  "quality": {"accepted": 1, "rejected": 0},
  "smiles": [
    {
      "expiry": "2026-11-20",
      "nodes": [{"strike": 210.0, "delta": 0.5, "iv": 0.28}]
    }
  ]
}
""".strip(),
        encoding="utf-8",
    )
    loaded = load_surface("AAPL", tmp_path)
    assert loaded is not None
    assert loaded.legacy is True
    assert loaded.calibration_spot == 210.5
    assert loaded.quote_as_of.isoformat() == "2026-08-14T19:59:59+00:00"
    assert loaded.session_date == date(2026, 8, 14)


def test_build_surface_accepts_friday_close_print() -> None:
    from optionda.market.session import MarketSession
    from zoneinfo import ZoneInfo

    et = ZoneInfo("America/New_York")
    session = MarketSession(
        session_date=date(2026, 8, 14),
        open_at=datetime(2026, 8, 14, 9, 30, tzinfo=et),
        close_at=datetime(2026, 8, 14, 16, 0, tzinfo=et),
    )
    quote = datetime(2026, 8, 14, 15, 59, 59, tzinfo=et)
    surface = build_surface(
        "AAPL",
        spot=230.0,
        snapshots={
            "AAPL261120C00230000": {
                "impliedVolatility": 0.28,
                "latestQuote": {
                    "bp": 4.9,
                    "ap": 5.1,
                    "t": quote.isoformat(),
                },
            }
        },
        as_of=datetime(2026, 8, 16, 12, 0, tzinfo=timezone.utc),
        quote_as_of=quote,
        target_session=session,
        source="alpaca/chain",
    )
    assert surface.session_date == date(2026, 8, 14)
    assert surface.legacy is False


def test_weekend_wall_clock_does_not_expire_friday_quotes() -> None:
    from optionda.market.session import MarketSession
    from zoneinfo import ZoneInfo

    et = ZoneInfo("America/New_York")
    session = MarketSession(
        session_date=date(2026, 8, 14),
        open_at=datetime(2026, 8, 14, 9, 30, tzinfo=et),
        close_at=datetime(2026, 8, 14, 16, 0, tzinfo=et),
    )
    quote = datetime(2026, 8, 14, 15, 59, 59, tzinfo=et)
    surface = build_surface(
        "AAPL",
        spot=230.0,
        snapshots={
            "AAPL261120C00230000": {
                "impliedVolatility": 0.28,
                "latestQuote": {
                    "bp": 4.9,
                    "ap": 5.1,
                    "t": quote.isoformat(),
                },
            }
        },
        as_of=datetime(2026, 8, 16, 10, 0, tzinfo=timezone.utc),
        quote_as_of=quote,
        target_session=session,
        source="alpaca/chain",
    )
    assert surface.quality["accepted"] == 1


def test_build_surface_rejects_wrong_session_quotes() -> None:
    from optionda.market.session import MarketSession
    from zoneinfo import ZoneInfo

    et = ZoneInfo("America/New_York")
    session = MarketSession(
        session_date=date(2026, 8, 14),
        open_at=datetime(2026, 8, 14, 9, 30, tzinfo=et),
        close_at=datetime(2026, 8, 14, 16, 0, tzinfo=et),
    )
    with pytest.raises(ValueError, match="outside the 2026-08-14 close window"):
        build_surface(
            "AAPL",
            spot=230.0,
            snapshots={
                "AAPL261120C00230000": {
                    "impliedVolatility": 0.28,
                    "latestQuote": {
                        "bp": 4.9,
                        "ap": 5.1,
                        "t": "2026-08-13T19:59:59+00:00",
                    },
                }
            },
            as_of=datetime(2026, 8, 14, 21, 0, tzinfo=timezone.utc),
            quote_as_of=datetime(2026, 8, 13, 19, 59, 59, tzinfo=timezone.utc),
            target_session=session,
            source="alpaca/chain",
        )


def test_build_surface_filters_skewed_node() -> None:
    from optionda.market.session import MarketSession
    from zoneinfo import ZoneInfo

    et = ZoneInfo("America/New_York")
    session = MarketSession(
        session_date=date(2026, 8, 14),
        open_at=datetime(2026, 8, 14, 9, 30, tzinfo=et),
        close_at=datetime(2026, 8, 14, 16, 0, tzinfo=et),
    )
    quote = datetime(2026, 8, 14, 15, 59, 59, tzinfo=et)
    surface = build_surface(
        "AAPL",
        spot=230.0,
        snapshots={
            "AAPL261120C00230000": {
                "impliedVolatility": 0.28,
                "latestQuote": {
                    "bp": 4.9,
                    "ap": 5.1,
                    "t": quote.isoformat(),
                },
            },
            "AAPL261120C00240000": {
                "impliedVolatility": 0.30,
                "latestQuote": {
                    "bp": 3.9,
                    "ap": 4.1,
                    "t": (quote - timedelta(hours=2)).isoformat(),
                },
            },
        },
        as_of=quote,
        quote_as_of=quote,
        target_session=session,
        source="alpaca/chain",
    )
    assert surface.quality["accepted"] == 1
    assert surface.quality["rejected"] == 1


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
            raise AssertionError("ordinary mark must not fetch option mids")

    from optionda.market.session import MarketSession
    from zoneinfo import ZoneInfo

    et = ZoneInfo("America/New_York")
    row = mark_account(
        Account(name="demo", positions=[position]),
        home=tmp_path,
        router=Router(),
        now=now,
        completed_session=MarketSession(
            session_date=date(2026, 8, 7),
            open_at=datetime(2026, 8, 7, 9, 30, tzinfo=et),
            close_at=datetime(2026, 8, 7, 16, 0, tzinfo=et),
        ),
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
    assert row.iv_stale is True
    assert row.model_iv == pytest.approx(1.20)


def test_mark_account_uses_session_reference_not_calibration_spot(tmp_path) -> None:
    from optionda.market.session import SessionReference, save_session_reference

    now = datetime(2026, 8, 14, 21, 0, tzinfo=timezone.utc)
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
    save_surface(
        IvSurface(
            underlying="SPCX",
            spot=140.0,
            as_of=now,
            source="alpaca/chain",
            quality={"accepted": 2, "rejected": 0},
            session_date=date(2026, 8, 14),
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
    save_session_reference(
        SessionReference(
            underlying="SPCX",
            session_date=date(2026, 8, 14),
            session_close_at=datetime(2026, 8, 14, 20, 0, tzinfo=timezone.utc),
            close_spot=116.0,
            source="alpaca/sip/1Day",
            updated_at=now,
        ),
        tmp_path,
    )

    class Router:
        def get_spots(self, _symbols):
            return {
                "SPCX": SpotQuote(
                    symbol="SPCX", price=120.0, as_of=now, source="mock"
                )
            }

        def get_option_mid(self, _occ):
            raise AssertionError("ordinary mark must not fetch option mids")

    row = mark_account(
        Account(name="demo", positions=[position]),
        home=tmp_path,
        router=Router(),
        now=now,
    )[0]
    assert row.close_spot == pytest.approx(116.0)
    assert row.spot == pytest.approx(120.0)
    assert row.reference_session_date == date(2026, 8, 14)


def test_mark_account_never_calls_option_mid(tmp_path) -> None:
    now = datetime(2026, 8, 14, 21, 0, tzinfo=timezone.utc)
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

    class Router:
        mids = 0

        def get_spots(self, _symbols):
            return {
                "SPCX": SpotQuote(
                    symbol="SPCX", price=116.0, as_of=now, source="mock"
                )
            }

        def get_option_mid(self, _occ):
            Router.mids += 1
            return 9.99

    row = mark_account(
        Account(name="demo", positions=[position]),
        home=tmp_path,
        router=Router(),
        now=now,
    )[0]
    assert Router.mids == 0
    assert row.live is None
    assert row.iv_fallback is True
    assert row.model_iv == pytest.approx(0.50)


def test_mark_account_progress_keeps_fetch_and_mark_separate(tmp_path) -> None:
    now = datetime(2026, 8, 14, 21, 0, tzinfo=timezone.utc)
    positions = [
        Position(
            occ_symbol="SKHY261016C00200000",
            underlying="SKHY",
            expiry=date(2026, 10, 16),
            strike=200.0,
            option_type="call",
            iv_frozen=0.40,
            iv_as_of=now,
            entry_premium=6.92,
        ),
        Position(
            occ_symbol="AAPL261120C00350000",
            underlying="AAPL",
            expiry=date(2026, 11, 20),
            strike=350.0,
            option_type="call",
            iv_frozen=0.25,
            iv_as_of=now,
            entry_premium=4.51,
        ),
    ]

    class Router:
        def get_spots(self, symbols):
            return {
                name: SpotQuote(symbol=name, price=100.0, as_of=now, source="mock")
                for name in symbols
            }

        def get_option_mid(self, _occ):
            raise AssertionError("ordinary mark must not fetch option mids")

    events: list[tuple[str, int, int]] = []
    mark_account(
        Account(name="demo", positions=positions),
        home=tmp_path,
        router=Router(),
        now=now,
        on_progress=lambda label, done, total: events.append((label, done, total)),
    )
    fetch = [(label, done, total) for label, done, total in events if label.startswith("1/2 fetch")]
    mark = [(label, done, total) for label, done, total in events if label.startswith("2/2 mark")]
    assert fetch
    assert all(total == 1 for _, _, total in fetch)
    assert mark
    assert all(total == 2 for _, _, total in mark)
    assert mark[0][1] == 0
    assert mark[-1][1] == 2
    assert not any(total == 3 for _, _, total in events)


def test_close_premium_from_surface_exact_and_interpolated() -> None:
    surface = IvSurface(
        underlying="SKHY",
        spot=20.0,
        as_of=datetime(2026, 8, 14, 20, 0, tzinfo=timezone.utc),
        source="alpaca/chain",
        quality={"accepted": 2, "rejected": 0},
        session_date=date(2026, 8, 14),
        smiles=[
            ExpirySmile(
                expiry=date(2026, 12, 18),
                nodes=[
                    SurfaceNode(
                        strike=20.0,
                        delta=0.45,
                        iv=0.40,
                        option_type="call",
                        premium=10.80,
                    ),
                    SurfaceNode(
                        strike=25.0,
                        delta=0.30,
                        iv=0.38,
                        option_type="call",
                        premium=8.10,
                    ),
                ],
            )
        ],
    )
    exact = Position(
        occ_symbol="SKHY261218C00020000",
        underlying="SKHY",
        expiry=date(2026, 12, 18),
        strike=20.0,
        option_type="call",
        iv_frozen=0.40,
        iv_as_of=surface.as_of,
        entry_premium=11.0,
    )
    between = exact.model_copy(
        update={"occ_symbol": "SKHY261218C00022500", "strike": 22.5}
    )
    assert close_premium_from_surface(surface, exact) == pytest.approx(10.80)
    assert close_premium_from_surface(surface, between) == pytest.approx(9.45)


def test_mark_account_uses_stored_close_premium(tmp_path) -> None:
    from optionda.market.session import (
        ClosePremiums,
        SessionReference,
        save_close_premiums,
        save_session_reference,
    )

    now = datetime(2026, 8, 16, 14, 0, tzinfo=timezone.utc)
    position = Position(
        occ_symbol="SKHY261218C00020000",
        underlying="SKHY",
        expiry=date(2026, 12, 18),
        strike=20.0,
        option_type="call",
        iv_frozen=0.40,
        iv_as_of=now,
        entry_premium=11.0,
    )
    save_surface(
        IvSurface(
            underlying="SKHY",
            spot=20.0,
            as_of=datetime(2026, 8, 14, 20, 0, tzinfo=timezone.utc),
            source="alpaca/chain",
            quality={"accepted": 1, "rejected": 0},
            session_date=date(2026, 8, 14),
            smiles=[
                ExpirySmile(
                    expiry=date(2026, 12, 18),
                    nodes=[
                        SurfaceNode(
                            strike=20.0,
                            delta=0.45,
                            iv=0.40,
                            option_type="call",
                            premium=10.80,
                        )
                    ],
                )
            ],
        ),
        tmp_path,
    )
    save_session_reference(
        SessionReference(
            underlying="SKHY",
            session_date=date(2026, 8, 14),
            session_close_at=datetime(2026, 8, 14, 20, 0, tzinfo=timezone.utc),
            close_spot=20.0,
            source="alpaca/sip/1Day",
            updated_at=now,
        ),
        tmp_path,
    )
    save_close_premiums(
        ClosePremiums(
            underlying="SKHY",
            session_date=date(2026, 8, 14),
            premiums={"SKHY261218C00020000": 10.80},
            source="alpaca/chain",
            updated_at=now,
        ),
        tmp_path,
    )

    class Router:
        def get_spots(self, _symbols):
            return {
                "SKHY": SpotQuote(
                    symbol="SKHY", price=21.0, as_of=now, source="mock"
                )
            }

        def get_option_mid(self, _occ):
            raise AssertionError("ordinary mark must not fetch option mids")

    row = mark_account(
        Account(name="demo", positions=[position]),
        home=tmp_path,
        router=Router(),
        now=now,
    )[0]
    assert row.close_premium == pytest.approx(10.80)
    assert row.theo is not None
    assert row.theo_chg == pytest.approx(row.theo - 10.80)


def test_mark_account_uses_surface_premium_when_book_missing(tmp_path) -> None:
    now = datetime(2026, 8, 16, 14, 0, tzinfo=timezone.utc)
    position = Position(
        occ_symbol="SKHY261218C00020000",
        underlying="SKHY",
        expiry=date(2026, 12, 18),
        strike=20.0,
        option_type="call",
        iv_frozen=0.40,
        iv_as_of=now,
        entry_premium=11.0,
    )
    save_surface(
        IvSurface(
            underlying="SKHY",
            spot=20.0,
            as_of=datetime(2026, 8, 14, 20, 0, tzinfo=timezone.utc),
            source="alpaca/chain",
            quality={"accepted": 1, "rejected": 0},
            session_date=date(2026, 8, 14),
            smiles=[
                ExpirySmile(
                    expiry=date(2026, 12, 18),
                    nodes=[
                        SurfaceNode(
                            strike=20.0,
                            delta=0.45,
                            iv=0.40,
                            option_type="call",
                            premium=10.80,
                        )
                    ],
                )
            ],
        ),
        tmp_path,
    )

    class Router:
        def get_spots(self, _symbols):
            return {
                "SKHY": SpotQuote(
                    symbol="SKHY", price=21.0, as_of=now, source="mock"
                )
            }

        def get_option_mid(self, _occ):
            raise AssertionError("ordinary mark must not fetch option mids")

    row = mark_account(
        Account(name="demo", positions=[position]),
        home=tmp_path,
        router=Router(),
        now=now,
    )[0]
    assert row.close_premium == pytest.approx(10.80)
    assert row.theo_chg == pytest.approx(row.theo - 10.80)


def test_resolve_close_premium_models_at_close_spot() -> None:
    now = datetime(2026, 8, 14, 20, 0, tzinfo=timezone.utc)
    position = Position(
        occ_symbol="SKHY261218C00020000",
        underlying="SKHY",
        expiry=date(2026, 12, 18),
        strike=20.0,
        option_type="call",
        iv_frozen=0.40,
        iv_as_of=now,
        entry_premium=11.0,
    )
    expected = price_option(
        spot=20.0,
        strike=20.0,
        years=years_to_expiry(position.expiry, now),
        iv=0.40,
        rate=0.045,
        option_type="call",
        style="american",
        greeks=False,
    ).price
    close_px = resolve_close_premium(
        position,
        book=None,
        surface=None,
        close_spot=20.0,
        session_close_at=now,
        rate=0.045,
        dividend=0.0,
        style="american",
    )
    assert close_px == pytest.approx(expected)
