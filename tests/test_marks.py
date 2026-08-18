from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from optionda.analytics import build_report
from optionda.gui.charts import mark_xy, position_mark_xy
from optionda.journal import log_path
from optionda.market.session import DailyClose
from optionda.marks import (
    book_on,
    build_mark_series,
    load_surface_for_session,
    mark_lot,
)
from optionda.models import Position
from optionda.pricing.bs import price_option, years_to_expiry
from optionda.pricing.surface import (
    ExpirySmile,
    IvSurface,
    SurfaceNode,
    save_surface,
)


def _ts(day: str) -> str:
    return f"{day}T20:00:00+00:00"


def _journal(tmp_path: Path) -> None:
    path = log_path("demo", tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(
            [
                '{"ts":"' + _ts("2026-03-10") + '","event":"add","id":"hood","occ":"HOOD260618C00150000","qty_added":1,"qty":1,"cost":5.2,"iv":0.45,"side":"long"}',
                '{"ts":"' + _ts("2026-03-12") + '","event":"sell","id":"hood","occ":"HOOD260618C00150000","realized":225,"closed":true,"qty_sold":1,"avg_cost":5.2}',
            ]
        )
        + "\n",
        encoding="utf-8",
    )


def _close(symbol: str, day: date, price: float) -> DailyClose:
    return DailyClose(
        symbol=symbol,
        session_date=day,
        close=price,
        source="test/1Day",
    )


def _closer(table: dict[tuple[str, date], float]):
    calls = {"n": 0}

    def fetch(symbols: list[str], start: date, end: date) -> dict[str, dict[date, DailyClose]]:
        calls["n"] += 1
        out: dict[str, dict[date, DailyClose]] = {}
        for symbol in symbols:
            series: dict[date, DailyClose] = {}
            cursor = start
            while cursor <= end:
                price = table.get((symbol, cursor))
                if price is not None:
                    series[cursor] = _close(symbol, cursor, price)
                cursor = date.fromordinal(cursor.toordinal() + 1)
            if series:
                out[symbol] = series
        return out

    fetch.calls = calls  # type: ignore[attr-defined]
    return fetch


def test_book_on_keeps_open_then_drops_closed(tmp_path) -> None:
    _journal(tmp_path)
    from optionda.analytics import read_events

    events = read_events(log_path("demo", tmp_path))
    held = book_on(events, date(2026, 3, 11))
    assert len(held.lots) == 1
    assert held.lots[0].position_id == "hood"
    assert held.lots[0].cost == 5.2
    assert held.lots[0].iv == 0.45
    assert held.realized_cum == 0.0
    gone = book_on(events, date(2026, 3, 12))
    assert gone.lots == []
    assert gone.realized_cum == 225.0


def test_book_on_applies_compact_refresh_iv() -> None:
    held = book_on(
        [
            {
                "ts": _ts("2026-03-10"),
                "event": "add",
                "id": "hood",
                "occ": "HOOD260618C00150000",
                "qty": 1,
                "cost": 5.2,
                "iv": 0.45,
                "side": "long",
            },
            {
                "ts": _ts("2026-03-11"),
                "event": "refresh_iv",
                "ivs": {"hood": 0.60},
            },
        ],
        date(2026, 3, 11),
    )
    assert held.lots[0].iv == pytest.approx(0.60)


def test_mark_lot_matches_desk_upnl_formula() -> None:
    lot = book_on(
        [
            {
                "ts": _ts("2026-03-10"),
                "event": "add",
                "id": "hood",
                "occ": "HOOD260618C00150000",
                "qty": 1,
                "cost": 5.2,
                "iv": 0.45,
                "side": "long",
            }
        ],
        date(2026, 3, 11),
    ).lots[0]
    day = date(2026, 3, 11)
    close = 48.0
    marked = mark_lot(lot, close=close, day=day, surface=None)
    from zoneinfo import ZoneInfo

    now = datetime(2026, 3, 11, 16, 0, tzinfo=ZoneInfo("America/New_York"))
    years = years_to_expiry(lot.expiry, now)
    theo = price_option(
        spot=close,
        strike=lot.strike,
        years=years,
        iv=0.45,
        option_type="call",
    ).price
    assert marked.valuation_mode == "frozen"
    assert marked.upnl == (theo - 5.2) * 100 * 1
    assert marked.model == theo


