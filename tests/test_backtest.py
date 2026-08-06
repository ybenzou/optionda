from optionda.backtest import evaluate_rows, recommended_sticky_delta_weight


def test_evaluate_rows_reports_model_error_and_interval_coverage() -> None:
    result = evaluate_rows(
        [
            {"occ": "A", "live": 10.0, "model": 9.0, "model_low": 8.0, "model_high": 11.0},
            {"occ": "B", "live": 5.0, "model": 7.0, "model_low": 6.0, "model_high": 8.0},
        ]
    )
    assert result.count == 2
    assert result.mae == 1.5
    assert result.interval_coverage == 0.5


def test_recommended_weight_interpolates_between_scenarios() -> None:
    rows = [
        {"live": 9.0, "sticky_strike_model": 8.0, "sticky_delta_model": 10.0},
        {"live": 19.0, "sticky_strike_model": 18.0, "sticky_delta_model": 20.0},
    ]
    assert recommended_sticky_delta_weight(rows) == 0.5
