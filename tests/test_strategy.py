from datetime import date, datetime, timezone

from optionda.strategy import (
    ContractSeries,
    StrategyPoint,
    build_strategy,
    load_strategy,
    month_bounds,
    payback_days,
    shift_month,
    slice_window,
    year_bounds,
)

OCC = "SPCX261218C00205000"


def _add(day: str, qty: float, cost: float) -> dict:
    return {
        "ts": f"{day}T20:00:00+00:00",
        "event": "add" if qty == 1 and cost == 8.2 else "merge",
        "id": "spcx",
        "occ": OCC,
        "side": "long",
        "qty": qty,
        "qty_added": 1.0,
        "cost": cost,
        "cost_added": cost,
        "iv": 0.5,
    }


def _sell(day: str, remaining: float, exit_px: float) -> dict:
    return {
        "ts": f"{day}T20:00:00+00:00",
        "event": "sell",
        "id": "spcx",
        "occ": OCC,
        "side": "long",
        "qty_sold": 1.0,
        "qty_remaining": remaining,
        "exit": exit_px,
        "avg_cost": 4.0,
        "realized": 100.0,
        "closed": remaining <= 0,
        "iv": 0.5,
    }


def test_strategy_steps_qty_and_marks_trades() -> None:
    events = [
        _add("2026-09-01", 1, 8.2),
        _add("2026-09-02", 2, 6.0),
        _sell("2026-09-04", 1, 5.0),
    ]
    closes = {("SPCX", date(2026, 9, 2)): 100.0}
    marks = {(OCC, date(2026, 9, 2)): (4.0, -400.0)}
    series = build_strategy(events, closes=closes, marks=marks, end=date(2026, 9, 4))
    assert len(series) == 1
    got = series[0]
    by_day = {point.day: point for point in got.points}
    assert by_day[date(2026, 9, 1)].qty == 1
    assert by_day[date(2026, 9, 2)].qty == 2
    assert by_day[date(2026, 9, 2)].cost == (8.2 + 6.0) / 2
    cost = (8.2 + 6.0) / 2
    assert by_day[date(2026, 9, 2)].pnl_pct == (4.0 - cost) / cost * 100
    assert by_day[date(2026, 9, 2)].close_spot == 100.0
    assert by_day[date(2026, 9, 3)].pnl_pct is None
    assert by_day[date(2026, 9, 4)].qty == 1
    assert [(trade.day, trade.side, trade.qty) for trade in got.trades] == [
        (date(2026, 9, 1), "add", 1.0),
        (date(2026, 9, 2), "add", 1.0),
        (date(2026, 9, 4), "sell", 1.0),
    ]


def test_backdated_add_keeps_later_sells() -> None:
    events = [
        {
            "ts": "2026-09-01T20:00:00+00:00",
            "event": "add",
            "id": "spcx",
            "occ": OCC,
            "side": "long",
            "qty": 2,
            "qty_added": 2,
            "cost": 4.0,
            "cost_added": 4.0,
            "iv": 0.5,
        },
        _sell("2026-09-08", 1, 5.0),
        {
            "ts": "2026-09-04T20:00:00+00:00",
            "event": "merge",
            "id": "spcx",
            "occ": OCC,
            "side": "long",
            "qty": 14,
            "qty_added": 1,
            "cost": 3.9,
            "cost_added": 3.9,
            "iv": 0.5,
        },
    ]
    series = build_strategy(events, end=date(2026, 9, 8))
    by_day = {point.day: point.qty for point in series[0].points}
    assert by_day[date(2026, 9, 4)] == 3
    assert by_day[date(2026, 9, 8)] == 2


def test_weekend_add_is_held_on_the_next_session() -> None:
    series = build_strategy([_add("2026-09-05", 1, 8.2)], end=date(2026, 9, 7))
    by_day = {point.day: point.qty for point in series[0].points}
    assert date(2026, 9, 5) not in by_day
    assert by_day[date(2026, 9, 7)] == 1