def test_old_day_does_not_use_current_surface(tmp_path) -> None:
    expiry = date(2026, 6, 18)
    current = IvSurface(
        underlying="HOOD",
        spot=80.0,
        as_of=datetime(2026, 8, 14, 20, tzinfo=timezone.utc),
        source="test",
        smiles=[
            ExpirySmile(
                expiry=expiry,
                nodes=[
                    SurfaceNode(strike=150.0, delta=0.2, iv=0.9, option_type="call"),
                    SurfaceNode(strike=160.0, delta=0.1, iv=0.85, option_type="call"),
                ],
            )
        ],
        quality={"accepted": 2, "rejected": 0},
        session_date=date(2026, 8, 14),
    )
    save_surface(current, tmp_path)
    assert load_surface_for_session("HOOD", date(2026, 3, 11), tmp_path) is None
    lot = Position(
        occ_symbol="HOOD260618C00150000",
        underlying="HOOD",
        expiry=expiry,
        strike=150.0,
        option_type="call",
        qty=1,
        side="long",
        iv_frozen=0.45,
        iv_as_of=datetime(2026, 3, 10, tzinfo=timezone.utc),
        entry_premium=5.2,
    )
    from optionda.marks import HeldLot

    held = HeldLot(
        position_id="hood",
        occ=lot.occ_symbol,
        underlying="HOOD",
        expiry=expiry,
        strike=150.0,
        option_type="call",
        qty=1,
        side="long",
        cost=5.2,
        iv=0.45,
    )
    marked = mark_lot(
        held,
        close=48.0,
        day=date(2026, 3, 11),
        surface=load_surface_for_session("HOOD", date(2026, 3, 11), tmp_path),
    )
    assert marked.valuation_mode == "frozen"
    assert marked.iv == 0.45


def test_mark_series_uses_closes_and_cache(tmp_path) -> None:
    _journal(tmp_path)
    from optionda.analytics import read_events

    events = read_events(log_path("demo", tmp_path))
    table = {
        ("HOOD", date(2026, 3, 10)): 40.0,
        ("HOOD", date(2026, 3, 11)): 48.0,
        ("HOOD", date(2026, 3, 12)): 55.0,
    }
    closer = _closer(table)
    first = build_mark_series(
        "demo",
        tmp_path,
        events=events,
        start=date(2026, 3, 10),
        end=date(2026, 3, 12),
        closer=closer,
    )
    assert closer.calls["n"] == 1
    assert [item.day for item in first] == [
        date(2026, 3, 10),
        date(2026, 3, 11),
        date(2026, 3, 12),
    ]
    assert first[0].open_upnl is not None
    assert first[1].open_upnl is not None
    assert first[2].open_upnl == 0.0
    assert first[2].realized_cum == 225.0
    assert first[2].total == 225.0
    second = build_mark_series(
        "demo",
        tmp_path,
        events=events,
        start=date(2026, 3, 10),
        end=date(2026, 3, 12),
        closer=closer,
    )
    assert closer.calls["n"] == 1
    assert [item.total for item in second] == [item.total for item in first]


def test_close_cache_refetches_when_claimed_range_missing_weekday(tmp_path) -> None:
    from optionda.marks import _merge_close_cache, _resolve_closes

    _merge_close_cache(
        tmp_path,
        "HOOD",
        {date(2026, 8, 14): _close("HOOD", date(2026, 8, 14), 80.0)},
        date(2026, 8, 12),
        date(2026, 8, 17),
    )
    closer = _closer(
        {
            ("HOOD", date(2026, 8, 14)): 80.0,
            ("HOOD", date(2026, 8, 17)): 82.0,
        }
    )
    found = _resolve_closes(
        ["HOOD"],
        date(2026, 8, 12),
        date(2026, 8, 17),
        tmp_path,
        closer,
    )
    assert closer.calls["n"] == 1
    assert found[("HOOD", date(2026, 8, 17))].close == 82.0


def test_build_report_mark_curve_and_charts(tmp_path) -> None:
    _journal(tmp_path)
    table = {
        ("HOOD", date(2026, 3, 10)): 40.0,
        ("HOOD", date(2026, 3, 11)): 48.0,
        ("HOOD", date(2026, 3, 12)): 55.0,
    }
    report = build_report(
        "demo",
        tmp_path,
        period="all",
        as_of=datetime(2026, 3, 12, 22, tzinfo=timezone.utc),
        closer=_closer(table),
    )
    assert report.mark_curve
    assert report.mark_curve[-1][1] == 225.0
    xs, ys = mark_xy(report)
    assert len(xs) >= 2
    assert ys[-1] == 225.0
    px, py = position_mark_xy(report, "hood")
    assert py[-1] == 225.0
    days = {item.day for item in report.calendar}
    assert date(2026, 3, 11) in days
    mid = next(item for item in report.calendar if item.day == date(2026, 3, 11))
    assert mid.mark_delta is not None
