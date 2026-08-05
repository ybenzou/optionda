from datetime import date, datetime, timezone
from unittest.mock import patch

from typer.testing import CliRunner

from optionda.cli import app
from optionda.models import OptionIvQuote, SpotQuote

runner = CliRunner()


def test_cli_create_add_export(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("OPTIONDA_HOME", str(tmp_path))

    result = runner.invoke(app, ["create", "demo"])
    assert result.exit_code == 0, result.output

    iv_quote = OptionIvQuote(
        occ_symbol="AAPL250117C00200000",
        iv=0.28,
        as_of=datetime.now(timezone.utc),
        source="mock",
    )
    spots = {
        "AAPL": SpotQuote(symbol="AAPL", price=210.0, source="mock"),
    }

    with (
        patch("optionda.engine.MarketRouter") as router_cls,
        patch("optionda.cli.MarketRouter") as cli_router_cls,
    ):
        router = router_cls.return_value
        router.get_option_iv.return_value = iv_quote
        router.get_spots.return_value = spots
        router.feed_name = "yahoo"

        cli_router = cli_router_cls.return_value
        cli_router.get_option_iv.return_value = iv_quote
        cli_router.get_spots.return_value = spots
        cli_router.feed_name = "yahoo"

        # freeze_iv uses MarketRouter(home) inside engine — patch there
        with patch("optionda.cli.freeze_iv_for_position", side_effect=lambda p, **kw: p.model_copy(update={"iv_frozen": 0.28})):
            add = runner.invoke(
                app,
                ["add", "AAPL250117C00200000", "--qty", "2", "--iv", "0.28"],
            )
        assert add.exit_code == 0, add.output

        with patch("optionda.cli.mark_account") as mark:
            from optionda.models import Position, RowMark

            pos = Position(
                occ_symbol="AAPL250117C00200000",
                underlying="AAPL",
                expiry=date(2025, 1, 17),
                strike=200,
                option_type="call",
                qty=2,
                side="long",
                iv_frozen=0.28,
                iv_as_of=datetime.now(timezone.utc),
            )
            mark.return_value = [
                RowMark(
                    position=pos,
                    spot=210.0,
                    theo=18.5,
                    delta=0.62,
                    dte=120.0,
                    notional=3700.0,
                )
            ]
            exported = runner.invoke(app, ["export"])
            assert exported.exit_code == 0, exported.output
            assert "AAPL250117C00200000" in exported.output
            assert "MODEL" in exported.output


def test_key_status(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("OPTIONDA_HOME", str(tmp_path))
    result = runner.invoke(app, ["key", "alpaca", "PK", "SEC"])
    assert result.exit_code == 0, result.output
    status = runner.invoke(app, ["key", "status"])
    assert status.exit_code == 0
    assert "alpaca=configured" in status.output
    assert "poll_interval_sec=15" in status.output
