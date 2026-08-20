from datetime import date, datetime, timezone
from unittest.mock import patch

from typer.testing import CliRunner

from optionda.analytics import StatsReport, build_report
from optionda.cli import app
from optionda.gui.charts import position_step_xy, sell_points, step_xy
from optionda.gui.format import hold_label, kpi_cards, kpi_line, occ_short, signed_money
from optionda.gui.launch import gui_command, run_app
from optionda.journal import log_path

runner = CliRunner()


def test_signed_money_and_hold() -> None:
    assert signed_money(2427) == "+2,427"
    assert signed_money(-80) == "-80"
    assert signed_money(1500, compact=True) == "+1.5k"
    assert hold_label(18) == "18d"
    assert hold_label(0.2).endswith("h")


def test_add_progress_keeps_full_label() -> None:
    from optionda.display.table import format_add_progress

    label = "add 3/7  HOOD 261218 150 C x2 @ 2.30"
    page = format_add_progress(spin="⠋", label=label, done=2, total=7)
    assert "HOOD 261218 150 C x2 @ 2.30" in page
    assert "2/7" in page
    assert "#" in page and "-" in page
    assert "…" not in page
    assert page.count("\n") >= 2


def test_occ_short() -> None:
    assert "HOOD" in occ_short("HOOD261218C00150000")
    assert occ_short("HOOD261218C00150000").endswith("C")


def test_position_row_columns_line_up() -> None:
    from optionda.gui.format import POS_OCC_W, POS_PNL_W, format_position_row

    short = format_position_row("AAPL 11/20 350C", "+200", "open")
    long = format_position_row("AVGO 12/18 500C", "+3,350", "closed")
    book = format_position_row("ALL", "+725", "2 closed")
    status_at = POS_OCC_W + 1 + POS_PNL_W + 2
    assert short[status_at:].startswith("open")
    assert long[status_at:].startswith("closed")
    assert book[status_at:].startswith("2 closed")
    assert short[POS_OCC_W + 1 : POS_OCC_W + 1 + POS_PNL_W].endswith("+200")
    assert long[POS_OCC_W + 1 : POS_OCC_W + 1 + POS_PNL_W].endswith("+3,350")


def test_step_xy_starts_at_zero_and_jumps_on_sell_days() -> None:
    report = StatsReport(
        account="demo",
        period="all",
        as_of=date(2026, 3, 20),
        period_start=date(2026, 2, 1),
        cumulative=[(date(2026, 2, 10), 200.0), (date(2026, 2, 21), 70.0)],
    )
    xs, ys = step_xy(report)
    assert ys[0] == 0.0
    assert 200.0 in ys
    assert ys[-1] == 70.0
    assert xs[0] < xs[-1]
    px, py = sell_points(report)
    assert py == [200.0, 70.0]
    assert len(px) == 2


def test_step_xy_does_not_stretch_back_to_distant_period_start() -> None:
    report = StatsReport(
        account="demo",
        period="all",
        as_of=date(2026, 8, 17),
        period_start=date(2020, 2, 17),
        cumulative=[(date(2026, 8, 12), 225.0), (date(2026, 8, 13), 1262.0)],
    )
    xs, ys = step_xy(report)
    earliest = datetime(2026, 8, 1, tzinfo=timezone.utc).timestamp()
    assert xs[0] >= earliest
    assert ys[0] == 0.0
    assert ys[-1] == 1262.0


def test_kpi_line_is_one_row(tmp_path) -> None:
    path = log_path("demo", tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        '{"ts":"2026-02-20T18:00:00+00:00","event":"sell","id":"a","occ":"HOOD260618C00150000","realized":100,"closed":true}\n',
        encoding="utf-8",
    )
    report = build_report(
        "demo",
        tmp_path,
        period="all",
        as_of=datetime(2026, 3, 1, tzinfo=timezone.utc),
    )
    line = kpi_line(report)
    assert "\n" not in line
    assert "Realized P&L" in line
    assert "Win Rate" in line


