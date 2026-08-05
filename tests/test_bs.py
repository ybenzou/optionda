from optionda.pricing.bs import black_scholes


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
