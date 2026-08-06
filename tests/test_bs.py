import pytest

from optionda.pricing.bs import black_scholes, implied_volatility, price_option


def test_atm_call_positive() -> None:
    result = black_scholes(
        spot=100,
        strike=100,
        years=1.0,
        iv=0.2,
        rate=0.05,
        dividend=0.0,
        option_type="call",
    )
    # Rough BS ATM ≈ 10.45 for these params
    assert 8.0 < result.price < 13.0
    assert 0.5 < result.delta < 0.7


def test_deep_itm_put_delta() -> None:
    result = black_scholes(
        spot=80,
        strike=100,
        years=0.5,
        iv=0.25,
        option_type="put",
    )
    assert result.delta < -0.5
    assert result.price > 15


def test_implied_vol_roundtrip_european() -> None:
    target_iv = 0.32
    price = black_scholes(
        spot=100,
        strike=105,
        years=0.4,
        iv=target_iv,
        rate=0.045,
        option_type="call",
    ).price
    solved = implied_volatility(
        spot=100,
        strike=105,
        years=0.4,
        price=price,
        rate=0.045,
        option_type="call",
        style="european",
    )
    assert solved == pytest.approx(target_iv, abs=1e-4)


def test_american_put_worth_at_least_european() -> None:
    kwargs = dict(
        spot=100.0,
        strike=100.0,
        years=0.5,
        iv=0.35,
        rate=0.05,
        dividend=0.0,
        option_type="put",
    )
    euro = black_scholes(**kwargs).price
    amer = price_option(**kwargs, style="american", steps=200).price
    assert amer >= euro - 1e-6


def test_american_call_no_div_near_european() -> None:
    kwargs = dict(
        spot=100.0,
        strike=100.0,
        years=0.5,
        iv=0.25,
        rate=0.05,
        dividend=0.0,
        option_type="call",
    )
    euro = black_scholes(**kwargs).price
    amer = price_option(**kwargs, style="american", steps=250).price
    assert amer == pytest.approx(euro, rel=0.02)


def test_american_implied_vol_roundtrip() -> None:
    target_iv = 0.40
    price = price_option(
        spot=110,
        strike=100,
        years=0.25,
        iv=target_iv,
        rate=0.04,
        option_type="put",
        style="american",
        steps=120,
        greeks=False,
    ).price
    solved = implied_volatility(
        spot=110,
        strike=100,
        years=0.25,
        price=price,
        rate=0.04,
        option_type="put",
        style="american",
        steps=120,
    )
    assert solved == pytest.approx(target_iv, abs=2e-3)


def test_american_tree_uses_even_odd_convergence_average() -> None:
    kwargs = dict(
        spot=109.66,
        strike=100.0,
        years=0.12,
        iv=0.925,
        rate=0.045,
        option_type="put",
        style="american",
    )
    coarse = price_option(**kwargs, steps=80)
    fine = price_option(**kwargs, steps=240)
    # Averaging adjacent CRR trees removes the large even/odd lattice jump.
    assert coarse.price == pytest.approx(fine.price, abs=0.01)
    assert coarse.delta == pytest.approx(fine.delta, abs=0.01)