def test_position_step_xy_follows_one_id() -> None:
    from optionda.analytics import DailyPnl, SellRecord

    sell = SellRecord(
        ts=datetime(2026, 8, 12, 20, tzinfo=timezone.utc),
        et_date=date(2026, 8, 12),
        position_id="hood",
        occ="HOOD260618C00150000",
        underlying="HOOD",
        side="long",
        option_type="call",
        qty_sold=1,
        exit_premium=None,
        avg_cost=None,
        realized=225.0,
        closed=True,
        hold_days=2.0,
        dte_at_exit=90,
    )
    report = StatsReport(
        account="demo",
        period="all",
        as_of=date(2026, 8, 17),
        period_start=date(2026, 8, 1),
        calendar=[DailyPnl(day=date(2026, 8, 12), realized=225.0, n_sells=1, sells=[sell])],
        cumulative=[(date(2026, 8, 12), 225.0)],
    )
    _xs, ys = position_step_xy(report, "hood")
    assert ys[-1] == 225.0
    empty_xs, empty_ys = position_step_xy(report, "missing")
    assert empty_ys[-1] == 0.0
    assert len(empty_xs) >= 2
    assert empty_xs[-1] > empty_xs[0]


def test_step_xy_empty_is_flat_zero() -> None:
    report = StatsReport(
        account="demo",
        period="1m",
        as_of=date(2026, 3, 20),
        period_start=date(2026, 2, 20),
    )
    xs, ys = step_xy(report)
    assert ys == [0.0, 0.0]
    assert len(xs) == 2


def test_kpi_empty_closed_lots_is_explicit(tmp_path) -> None:
    path = log_path("demo", tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        '{"ts":"2026-02-20T18:00:00+00:00","event":"sell","id":"a","occ":"HOOD260618C00150000","realized":100,"closed":false}\n'
        '{"ts":"2026-02-22T18:00:00+00:00","event":"run","sum_upnl":1554,"n":1,"rows":[]}\n',
        encoding="utf-8",
    )
    report = build_report(
        "demo",
        tmp_path,
        period="all",
        as_of=datetime(2026, 3, 1, tzinfo=timezone.utc),
    )
    cards = {card.title: card for card in kpi_cards(report)}
    assert cards["Win Rate"].value == "—"
    assert "no fully closed" in cards["Win Rate"].detail
    assert cards["Closed Trades"].value == "0"
    assert "sell-event" in cards["Closed Trades"].detail


def test_kpi_uses_closed_lot_win_rate(tmp_path) -> None:
    path = log_path("demo", tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        '{"ts":"2026-02-20T18:00:00+00:00","event":"sell","id":"a","occ":"HOOD260618C00150000","realized":100,"closed":true}\n'
        '{"ts":"2026-02-21T18:00:00+00:00","event":"sell","id":"b","occ":"AAPL260618C00200000","realized":-40,"closed":true}\n'
        '{"ts":"2026-02-22T18:00:00+00:00","event":"run","sum_upnl":1554,"n":1,"rows":[]}\n',
        encoding="utf-8",
    )
    report = build_report(
        "demo",
        tmp_path,
        period="all",
        as_of=datetime(2026, 3, 1, tzinfo=timezone.utc),
    )
    cards = {card.title: card for card in kpi_cards(report)}
    assert cards["Win Rate"].value == "50%"
    assert "1/2" in cards["Win Rate"].detail
    assert cards["Open uPnL"].value.startswith("+")


def test_desk_and_stats_help() -> None:
    desk = runner.invoke(app, ["desk", "--help"])
    assert desk.exit_code == 0, desk.output
    assert "foreground" in desk.output.lower()
    stats = runner.invoke(app, ["stats", "--help"])
    assert stats.exit_code == 0, stats.output
    assert "stats" in stats.output.lower() or "analysis" in stats.output.lower()


