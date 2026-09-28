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


def test_ledger_keeps_write_order_ahead_of_timestamp(tmp_path) -> None:
    from optionda.journal import migrate_ledger, read_ledger_events

    path = log_path("demo", tmp_path)
    path.write_text(
        "\n".join(
            [
                '{"ts":"2026-09-08T20:00:00+00:00","event":"sell","id":"a","occ":"MSFT270319C00600000"}',
                '{"ts":"2026-09-04T20:00:00+00:00","event":"add","id":"a","occ":"MSFT270319C00600000"}',
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    assert migrate_ledger("demo", tmp_path) == 2
    events = read_ledger_events(path)
    assert [event["event"] for event in events] == ["sell", "add"]
    assert events[1]["ts"].startswith("2026-09-04")
    assert not path.exists()
    archive = tmp_path / "logs" / "demo.ledger.archive.jsonl"
    assert archive.exists()
    assert migrate_ledger("demo", tmp_path) == 0


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
    from optionda.quotes import quote_count, quote_db_path

    saved = append_export_log(acc, rows, feed="alpaca", home=tmp_path)
    assert saved == quote_db_path("demo", tmp_path)
    assert quote_count("demo", tmp_path) == 1
    import sqlite3

    def sources() -> list[str]:
        conn = sqlite3.connect(saved)
        try:
            return [
                row[0]
                for row in conn.execute("SELECT source FROM quotes ORDER BY rowid")
            ]
        finally:
            conn.close()

    assert sources() == ["export"]
    conn = sqlite3.connect(saved)
    try:
        row = conn.execute(
            "SELECT occ, model, upnl, notional, valuation_mode FROM latest"
        ).fetchone()
    finally:
        conn.close()
    assert row[0] == "AAPL261120C00350000"
    assert row[1] == 12.5
    assert row[2] == 500.0
    assert row[3] == 2500.0
    assert row[4] == "frozen"

    append_export_log(acc, rows, feed="alpaca", home=tmp_path)
    assert quote_count("demo", tmp_path) == 2

    append_export_log(acc, rows, feed="alpaca", home=tmp_path, source="run")
    assert sources()[-1] == "run"

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
    from optionda.journal import read_ledger_events

    events = read_ledger_events(log_path("demo", tmp_path))
    assert events[-1]["event"] == "refresh_iv"
    assert events[-1]["surfaces"][0]["underlying"] == "AAPL"
