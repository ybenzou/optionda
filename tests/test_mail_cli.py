from datetime import date, datetime, timezone
from unittest.mock import patch

from typer.testing import CliRunner

from optionda.cli import app
from optionda.journal import log_path
from optionda.models import Position, RowMark

runner = CliRunner()


def _row() -> RowMark:
    pos = Position(
        occ_symbol="AAPL261120C00350000",
        underlying="AAPL",
        expiry=date(2026, 11, 20),
        strike=350,
        option_type="call",
        qty=1,
        side="long",
        iv_frozen=0.28,
        iv_as_of=datetime.now(timezone.utc),
        entry_premium=5.20,
    )
    return RowMark(
        position=pos,
        spot=210.0,
        theo=18.5,
        delta=0.62,
        dte=120.0,
        notional=3700.0,
        cost=5.20,
        upnl=2660.0,
        close_premium=16.5,
        theo_chg=2.0,
    )


def _account(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("OPTIONDA_HOME", str(tmp_path))
    monkeypatch.setenv("OPTIONDA_ACTIVE", "demo")
    assert runner.invoke(app, ["create", "demo"]).exit_code == 0


def test_mail_without_login_exits(tmp_path, monkeypatch) -> None:
    _account(tmp_path, monkeypatch)
    from optionda.market.session import SessionSyncResult

    with (
        patch("optionda.cli.sync_completed_session", return_value=SessionSyncResult()),
        patch("optionda.cli.mark_account", return_value=[_row()]),
    ):
        result = runner.invoke(app, ["mail"])
    assert result.exit_code == 1
    assert "login" in result.output.lower()


def test_mail_login_list_hides_password(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("OPTIONDA_HOME", str(tmp_path))
    result = runner.invoke(
        app,
        ["mail", "login", "devnull@example.com", "not-a-real-password"],
    )
    assert result.exit_code == 0, result.output
    assert "not-a-real-password" not in result.output
    assert "optionda ·" in result.output
    shown = runner.invoke(app, ["mail", "list"])
    assert shown.exit_code == 0, shown.output
    assert "devnull@example.com" in shown.output
    assert "not-a-real-password" not in shown.output


def test_mail_send_and_journal_omit_secrets(tmp_path, monkeypatch) -> None:
    _account(tmp_path, monkeypatch)
    assert runner.invoke(
        app,
        ["mail", "login", "devnull@example.com", "not-a-real-password"],
    ).exit_code == 0
    from optionda.market.session import SessionSyncResult

    with (
        patch("optionda.cli.sync_completed_session", return_value=SessionSyncResult()),
        patch("optionda.cli.mark_account", return_value=[_row()]),
        patch("optionda.mailer.smtp_send") as smtp,
    ):
        result = runner.invoke(app, ["mail"])
    assert result.exit_code == 0, result.output
    smtp.assert_called_once()
    journal = log_path("demo", tmp_path).read_text(encoding="utf-8")
    assert "not-a-real-password" not in journal
    assert "devnull@example.com" not in journal
    assert "Message-ID" not in journal
    assert '"event": "mail"' in journal or '"event":"mail"' in journal


def test_mail_paused_one_shot_refuses(tmp_path, monkeypatch) -> None:
    _account(tmp_path, monkeypatch)
    assert runner.invoke(
        app,
        ["mail", "login", "devnull@example.com", "not-a-real-password"],
    ).exit_code == 0
    from optionda.mailer import ensure_session

    ensure_session("demo", tmp_path)
    paused = runner.invoke(app, ["mail", "pause"])
    assert paused.exit_code == 0, paused.output
    from optionda.market.session import SessionSyncResult

    with (
        patch("optionda.cli.sync_completed_session", return_value=SessionSyncResult()),
        patch("optionda.cli.mark_account", return_value=[_row()]),
        patch("optionda.mailer.smtp_send") as smtp,
    ):
        result = runner.invoke(app, ["mail"])
    assert result.exit_code == 1
    assert "resume" in result.output.lower()
    smtp.assert_not_called()


def test_mail_delete_default_clears_login(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("OPTIONDA_HOME", str(tmp_path))
    assert runner.invoke(
        app,
        ["mail", "login", "devnull@example.com", "not-a-real-password"],
    ).exit_code == 0
    deleted = runner.invoke(app, ["mail", "delete"])
    assert deleted.exit_code == 0, deleted.output
    shown = runner.invoke(app, ["mail", "list"])
    assert shown.exit_code == 0
    assert "mail  off" in shown.output or "mail  idle" in shown.output


def test_mail_every_detaches_and_returns(tmp_path, monkeypatch) -> None:
    _account(tmp_path, monkeypatch)
    assert runner.invoke(
        app,
        ["mail", "login", "devnull@example.com", "not-a-real-password"],
    ).exit_code == 0

    class FakeProc:
        pid = 4242

    with patch("optionda.cli.spawn_mail_every", return_value=FakeProc()) as spawn:
        result = runner.invoke(app, ["mail", "--every", "30"])
    assert result.exit_code == 0, result.output
    spawn.assert_called_once()
    assert "started" in result.output.lower()
    assert "4242" in result.output
    assert "next" in result.output.lower()


def test_window_mints_session_token(tmp_path, qtbot, monkeypatch) -> None:
    monkeypatch.setenv("OPTIONDA_HOME", str(tmp_path))
    from optionda.gui.main_window import MainWindow
    from optionda.mailer import load_session
    from optionda.store import AccountStore

    store = AccountStore(tmp_path)
    store.create("main")
    store.activate("main")
    win = MainWindow("main", tmp_path)
    qtbot.addWidget(win)
    session = load_session(tmp_path)
    assert session is not None
    assert len(session.token) == 32
    assert session.subject.startswith("optionda · main ·")


def test_update_current_prints_ok(tmp_path, monkeypatch) -> None:
    from optionda import __version__

    monkeypatch.setenv("OPTIONDA_HOME", str(tmp_path))
    monkeypatch.setattr("optionda.cli.fetch_pypi_version", lambda: __version__)
    result = runner.invoke(app, ["update"])
    assert result.exit_code == 0, result.output
    assert "ok" in result.output
    assert __version__ in result.output


def test_update_newer_installs(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("OPTIONDA_HOME", str(tmp_path))
    monkeypatch.setattr("optionda.cli.fetch_pypi_version", lambda: "9.9.9")
    called: list[str] = []

    def fake_upgrade(latest: str) -> None:
        called.append(latest)

    monkeypatch.setattr("optionda.cli.run_upgrade", fake_upgrade)
    result = runner.invoke(app, ["update"])
    assert result.exit_code == 0, result.output
    assert called == ["9.9.9"]
    assert "9.9.9" in result.output
