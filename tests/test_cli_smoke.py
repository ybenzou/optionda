from datetime import date, datetime, timezone
from unittest.mock import patch

from typer.testing import CliRunner

from optionda.cli import app
from optionda.models import OptionIvQuote, SpotQuote
from optionda.pricing.surface import IvSurface, save_surface

runner = CliRunner()


def test_export_blocked_without_activate(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("OPTIONDA_HOME", str(tmp_path))
    monkeypatch.delenv("OPTIONDA_ACTIVE", raising=False)
    assert runner.invoke(app, ["create", "demo"]).exit_code == 0
    blocked = runner.invoke(app, ["export"])
    assert blocked.exit_code == 1
    assert "activate" in blocked.output.lower()


def test_book_shows_positions(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("OPTIONDA_HOME", str(tmp_path))
    monkeypatch.setenv("OPTIONDA_ACTIVE", "demo")
    assert runner.invoke(app, ["create", "demo"]).exit_code == 0

    empty = runner.invoke(app, ["book"])
    assert empty.exit_code == 0, empty.output
    assert "empty book" in empty.output

    with patch(
        "optionda.cli.freeze_iv_for_position",
        side_effect=lambda p, **kw: p.model_copy(update={"iv_frozen": 0.28}),
    ):
        add = runner.invoke(
            app,
            ["add", "AAPL250117C00200000", "--qty", "2", "--iv", "0.28", "--entry", "5.2"],
        )
    assert add.exit_code == 0, add.output

    shown = runner.invoke(app, ["book"])
    assert shown.exit_code == 0, shown.output
    assert "AAPL250117C00200000" in shown.output
    assert "5.2" in shown.output


def test_book_shows_surface_metadata(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("OPTIONDA_HOME", str(tmp_path))
    monkeypatch.setenv("OPTIONDA_ACTIVE", "demo")
    assert runner.invoke(app, ["create", "demo"]).exit_code == 0
    save_surface(
        IvSurface(
            underlying="SPCX",
            spot=116.0,
            as_of=datetime(2026, 8, 4, 20, 0, tzinfo=timezone.utc),
            source="alpaca/chain",
            smiles=[],
            quality={"accepted": 20, "rejected": 4},
        ),
        tmp_path,
    )

    shown = runner.invoke(app, ["book"])

    assert shown.exit_code == 0, shown.output
    assert "surface SPCX" in shown.output
    assert "accepted=20" in shown.output


def test_refresh_iv_calibrates_surface(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("OPTIONDA_HOME", str(tmp_path))
    monkeypatch.setenv("OPTIONDA_ACTIVE", "demo")
    assert runner.invoke(app, ["create", "demo"]).exit_code == 0

    from optionda.market.session import MarketSession, SessionSyncResult
    from optionda.pricing.surface import ExpirySmile, IvSurface, SurfaceNode
    from datetime import date, datetime, timezone
    from zoneinfo import ZoneInfo

    surface = IvSurface(
        underlying="AAPL",
        spot=210.0,
        as_of=datetime.now(timezone.utc),
        source="alpaca/chain",
        smiles=[
            ExpirySmile(
                expiry=date(2026, 11, 20),
                nodes=[SurfaceNode(strike=350, delta=0.25, iv=0.28)],
            )
        ],
        quality={"accepted": 1, "rejected": 0},
        session_date=date(2026, 8, 14),
    )
    et = ZoneInfo("America/New_York")
    session = MarketSession(
        session_date=date(2026, 8, 14),
        open_at=datetime(2026, 8, 14, 9, 30, tzinfo=et),
        close_at=datetime(2026, 8, 14, 16, 0, tzinfo=et),
    )

    class FakeRouter:
        feed_name = "alpaca"

        def get_spots(self, symbols):
            return {}

    with (
        patch("optionda.cli.MarketRouter", return_value=FakeRouter()),
        patch(
            "optionda.cli.sync_completed_session",
            return_value=SessionSyncResult(
                completed_session=session,
                surfaces_saved={"AAPL": surface},
            ),
        ) as sync,
    ):
        result = runner.invoke(app, ["refresh-iv"])

    assert result.exit_code == 0, result.output
    sync.assert_called_once()
    assert sync.call_args.kwargs["force"] is False
    assert "session" in result.output.lower()


def test_refresh_iv_fresh_uses_tight_age(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("OPTIONDA_HOME", str(tmp_path))
    monkeypatch.setenv("OPTIONDA_ACTIVE", "demo")
    assert runner.invoke(app, ["create", "demo"]).exit_code == 0

    from optionda.market.session import SessionSyncResult

    class FakeRouter:
        feed_name = "alpaca"

        def get_spots(self, symbols):
            return {}

    with (
        patch("optionda.cli.MarketRouter", return_value=FakeRouter()),
        patch(
            "optionda.cli.sync_completed_session",
            return_value=SessionSyncResult(errors={"AAPL": "stale"}),
        ) as sync,
    ):
        result = runner.invoke(app, ["refresh-iv", "--fresh"])

    assert result.exit_code == 1, result.output
    assert sync.call_args.kwargs["force"] is True
    assert sync.call_args.kwargs["fresh"] is True
    assert "fresh" in result.output.lower()


def test_refresh_iv_all_stale_hints_retry(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("OPTIONDA_HOME", str(tmp_path))
    monkeypatch.setenv("OPTIONDA_ACTIVE", "demo")
    assert runner.invoke(app, ["create", "demo"]).exit_code == 0

    from optionda.market.session import SessionSyncResult

    class FakeRouter:
        feed_name = "alpaca"

        def get_spots(self, symbols):
            return {}

    with (
        patch("optionda.cli.MarketRouter", return_value=FakeRouter()),
        patch(
            "optionda.cli.sync_completed_session",
            return_value=SessionSyncResult(
                errors={"AAPL": "no usable surface nodes"},
            ),
        ),
    ):
        result = runner.invoke(app, ["refresh-iv", "--fresh"])

    assert result.exit_code == 1, result.output
    assert "no surfaces calibrated" in result.output.lower()


def test_cli_create_add_export(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("OPTIONDA_HOME", str(tmp_path))
    monkeypatch.setenv("OPTIONDA_ACTIVE", "demo")

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
                [
                    "add",
                    "AAPL250117C00200000",
                    "--qty",
                    "2",
                    "--iv",
                    "0.28",
                    "--entry",
                    "5.20",
                ],
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
                entry_premium=5.20,
            )
            mark.return_value = [
                RowMark(
                    position=pos,
                    spot=210.0,
                    theo=18.5,
                    delta=0.62,
                    dte=120.0,
                    notional=3700.0,
                    cost=5.20,
                    upnl=2660.0,
                )
            ]
            exported = runner.invoke(app, ["export"])
            assert exported.exit_code == 0, exported.output
            # Rich box chars can mangle under some Windows consoles; assert stable tokens.
            out = exported.output
            # Rich box drawing can mangle under narrow/legacy CliRunner consoles;
            # assert stable identity tokens that survive truncation.
            assert "demo" in out
            assert "optionda" in out
            assert "AAPL" in out
            assert "IVsrc" not in out
            assert "Chg$" not in out


def test_paint_live_forces_refresh() -> None:
    from unittest.mock import MagicMock

    from optionda.cli import _paint_live

    live = MagicMock()
    _paint_live(live, "desk")
    live.update.assert_called_once_with("desk", refresh=True)


def test_run_ctrl_c_exits_without_nameerror(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("OPTIONDA_HOME", str(tmp_path))
    monkeypatch.setenv("OPTIONDA_ACTIVE", "demo")
    assert runner.invoke(app, ["create", "demo"]).exit_code == 0
    with patch("optionda.cli.mark_account", return_value=[]), patch(
        "optionda.cli.time.sleep", side_effect=KeyboardInterrupt
    ):
        result = runner.invoke(app, ["run"])
    text = f"{result.output or ''}\n{result.exception!r}"
    assert "NameError" not in text
    assert "log_file" not in text
    assert "stopped" in (result.output or "").lower()
    assert result.exception is None or isinstance(result.exception, SystemExit)


def test_add_one_liner_mixes_buy_and_sell(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("OPTIONDA_HOME", str(tmp_path))
    monkeypatch.setenv("OPTIONDA_ACTIVE", "demo")
    assert runner.invoke(app, ["create", "demo"]).exit_code == 0

    with patch(
        "optionda.cli.freeze_iv_for_position",
        side_effect=lambda p, **kw: p.model_copy(update={"iv_frozen": 0.40}),
    ), patch(
        "optionda.batch.freeze_iv_for_position",
        side_effect=lambda p, **kw: p.model_copy(update={"iv_frozen": 0.40}),
    ):
        seed = runner.invoke(
            app,
            ["add", "SKHY 261016 200 C x6 @ 9.5", "--iv", "0.40"],
        )
        assert seed.exit_code == 0, seed.output
        mixed = runner.invoke(
            app,
            [
                "add",
                "AAPL 261120 350 C x1 @ 3.4; "
                "sell SKHY 261016 200 C x6 @ 7.3",
                "--iv",
                "0.40",
            ],
        )
    assert mixed.exit_code == 0, mixed.output
    assert "AAPL261120C00350000" in mixed.output
    assert "SKHY261016C00200000" in mixed.output
    assert "sold" in mixed.output.lower() or "sell" in mixed.output.lower()

    book = runner.invoke(app, ["book"])
    assert book.exit_code == 0, book.output
    assert "AAPL261120C00350000" in book.output
    assert "SKHY261016C00200000" not in book.output


def test_key_status(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("OPTIONDA_HOME", str(tmp_path))
    with patch("optionda.cli.AlpacaClient") as client_cls:
        client_cls.return_value.verify.return_value = "verified (SPY last=500.00)"
        result = runner.invoke(app, ["key", "alpaca", "PK", "SEC"])
        assert result.exit_code == 0, result.output
        assert "verified" in result.output
        status = runner.invoke(app, ["key", "status"])
    assert status.exit_code == 0, status.output
    assert "alpaca=configured" in status.output
    assert "poll_interval_sec=15" in status.output
    assert "check=verified" in status.output


def test_key_rejects_bad_credentials(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("OPTIONDA_HOME", str(tmp_path))
    with patch("optionda.cli.AlpacaClient") as client_cls:
        from optionda.market.alpaca import AlpacaError

        client_cls.return_value.verify.side_effect = AlpacaError(
            "credentials rejected by Alpaca (HTTP 403)"
        )
        result = runner.invoke(app, ["key", "alpaca", "BAD", "SECRET"])
    assert result.exit_code == 1
    assert "not saved" in result.output


def test_verify_fetches_option_mids(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("OPTIONDA_HOME", str(tmp_path))
    monkeypatch.setenv("OPTIONDA_ACTIVE", "demo")
    assert runner.invoke(app, ["create", "demo"]).exit_code == 0

    from optionda.market.session import SessionSyncResult
    from optionda.models import Position, RowMark

    pos = Position(
        occ_symbol="AAPL261120C00350000",
        underlying="AAPL",
        expiry=date(2026, 11, 20),
        strike=350,
        option_type="call",
        iv_frozen=0.28,
        iv_as_of=datetime.now(timezone.utc),
        entry_premium=5.20,
    )
    row = RowMark(
        position=pos,
        spot=210.0,
        theo=18.5,
        delta=0.62,
        dte=120.0,
        notional=3700.0,
        cost=5.20,
        upnl=2660.0,
    )

    class FakeRouter:
        feed_name = "alpaca"
        mids = 0

        def get_option_mid(self, occ):
            FakeRouter.mids += 1
            return 7.25

    with (
        patch(
            "optionda.cli.sync_completed_session",
            return_value=SessionSyncResult(),
        ),
        patch("optionda.cli.mark_account", return_value=[row]),
        patch("optionda.cli.MarketRouter", return_value=FakeRouter()),
    ):
        result = runner.invoke(app, ["verify"])
    assert result.exit_code == 0, result.output
    assert FakeRouter.mids == 1


def test_export_calls_session_sync(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("OPTIONDA_HOME", str(tmp_path))
    monkeypatch.setenv("OPTIONDA_ACTIVE", "demo")
    assert runner.invoke(app, ["create", "demo"]).exit_code == 0

    from optionda.market.session import SessionSyncResult

    with (
        patch(
            "optionda.cli.sync_completed_session",
            return_value=SessionSyncResult(),
        ) as sync,
        patch("optionda.cli.mark_account", return_value=[]),
    ):
        result = runner.invoke(app, ["export"])
    assert result.exit_code == 0, result.output
    sync.assert_called_once()


def test_run_syncs_once_at_start(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("OPTIONDA_HOME", str(tmp_path))
    monkeypatch.setenv("OPTIONDA_ACTIVE", "demo")
    assert runner.invoke(app, ["create", "demo"]).exit_code == 0

    from optionda.market.session import SessionSyncResult

    with (
        patch(
            "optionda.cli.sync_completed_session",
            return_value=SessionSyncResult(),
        ) as sync,
        patch("optionda.cli.mark_account", return_value=[]),
        patch("optionda.cli.time.sleep", side_effect=KeyboardInterrupt),
    ):
        result = runner.invoke(app, ["run"])
    assert "stopped" in (result.output or "").lower()
    assert sync.call_count == 1


def test_run_resyncs_after_close_boundary(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("OPTIONDA_HOME", str(tmp_path))
    monkeypatch.setenv("OPTIONDA_ACTIVE", "demo")
    assert runner.invoke(app, ["create", "demo"]).exit_code == 0

    from optionda.market.session import SessionSyncResult

    close_at = datetime(2026, 8, 14, 20, 0, tzinfo=timezone.utc)
    first = SessionSyncResult(next_close_at=close_at)
    second = SessionSyncResult()
    calls = {"n": 0}

    def fake_sync(*_args, **_kwargs):
        calls["n"] += 1
        return first if calls["n"] == 1 else second

    due = {"n": 0}

    def fake_due(*_args, **_kwargs):
        due["n"] += 1
        return due["n"] > 1

    sleeps = {"n": 0}

    def fake_sleep(_seconds):
        sleeps["n"] += 1
        if sleeps["n"] > 20:
            raise KeyboardInterrupt

    with (
        patch("optionda.cli.sync_completed_session", side_effect=fake_sync),
        patch("optionda.cli.session_due", side_effect=fake_due),
        patch("optionda.cli.mark_account", return_value=[]),
        patch("optionda.cli.time.sleep", side_effect=fake_sleep),
        patch("optionda.cli.resolve_poll_interval", return_value=1),
    ):
        result = runner.invoke(app, ["run"])
    assert calls["n"] >= 2
    assert result.exception is None or isinstance(result.exception, SystemExit)
