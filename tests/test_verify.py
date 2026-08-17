from datetime import date, datetime, timezone
from pathlib import Path

from optionda.backtest import journal_rows
from optionda.engine import attach_live_option_mids, mark_account
from optionda.journal import append_export_log, append_verify_log
from optionda.models import Account, Position, RowMark, SpotQuote


def _position() -> Position:
    return Position(
        occ_symbol="AAPL261120C00350000",
        underlying="AAPL",
        expiry=date(2026, 11, 20),
        strike=350.0,
        option_type="call",
        iv_frozen=0.28,
        iv_as_of=datetime(2026, 8, 14, 20, 0, tzinfo=timezone.utc),
        entry_premium=5.0,
    )


def test_verify_attaches_live_mids_without_changing_model(tmp_path) -> None:
    now = datetime(2026, 8, 14, 21, 0, tzinfo=timezone.utc)
    position = _position()

    class Router:
        def get_spots(self, _symbols):
            return {
                "AAPL": SpotQuote(symbol="AAPL", price=230.0, as_of=now, source="live")
            }

        def get_option_mid(self, occ):
            assert occ == position.occ_symbol
            return 7.25

    account = Account(name="demo", positions=[position])
    rows = mark_account(account, home=tmp_path, router=Router(), now=now)
    assert rows[0].live is None
    model = rows[0].theo
    events: list[tuple[str, int, int]] = []
    verified = attach_live_option_mids(
        rows,
        router=Router(),
        on_progress=lambda label, done, total: events.append((label, done, total)),
        phase_index=3,
        phase_count=3,
    )
    assert verified[0].live == 7.25
    assert verified[0].theo == model
    assert events[0][0].startswith("3/3 live")
    assert all(total == 1 for _, _, total in events)


def test_backtest_prefers_verify_journal_and_reads_legacy(tmp_path: Path) -> None:
    account = Account(name="demo", positions=[_position()])
    pos = _position()
    legacy = RowMark(
        position=pos,
        spot=230.0,
        theo=6.0,
        delta=0.4,
        dte=90.0,
        notional=600.0,
        live=5.5,
    )
    verify = RowMark(
        position=pos,
        spot=231.0,
        theo=6.2,
        delta=0.4,
        dte=90.0,
        notional=620.0,
        live=6.1,
        model_iv=0.28,
        surface_session_date=date(2026, 8, 14),
    )
    append_export_log(account, [legacy], feed="alpaca", home=tmp_path, source="run")
    only_legacy = journal_rows(tmp_path / "logs" / "demo.jsonl")
    assert only_legacy[0]["live"] == 5.5
    append_verify_log(account, [verify], feed="alpaca", home=tmp_path)
    preferred = journal_rows(tmp_path / "logs" / "demo.jsonl")
    assert len(preferred) == 1
    assert preferred[0]["live"] == 6.1
    assert preferred[0]["model_iv"] == 0.28
