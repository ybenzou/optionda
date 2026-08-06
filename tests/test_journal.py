import json
from datetime import date, datetime, timezone

from optionda.journal import (
    append_export_log,
    append_refresh_iv_event,
    book_path,
    log_path,
    sync_book,
)
from optionda.models import Account, Position, RowMark


def _pos() -> Position:
    return Position(
        occ_symbol="AAPL261120C00350000",
        underlying="AAPL",
        expiry=date(2026, 11, 20),
        strike=350,
        option_type="call",
        qty=2,
        side="long",
        iv_frozen=0.28,
        iv_as_of=datetime.now(timezone.utc),
        entry_premium=10.0,
    )


def test_sync_book_and_append_log(tmp_path) -> None:
    acc = Account(name="demo", positions=[_pos()])
    book = sync_book(acc, tmp_path)
    assert book == book_path("demo", tmp_path)
    text = book.read_text(encoding="utf-8")
    assert "AAPL 261120 350 C @ 10" in text
    assert "qty=2" in text

    rows = [
        RowMark(
            position=_pos(),
            spot=200.0,
            theo=12.5,
            delta=0.4,
            dte=100.0,
            notional=2500.0,
            cost=10.0,
            upnl=500.0,
        )
    ]
    log = append_export_log(acc, rows, feed="alpaca", home=tmp_path)
    assert log == log_path("demo", tmp_path)
    assert log.suffix == ".jsonl"
    first = log.read_text(encoding="utf-8").strip().splitlines()
    assert len(first) == 1
    event = json.loads(first[0])
    assert event["event"] == "export"
    assert event["account"] == "demo"
    assert event["sum_model"] == 2500.0
    assert event["sum_upnl"] == 500.0
    assert event["rows"][0]["occ"] == "AAPL261120C00350000"
    assert event["rows"][0]["valuation_mode"] == "frozen"

    append_export_log(acc, rows, feed="alpaca", home=tmp_path)
    second = log.read_text(encoding="utf-8").strip().splitlines()
    assert len(second) == 2

    append_export_log(acc, rows, feed="alpaca", home=tmp_path, source="run")
    third = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    assert third[-1]["event"] == "run"

    append_refresh_iv_event(
        acc,
        home=tmp_path,
        surfaces=[
            {
                "underlying": "AAPL",
                "as_of": "2026-08-04T20:00:00+00:00",
                "source": "alpaca/chain",
                "accepted": 20,
                "rejected": 4,
            }
        ],
    )
    events = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    assert events[-1]["event"] == "refresh_iv"
    assert events[-1]["surfaces"][0]["underlying"] == "AAPL"