def test_desk_requires_active_account(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("OPTIONDA_HOME", str(tmp_path))
    monkeypatch.delenv("OPTIONDA_ACTIVE", raising=False)
    blocked = runner.invoke(app, ["desk"])
    assert blocked.exit_code == 1
    assert "activate" in blocked.output.lower()


def test_realized_hints_stats(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("OPTIONDA_HOME", str(tmp_path))
    monkeypatch.setenv("OPTIONDA_ACTIVE", "demo")
    assert runner.invoke(app, ["create", "demo"]).exit_code == 0
    shown = runner.invoke(app, ["realized"])
    assert shown.exit_code == 0, shown.output
    assert "stats" in shown.output.lower()


def test_optionda_no_args_opens_window(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("OPTIONDA_HOME", str(tmp_path))
    monkeypatch.setenv("OPTIONDA_ACTIVE", "demo")
    assert runner.invoke(app, ["create", "demo"]).exit_code == 0
    with patch("optionda.gui.launch.spawn_detached") as spawn:
        spawn.return_value = None
        result = runner.invoke(app, [])
    assert result.exit_code == 0, result.output
    assert "opened" in result.output.lower()
    spawn.assert_called_once()
    assert spawn.call_args.kwargs["initial_view"] == "term"


def test_optionda_help_does_not_open_window(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("OPTIONDA_HOME", str(tmp_path))
    with patch("optionda.gui.launch.spawn_detached") as spawn:
        result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0, result.output
    spawn.assert_not_called()
    assert "run" in result.output.lower()


def test_default_launch_is_detached(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("OPTIONDA_HOME", str(tmp_path))
    monkeypatch.setenv("OPTIONDA_ACTIVE", "demo")
    assert runner.invoke(app, ["create", "demo"]).exit_code == 0
    with patch("optionda.gui.launch.spawn_detached") as spawn:
        spawn.return_value = None
        result = runner.invoke(app, ["stats", "-p", "all"])
    assert result.exit_code == 0, result.output
    assert "opened" in result.output.lower()
    spawn.assert_called_once()
    assert spawn.call_args.kwargs["initial_view"] == "stats"
    assert spawn.call_args.kwargs["period"] == "all"


def test_desk_launch_uses_desk_view(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("OPTIONDA_HOME", str(tmp_path))
    monkeypatch.setenv("OPTIONDA_ACTIVE", "demo")
    assert runner.invoke(app, ["create", "demo"]).exit_code == 0
    with patch("optionda.gui.launch.spawn_detached") as spawn:
        result = runner.invoke(app, ["desk"])
    assert result.exit_code == 0, result.output
    assert spawn.call_args.kwargs["initial_view"] == "desk"


def test_foreground_does_not_spawn(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("OPTIONDA_HOME", str(tmp_path))
    monkeypatch.setenv("OPTIONDA_ACTIVE", "demo")
    assert runner.invoke(app, ["create", "demo"]).exit_code == 0
    with (
        patch("optionda.gui.launch.spawn_detached") as spawn,
        patch("optionda.gui.launch.run_foreground", return_value=0) as foreground,
    ):
        result = runner.invoke(app, ["stats", "--foreground"])
    assert result.exit_code == 0, result.output
    spawn.assert_not_called()
    foreground.assert_called_once()
    assert "opened" not in result.output.lower()


def test_gui_command_points_at_module(tmp_path) -> None:
    command = gui_command("demo", tmp_path, period="3m", initial_view="stats")
    assert "-m" in command
    assert "optionda.gui" in command
    assert "demo" in command


def test_run_app_foreground_skips_popen(tmp_path) -> None:
    with (
        patch("optionda.gui.launch.spawn_detached") as spawn,
        patch("optionda.gui.launch.run_foreground", return_value=0) as foreground,
    ):
        run_app("demo", tmp_path, period="1m", initial_view="stats", foreground=True)
    spawn.assert_not_called()
    foreground.assert_called_once()


def test_realized_is_read_once_per_cycle(tmp_path, monkeypatch) -> None:
    from optionda.desk_live import DeskRunner
    from optionda.market.router import MarketRouter
    from optionda.store import AccountStore

    monkeypatch.setenv("OPTIONDA_HOME", str(tmp_path))
    monkeypatch.setenv("OPTIONDA_ACTIVE", "demo")
    store = AccountStore(tmp_path)
    store.create("demo")
    store.activate("demo")
    calls = {"n": 0}

    def fake_summary(account, home=None):
        calls["n"] += 1
        return {"realized": 12.0, "n_sells": 1, "by_occ": {}}

    monkeypatch.setattr("optionda.desk_live.realized_pnl_summary", fake_summary)
    runner = DeskRunner(home=tmp_path, store=store, paint=lambda *_: None)
    acc = store.require_current()
    router = MarketRouter(tmp_path)
    runner._panel(acc, router, [], eta=5)
    runner._panel(acc, router, [], eta=4)
    runner._panel(acc, router, [], eta=3)
    assert calls["n"] == 1


def test_idle_until_uses_chrome_not_full_paint(tmp_path, monkeypatch) -> None:
    from optionda.desk_live import DeskRunner
    from optionda.market.router import MarketRouter
    from optionda.store import AccountStore

    monkeypatch.setenv("OPTIONDA_HOME", str(tmp_path))
    monkeypatch.setenv("OPTIONDA_ACTIVE", "demo")
    store = AccountStore(tmp_path)
    store.create("demo")
    store.activate("demo")
    paints: list[object] = []
    chromes: list[object] = []
    runner = DeskRunner(
        home=tmp_path,
        store=store,
        paint=paints.append,
        on_chrome=chromes.append,
    )
    acc = store.require_current()
    router = MarketRouter(tmp_path)
    runner._panel(acc, router, [])
    paints.clear()
    runner.idle_until(acc, router, [], 0.05)
    assert paints == []
    assert chromes


def test_add_batch_reports_progress(tmp_path, monkeypatch) -> None:
    from optionda.batch import add_batch
    from optionda.store import AccountStore

    store = AccountStore(tmp_path)
    store.create("demo")
    monkeypatch.setenv("OPTIONDA_ACTIVE", "demo")
    seen: list[tuple[str, int, int]] = []

    def freeze(pos, **kwargs):
        return pos.model_copy(update={"iv_frozen": 0.4, "iv_source": "test"})

    monkeypatch.setattr("optionda.batch.freeze_iv_for_position", freeze)
    long = "HOOD 261218 150 C x2 @ 2.30 extra-long-contract-note"
    add_batch(
        store,
        ["AAPL 261120 350 C x1 @ 3.50", long],
        home=tmp_path,
        on_progress=lambda label, done, steps: seen.append((label, done, steps)),
    )
    assert seen
    assert seen[-1][2] == 2
    assert any("add" in label for label, _d, _s in seen)
    assert any(long in label and "…" not in label for label, _d, _s in seen)


def test_sync_notes_skip_routine_surface_lines() -> None:
    from datetime import date
    from types import SimpleNamespace

    from optionda.desk_live import sync_notes

    result = SimpleNamespace(
        unavailable=None,
        references_saved={
            "AAPL": SimpleNamespace(close_spot=210.11, source="alpaca"),
        },
        surfaces_saved={
            "AAPL": SimpleNamespace(session_date=date(2026, 8, 18)),
            "CSCO": SimpleNamespace(session_date=date(2026, 8, 18)),
        },
        pending_closes={},
        pending_surfaces={"TSLA": "close grace"},
        errors={"IBM": "no chain"},
    )
    notes = sync_notes(result)
    assert not any(line.startswith("surface ") for line in notes)
    assert not any(line.startswith("close AAPL") for line in notes)
    assert any("IV pending TSLA" in line for line in notes)
    assert any("session IBM" in line for line in notes)
