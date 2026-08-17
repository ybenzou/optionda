from datetime import date, datetime, timezone
from pathlib import Path

from optionda.analytics import (
    build_report,
    et_date,
    month_cells,
    parse_ts,
    read_events,
)
from optionda.journal import log_path


def _write_jsonl(path: Path, events: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(
            __import__("json").dumps(event, separators=(",", ":")) + "\n"
            for event in events
        ),
        encoding="utf-8",
    )


def _journal(tmp_path: Path, events: list[dict]) -> Path:
    path = log_path("demo", tmp_path)
    _write_jsonl(path, events)
    return path


def test_et_date_rolls_back_before_midnight_eastern() -> None:
    # 03:00 UTC on Mar 15 is still Mar 14 in America/New_York.
    assert et_date("2026-03-15T03:00:00+00:00") == date(2026, 3, 14)
    assert et_date("2026-03-15T12:00:00+00:00") == date(2026, 3, 15)


def test_parse_ts_accepts_z_and_naive() -> None:
    z = parse_ts("2026-03-15T16:00:00Z")
    naive = parse_ts("2026-03-15T16:00:00")
    assert z is not None and naive is not None
    assert z == naive


def test_win_rate_partial_sells_and_delete_excluded(tmp_path: Path) -> None:
    events = [
        {
            "ts": "2026-01-05T15:00:00+00:00",
            "event": "add",
            "id": "win1",
            "occ": "HOOD260618C00150000",
            "side": "long",
            "qty_added": 1,
            "dte_at_entry": 160,
        },
        {
            "ts": "2026-01-06T15:00:00+00:00",
            "event": "add",
            "id": "loss1",
            "occ": "AAPL260618C00200000",
            "side": "long",
            "qty_added": 1,
            "dte_at_entry": 160,
        },
        {
            "ts": "2026-01-07T15:00:00+00:00",
            "event": "add",
            "id": "gone",
            "occ": "TSLA260618C00300000",
            "side": "long",
            "qty_added": 1,
        },
        {
            "ts": "2026-01-08T15:00:00+00:00",
            "event": "delete",
            "removed": [{"id": "gone", "occ": "TSLA260618C00300000"}],
        },
        # Partial then close: +200 then -50 → lot +150 (win), two sell events (1 win 1 loss)
        {
            "ts": "2026-02-10T18:00:00+00:00",
            "event": "sell",
            "id": "win1",
            "occ": "HOOD260618C00150000",
            "realized": 200.0,
            "closed": False,
            "hold_days": 36,
        },
        {
            "ts": "2026-02-20T18:00:00+00:00",
            "event": "sell",
            "id": "win1",
            "occ": "HOOD260618C00150000",
            "realized": -50.0,
            "closed": True,
            "hold_days": 46,
        },
        {
            "ts": "2026-02-21T18:00:00+00:00",
            "event": "sell",
            "id": "loss1",
            "occ": "AAPL260618C00200000",
            "realized": -80.0,
            "closed": True,
            "hold_days": 46,
        },
    ]
    _journal(tmp_path, events)
    report = build_report(
        "demo",
        tmp_path,
        period="all",
        as_of=datetime(2026, 3, 1, tzinfo=timezone.utc),
    )
    assert report.n_sells == 3
    assert report.n_closed == 2
    assert report.realized == 70.0
    assert report.lot_win.wins == 1
    assert report.lot_win.total == 2
    assert report.sell_win.wins == 1
    assert report.sell_win.total == 3
    assert report.n_deletes == 1
    assert report.behavior.n_deletes == 1
    hood = next(item for item in report.by_ticker if item.key == "HOOD")
    assert hood.n_closed == 1
    assert hood.lot_wins == 1
    assert hood.realized == 150.0
    closed = {lot.position_id: lot for lot in report.closed_lots}
    assert closed["win1"].realized == 150.0
    assert closed["win1"].n_sells == 2


def test_calendar_uses_et_sell_dates_only(tmp_path: Path) -> None:
    events = [
        {
            "ts": "2026-03-15T03:00:00+00:00",
            "event": "sell",
            "id": "a",
            "occ": "HOOD260618C00150000",
            "realized": 120.0,
            "closed": True,
        },
        {
            "ts": "2026-03-16T18:00:00+00:00",
            "event": "run",
            "sum_upnl": 999.0,
            "n": 1,
            "rows": [{"occ": "HOOD260618C00150000", "upnl": 999.0, "dte": 90, "qty": 1}],
        },
    ]
    _journal(tmp_path, events)
    report = build_report(
        "demo",
        tmp_path,
        period="all",
        as_of=datetime(2026, 3, 20, tzinfo=timezone.utc),
    )
    days = {item.day: item.realized for item in report.calendar}
    assert date(2026, 3, 14) in days
    assert days[date(2026, 3, 14)] == 120.0
    assert date(2026, 3, 16) not in days
    assert report.open_upnl == 999.0
    assert report.cumulative[-1][1] == 120.0


