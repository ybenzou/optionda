import json
from datetime import date, datetime, timezone
from unittest.mock import patch

from typer.testing import CliRunner

from optionda.cli import app
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


def test_snapshot_writes_json_and_cached_rereads(tmp_path, monkeypatch) -> None:
    _account(tmp_path, monkeypatch)
    from optionda.market.session import SessionSyncResult

    with (
        patch("optionda.cli.sync_completed_session", return_value=SessionSyncResult()),
        patch("optionda.cli.mark_account", return_value=[_row()]),
    ):
        first = runner.invoke(app, ["snapshot"])
    assert first.exit_code == 0, first.output
    blob = first.output
    start = blob.find("{")
    assert start >= 0
    payload = json.loads(blob[start:])
    assert payload["account"] == "demo"
    assert payload["up"] or payload["down"]
    assert "email" not in payload

    cached = runner.invoke(app, ["snapshot", "--cached"])
    assert cached.exit_code == 0, cached.output
    cstart = cached.output.find("{")
    replay = json.loads(cached.output[cstart:])
    assert replay["account"] == "demo"
    assert replay["n"] == payload["n"]


def test_snapshot_text_is_desk_table(tmp_path, monkeypatch) -> None:
    _account(tmp_path, monkeypatch)
    from optionda.market.session import SessionSyncResult

    with (
        patch("optionda.cli.sync_completed_session", return_value=SessionSyncResult()),
        patch("optionda.cli.mark_account", return_value=[_row()]),
    ):
        result = runner.invoke(app, ["snapshot", "--text"])
    assert result.exit_code == 0, result.output
    assert "today +" in result.output
    assert "AAPL" in result.output
    assert "Model$" in result.output


def test_snapshot_cached_without_file_fails(tmp_path, monkeypatch) -> None:
    _account(tmp_path, monkeypatch)
    result = runner.invoke(app, ["snapshot", "--cached"])
    assert result.exit_code == 1
    assert "latest" in result.output.lower() or "cached" in result.output.lower()
