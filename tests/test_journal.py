from datetime import date, datetime, timezone

from optionda.journal import append_export_log, book_path, log_path, sync_book
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
    )


def test_sync_book_and_append_log(tmp_path) -> None:
    acc = Account(name="demo", positions=[_pos()])
    book = sync_book(acc, tmp_path)
    assert book == book_path("demo", tmp_path)
    text = book.read_text(encoding="utf-8")
    assert "AAPL 261120 350 C" in text
    assert "qty=2" in text

    rows = [
        RowMark(
            position=_pos(),
            spot=200.0,
            theo=12.5,
            delta=0.4,
            dte=100.0,
            notional=2500.0,
        )
    ]
    log = append_export_log(acc, rows, feed="alpaca", home=tmp_path)
    assert log == log_path("demo", tmp_path)
    first = log.read_text(encoding="utf-8")
    assert "AAPL261120C00350000" in first
    assert "Σ Model$" in first

    append_export_log(acc, rows, feed="alpaca", home=tmp_path)
    second = log.read_text(encoding="utf-8")
    assert second.count("export  account=demo") == 2
