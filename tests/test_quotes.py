import sqlite3
from datetime import date, datetime, timezone

from optionda.models import Position, RowMark
from optionda.quotes import load_latest_rows, quote_count, quote_db_path, save_quotes


def _row(*, spot: float, model: float, pid: str = "msft1") -> RowMark:
    return RowMark(
        position=Position(
            id=pid,
            occ_symbol="MSFT270319C00600000",
            underlying="MSFT",
            expiry=date(2027, 3, 19),
            strike=600.0,
            option_type="call",
            qty=1,
            side="long",
            iv_frozen=0.30,
            iv_as_of=datetime(2026, 9, 25, 20, tzinfo=timezone.utc),
            entry_premium=12.4,
        ),
        spot=spot,
        theo=model,
        delta=0.4,
        dte=170.0,
        notional=model * 100,
        cost=12.4,
        upnl=(model - 12.4) * 100,
        valuation_mode="surface",
        model_iv=0.28,
        close_spot=510.0,
        close_premium=16.0,
        spot_source="alpaca",
        last_op_at=datetime(2026, 9, 21, 20, tzinfo=timezone.utc),
    )


def test_quotes_keep_latest_and_history(tmp_path) -> None:
    when = datetime(2026, 9, 25, 20, tzinfo=timezone.utc)
    save_quotes("main", [_row(spot=500.0, model=18.0)], home=tmp_path, ts=when)
    save_quotes(
        "main",
        [_row(spot=516.0, model=18.77)],
        home=tmp_path,
        ts=when.replace(hour=21),
    )
    latest = load_latest_rows("main", tmp_path)
    assert len(latest) == 1
    assert latest[0].position.occ_symbol == "MSFT270319C00600000"
    assert latest[0].spot == 516.0
    assert latest[0].theo == 18.77
    assert latest[0].cost == 12.4
    assert latest[0].valuation_mode == "surface"
    assert latest[0].spot_source == "alpaca"
    assert quote_count("main", tmp_path) == 2
    conn = sqlite3.connect(quote_db_path("main", tmp_path))
    pnl = conn.execute("SELECT pnl_pct FROM latest").fetchone()[0]
    conn.close()
    assert pnl == (18.77 - 12.4) / 12.4 * 100


def test_empty_book_clears_latest_and_keeps_history(tmp_path) -> None:
    save_quotes("main", [_row(spot=500.0, model=18.0)], home=tmp_path)
    save_quotes("main", [], home=tmp_path)
    assert load_latest_rows("main", tmp_path) == []
    assert quote_count("main", tmp_path) == 1


def test_journal_snapshots_move_into_sqlite(tmp_path) -> None:
    from optionda.analytics import build_report
    from optionda.journal import log_path
    from optionda.quotes import migrate_journal_quotes

    path = log_path("demo", tmp_path)
    add = (
        '{"ts":"2026-09-01T20:00:00+00:00","account":"demo","event":"add",'
        '"id":"a1","occ":"MSFT270319C00600000"}\n'
    )
    run = (
        '{"ts":"2026-09-25T20:00:00+00:00","account":"demo","event":"run",'
        '"sum_upnl":637,"n":1,"rows":[{"occ":"MSFT270319C00600000","side":"long",'
        '"qty":1,"spot":516,"iv":0.3,"model":18.77,"cost":12.4,"upnl":637,'
        '"notional":1877,"dte":170}]}\n'
    )
    path.write_text(add + run, encoding="utf-8")
    inserted = migrate_journal_quotes("demo", tmp_path)
    assert inserted == 1
    latest = load_latest_rows("demo", tmp_path)
    assert latest[0].spot == 516
    assert latest[0].theo == 18.77
    assert latest[0].upnl == 637
    assert path.read_text(encoding="utf-8") == add
    archive = next((tmp_path / "logs").glob("demo.archive*.jsonl"))
    assert b'"event":"run"' in archive.read_bytes()
    assert migrate_journal_quotes("demo", tmp_path) == 0
    assert quote_count("demo", tmp_path) == 1
    report = build_report("demo", tmp_path, period="all", with_marks=False)
    assert report.open_upnl == 637
    assert report.book.rows[0]["occ"] == "MSFT270319C00600000"


def test_saved_quotes_fill_the_live_pane(tmp_path, qtbot, monkeypatch) -> None:
    from optionda.gui.main_window import MainWindow

    monkeypatch.setenv("OPTIONDA_ACTIVE", "demo")
    save_quotes("demo", [_row(spot=516.0, model=18.77)], home=tmp_path)
    window = MainWindow("demo", tmp_path, period="all", initial_view="term")
    qtbot.addWidget(window)
    window.resize(1100, 700)
    window.show()
    assert window._show_saved_quotes() is True
    text = window.terminal.live.toPlainText()
    assert "MSFT270319C0060" in text
    assert "516.00" in text
    assert "12.40" in text
    assert "Getting the latest marks." not in text