def test_undo_drops_the_batch_and_keeps_a_backdated_add() -> None:
    events = [
        _add("2026-09-01", 1, 8.2),
        _add("2026-09-02", 2, 6.0),
        {"ts": "2026-09-02T21:00:00+00:00", "event": "undo", "n_events": 1},
        {
            "ts": "2026-09-01T20:00:00+00:00",
            "event": "merge",
            "id": "spcx",
            "occ": OCC,
            "qty": 2,
            "qty_added": 1.0,
            "cost": 6.0,
            "cost_added": 6.0,
            "iv": 0.5,
        },
    ]
    series = build_strategy(events, end=date(2026, 9, 2))
    by_day = {point.day: point.qty for point in series[0].points}
    assert by_day[date(2026, 9, 1)] == 2
    assert by_day[date(2026, 9, 2)] == 2
    assert [(trade.day, trade.side) for trade in series[0].trades] == [
        (date(2026, 9, 1), "add"),
        (date(2026, 9, 1), "add"),
    ]


def test_payback_counts_days_until_model_is_green() -> None:
    points = [
        StrategyPoint(date(2026, 9, 1), 1, 2.0, 10.0, 1.0, -50.0),
        StrategyPoint(date(2026, 9, 2), 1, 2.0, 10.0, 1.5, -25.0),
        StrategyPoint(date(2026, 9, 4), 1, 2.0, 12.0, 2.1, 5.0),
    ]
    assert payback_days(points) == 3
    assert payback_days(points[:2]) is None


def test_closes_stop_before_todays_unfinished_session() -> None:
    from zoneinfo import ZoneInfo

    from optionda.strategy import _closes_through

    morning = datetime(2026, 9, 28, 4, 0, tzinfo=ZoneInfo("America/New_York"))
    assert _closes_through(date(2026, 9, 28), morning) == date(2026, 9, 25)
    after = datetime(2026, 9, 28, 16, 30, tzinfo=ZoneInfo("America/New_York"))
    assert _closes_through(date(2026, 9, 28), after) == date(2026, 9, 28)


def test_fully_sold_contract_stays_in_the_year() -> None:
    events = [
        _add("2026-01-05", 1, 8.2),
        _sell("2026-03-02", 0, 9.0),
    ]
    series = build_strategy(events, end=date(2026, 12, 31))
    assert [item.occ for item in series] == [OCC]
    days = [point.day for point in series[0].points]
    assert date(2026, 1, 5) in days
    assert date(2026, 2, 27) in days
    flat = series[0].points[-1]
    assert flat.day == date(2026, 3, 2)
    assert flat.qty == 0
    assert round(flat.pnl_pct or 0, 4) == round((9.0 - 8.2) / 8.2 * 100, 4)
    year = slice_window(series[0], *year_bounds(date(2026, 6, 1)))
    assert year.points
    later = slice_window(series[0], *month_bounds(date(2026, 9, 1)))
    assert later.points == []


def test_week_bounds_cover_the_previous_week_too() -> None:
    from optionda.strategy import shift_week, week_bounds

    start, end = week_bounds(date(2026, 9, 15))
    assert start == date(2026, 9, 7)
    assert end == date(2026, 9, 20)
    assert (end - start).days == 13
    assert week_bounds(date(2026, 9, 14)) == (start, end)
    assert week_bounds(date(2026, 9, 20)) == (start, end)
    assert shift_week(date(2026, 9, 15), 1) == date(2026, 9, 22)
    assert shift_week(date(2026, 9, 15), -1) == date(2026, 9, 8)