def test_period_filter_drops_older_sells(tmp_path: Path) -> None:
    events = [
        {
            "ts": "2026-01-10T18:00:00+00:00",
            "event": "sell",
            "id": "old",
            "occ": "IBM260618C00200000",
            "realized": 500.0,
            "closed": True,
        },
        {
            "ts": "2026-03-10T18:00:00+00:00",
            "event": "sell",
            "id": "new",
            "occ": "HOOD260618C00150000",
            "realized": 50.0,
            "closed": True,
        },
    ]
    _journal(tmp_path, events)
    as_of = datetime(2026, 3, 15, tzinfo=timezone.utc)
    month = build_report("demo", tmp_path, period="1m", as_of=as_of)
    everything = build_report("demo", tmp_path, period="all", as_of=as_of)
    assert month.realized == 50.0
    assert month.n_closed == 1
    assert everything.realized == 550.0
    assert everything.n_closed == 2


def test_habits_without_sells(tmp_path: Path) -> None:
    events = [
        {
            "ts": "2026-03-01T15:00:00+00:00",
            "event": "add",
            "id": "c1",
            "occ": "HOOD261218C00150000",
            "side": "long",
            "qty_added": 2,
            "dte_at_entry": 280,
        },
        {
            "ts": "2026-03-02T15:00:00+00:00",
            "event": "add",
            "id": "p1",
            "occ": "AAPL261218P00200000",
            "side": "short",
            "qty_added": 1,
            "dte_at_entry": 20,
        },
        {
            "ts": "2026-03-03T15:00:00+00:00",
            "event": "export",
            "sum_upnl": 1554.0,
            "sum_model": 3200.0,
            "n": 2,
            "rows": [
                {
                    "occ": "HOOD261218C00150000",
                    "qty": 2,
                    "upnl": 1000.0,
                    "notional": 2000.0,
                    "dte": 270,
                    "side": "long",
                },
                {
                    "occ": "AAPL261218P00200000",
                    "qty": 1,
                    "upnl": 554.0,
                    "notional": 800.0,
                    "dte": 18,
                    "side": "short",
                },
            ],
        },
    ]
    _journal(tmp_path, events)
    report = build_report(
        "demo",
        tmp_path,
        period="all",
        as_of=datetime(2026, 3, 10, tzinfo=timezone.utc),
    )
    assert report.n_sells == 0
    assert report.lot_win.total == 0
    assert report.behavior.call_qty == 2
    assert report.behavior.put_qty == 1
    assert report.behavior.long_qty == 2
    assert report.behavior.short_qty == 1
    assert report.behavior.dte_buckets["91+"] == 1
    assert report.behavior.dte_buckets["8-30"] == 1
    assert report.behavior.by_ticker[0][0] == "HOOD"
    assert report.open_upnl == 1554.0
    assert report.book.avg_dte == 144.0
    assert len(report.open_lots) == 2
    assert report.calendar == []


def test_old_events_without_new_fields_still_infer(tmp_path: Path) -> None:
    events = [
        {
            "ts": "2026-01-01T15:00:00+00:00",
            "event": "add",
            "id": "legacy",
            "occ": "CSCO260401C00130000",
            "side": "long",
            "qty_added": 1,
        },
        {
            "ts": "2026-01-21T15:00:00+00:00",
            "event": "sell",
            "id": "legacy",
            "occ": "CSCO260401C00130000",
            "realized": 40.0,
            "closed": True,
        },
    ]
    _journal(tmp_path, events)
    report = build_report(
        "demo",
        tmp_path,
        period="all",
        as_of=datetime(2026, 2, 1, tzinfo=timezone.utc),
    )
    lot = report.closed_lots[0]
    assert lot.hold_days == 20.0
    assert lot.realized == 40.0
    entry = report.behavior
    assert entry.avg_entry_dte is not None


def test_month_cells_sunday_first() -> None:
    cells = month_cells(2026, 3)
    assert len(cells) == 42
    # 1 Mar 2026 is Sunday
    assert cells[0] == date(2026, 3, 1)
    assert cells[30] == date(2026, 3, 31)


def test_read_events_skips_bad_lines(tmp_path: Path) -> None:
    path = tmp_path / "x.jsonl"
    path.write_text("{not json\n{\"event\":\"sell\",\"ts\":\"2026-01-01T00:00:00+00:00\"}\n", encoding="utf-8")
    events = read_events(path)
    assert len(events) == 1
