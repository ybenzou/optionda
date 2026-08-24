from datetime import date, datetime, timezone

from optionda.display.table import render_snapshot
from optionda.models import Position, RowMark
from optionda.undo import last_operation_times


def test_last_operation_uses_latest_fill() -> None:
    events = [
        {
            "event": "add",
            "id": "abc",
            "occ": "AAPL261120C00350000",
            "ts": "2026-08-20T20:00:00+00:00",
        },
        {
            "event": "merge",
            "id": "abc",
            "occ": "AAPL261120C00350000",
            "ts": "2026-08-21T20:00:00+00:00",
        },
    ]
    times = last_operation_times(events)
    assert times["abc"].astimezone(timezone.utc) == datetime(
        2026, 8, 21, 20, tzinfo=timezone.utc
    )


def test_last_operation_skips_undone_batch() -> None:
    events = [
        {
            "event": "add",
            "id": "abc",
            "occ": "AAPL261120C00350000",
            "ts": "2026-08-20T20:00:00+00:00",
            "batch_id": "first",
        },
        {
            "event": "merge",
            "id": "abc",
            "occ": "AAPL261120C00350000",
            "ts": "2026-08-24T02:35:00+00:00",
            "batch_id": "dup",
        },
        {
            "event": "undo",
            "ts": "2026-08-24T03:00:00+00:00",
            "undone_batch_id": "dup",
            "batch_id": "undo1",
        },
    ]
    times = last_operation_times(events)
    assert times["abc"].isoformat() == "2026-08-20T20:00:00+00:00"


def test_desk_row_shows_last_op_et_date() -> None:
    pos = Position(
        occ_symbol="AAPL261120C00350000",
        underlying="AAPL",
        expiry=date(2026, 11, 20),
        strike=350.0,
        option_type="call",
        qty=1,
        side="long",
        iv_frozen=0.25,
        iv_as_of=datetime(2026, 8, 12, 20, tzinfo=timezone.utc),
        entry_premium=3.5,
    )
    row = RowMark(
        position=pos,
        spot=210.0,
        theo=12.0,
        delta=0.3,
        dte=90.0,
        notional=2400.0,
        cost=3.5,
        upnl=1700.0,
        last_op_at=datetime(2026, 8, 21, 20, tzinfo=timezone.utc),
    )
    group = render_snapshot(
        account="main",
        feed="alpaca",
        refresh_sec=15,
        rows=[row],
        framed=False,
    )
    table = next(
        item for item in group.renderables if getattr(item, "columns", None)
    )
    assert table.columns[-1].header == "Last"
    assert "Delta" not in [col.header for col in table.columns]
    plains = [cell.plain for cell in table.columns[-1].cells]
    assert any("8/21" in text for text in plains)