def test_month_window_carries_qty_from_before() -> None:
    series = ContractSeries(
        occ=OCC,
        points=[
            StrategyPoint(date(2026, 8, 31), 4, 3.0, None, None, None),
            StrategyPoint(date(2026, 9, 2), 5, 3.2, 100.0, 4.0, 25.0),
        ],
        trades=[],
        payback_days=None,
    )
    start, end = month_bounds(date(2026, 9, 15))
    window = slice_window(series, start, end)
    assert window.points[0].day == date(2026, 9, 1)
    assert window.points[0].qty == 4
    assert window.points[0].pnl_pct is None
    assert window.points[-1].qty == 5
    assert year_bounds(date(2026, 9, 15)) == (date(2026, 1, 1), date(2026, 12, 31))
    assert year_bounds(date(2026, 9, 15), opened=date(2026, 8, 12)) == (
        date(2026, 8, 1),
        date(2026, 12, 31),
    )
    assert year_bounds(date(2026, 9, 15), opened=date(2026, 1, 6)) == (
        date(2026, 1, 1),
        date(2026, 12, 31),
    )
    assert shift_month(date(2026, 9, 15), -1) == date(2026, 8, 1)
    assert shift_month(date(2026, 1, 15), -1) == date(2025, 12, 1)


def test_month_window_keeps_the_pnl_line_on_the_left_edge() -> None:
    series = ContractSeries(
        occ=OCC,
        points=[
            StrategyPoint(date(2026, 8, 28), 4, 3.0, 100.0, 3.3, 10.0),
            StrategyPoint(date(2026, 9, 2), 5, 3.2, 100.0, 4.0, 25.0),
        ],
        trades=[],
        payback_days=None,
    )
    window = slice_window(series, *month_bounds(date(2026, 9, 15)))
    assert window.points[0].day == date(2026, 9, 1)
    assert window.points[0].pnl_pct == 10.0
    assert window.points[0].model == 3.3


def test_strategy_cache_rebuilds_from_journal(tmp_path) -> None:
    from optionda.journal import append_event, log_path

    home = tmp_path
    append_event(
        "main",
        {
            "event": "add",
            "id": "spcx",
            "occ": OCC,
            "side": "long",
            "qty": 2,
            "qty_added": 2,
            "cost": 3.9,
            "iv": 0.4,
        },
        home=home,
        ts=datetime(2026, 9, 4, 20, tzinfo=timezone.utc),
    )
    first = load_strategy("main", home, end=date(2026, 9, 4))
    assert first[0].points[-1].qty == 2
    assert first[0].points[-1].cost == 3.9
    again = load_strategy("main", home, end=date(2026, 9, 4))
    assert again[0].points[-1].qty == 2
    cache = home / "strategy" / "main.sqlite"
    assert cache.exists()
    from optionda.journal import ledger_db_path

    assert ledger_db_path("main", home).exists()
    from optionda.strategy import read_strategy_store, refresh_strategy

    stored = read_strategy_store("main", home)
    assert stored is not None and stored[0].points[-1].qty == 2
    with log_path("main", home).open("a", encoding="utf-8") as handle:
        handle.write('{"event": "run", "rows": []}\n')
    same, changed = refresh_strategy("main", home, end=date(2026, 9, 4))
    assert changed is False
    assert same[0].points[-1].qty == 2
    append_event(
        "main",
        {
            "event": "merge",
            "id": "spcx",
            "occ": OCC,
            "side": "long",
            "qty": 3,
            "qty_added": 1,
            "cost": 3.5,
            "cost_added": 3.5,
            "iv": 0.4,
        },
        home=home,
        ts=datetime(2026, 9, 4, 21, tzinfo=timezone.utc),
    )
    rebuilt, changed = refresh_strategy("main", home, end=date(2026, 9, 4))
    assert changed is True
    assert rebuilt[0].points[-1].qty == 3


def test_offscreen_refresh_leaves_pricing_to_the_worker(tmp_path, monkeypatch) -> None:
    from optionda.strategy import refresh_strategy

    seen: dict[str, object] = {}

    def offscreen(account, home, end):
        seen["account"] = account
        seen["end"] = end
        return [], False

    monkeypatch.setattr("optionda.strategy._refresh_offscreen", offscreen)
    series, changed = refresh_strategy(
        "main", tmp_path, end=date(2026, 9, 4), offscreen=True
    )
    assert seen == {"account": "main", "end": date(2026, 9, 4)}
    assert series == []
    assert changed is False
