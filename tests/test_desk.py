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


def test_add_rows_list_every_leg_and_fill_one_at_a_time() -> None:
    import re

    from optionda.display.table import format_add_rows

    lines = [
        "IBM 261218 300 C x6 @ 1.4",
        "SKHY 261218 250 C x1 @ 5.5",
    ]
    waiting = format_add_rows(lines, done=0, active=0, tick=0, spin="⠋")
    assert "IBM 261218 300 C x6 @ 1.4" in waiting
    assert "SKHY 261218 250 C x1 @ 5.5" in waiting
    plain_marks = (
        waiting.replace("#61d6d6", "")
        .replace("#333333", "")
        .replace("#767676", "")
        .replace("#cccccc", "")
        .replace("#16c60c", "")
    )
    assert "#" not in plain_marks
    assert waiting.count("━") == 24 * 2
    def _cyan_fill(html: str) -> int:
        runs = re.findall(r"#61d6d6\">(━+)", html)
        return len(runs[0]) if runs else 0

    started = format_add_rows(lines, done=0, active=0, tick=0, spin="⠋")
    later = format_add_rows(lines, done=0, active=0, tick=8, spin="⠙")
    assert _cyan_fill(later) > _cyan_fill(started)
    assert "⠙" in later.split("SKHY")[0]
    done = format_add_rows(lines, done=1, active=1, tick=0, spin="⠹")
    assert done.split("SKHY")[0].count("✓") == 1
    assert "⠹" in done.split("SKHY")[0]
    parked = format_add_rows(
        lines,
        done=2,
        active=None,
        note="1/2 chain  IBM chain…",
    )
    assert parked.count("✓") == 2
    assert "Calibrating IV surfaces" in parked
    assert "IBM 261218 300 C x6 @ 1.4" in parked


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


def test_empty_busy_chrome_is_page_with_hint(tmp_path, monkeypatch) -> None:
    from optionda.desk_live import DeskRunner
    from optionda.store import AccountStore

    monkeypatch.setenv("OPTIONDA_HOME", str(tmp_path))
    monkeypatch.setenv("OPTIONDA_ACTIVE", "demo")
    store = AccountStore(tmp_path)
    store.create("demo")
    store.activate("demo")
    runner = DeskRunner(home=tmp_path, store=store, paint=lambda *_: None)
    payload = runner._chrome_from(
        {
            "rows": [],
            "poll_busy": True,
            "poll_label": "1/2 fetch  spots · AAPL",
            "poll_done": 0,
            "poll_total": 1,
            "spin": "⠋",
        }
    )
    assert payload["page"] is True
    assert payload.get("explain") is True
    assert "Fetching live underlying spots." in payload["text"]
    assert "AAPL" in payload["text"]
    assert "1/2 fetch" not in payload["text"]


def test_busy_chrome_with_rows_is_never_a_page(tmp_path, monkeypatch) -> None:
    from optionda.desk_live import DeskRunner
    from optionda.store import AccountStore

    monkeypatch.setenv("OPTIONDA_HOME", str(tmp_path))
    monkeypatch.setenv("OPTIONDA_ACTIVE", "demo")
    store = AccountStore(tmp_path)
    store.create("demo")
    store.activate("demo")
    runner = DeskRunner(home=tmp_path, store=store, paint=lambda *_: None)
    payload = runner._chrome_from(
        {
            "rows": [object()],
            "poll_busy": True,
            "poll_label": "updating…",
            "poll_done": 0,
            "poll_total": 1,
            "spin": "⠋",
            "eta": 12,
        }
    )
    assert payload.get("page") is not True
    assert "\n" not in payload["text"]
    assert "⠋" in payload["text"]


def test_idle_chrome_keeps_countdown_line(tmp_path, monkeypatch) -> None:
    from optionda.desk_live import DeskRunner
    from optionda.store import AccountStore

    monkeypatch.setenv("OPTIONDA_HOME", str(tmp_path))
    monkeypatch.setenv("OPTIONDA_ACTIVE", "demo")
    store = AccountStore(tmp_path)
    store.create("demo")
    store.activate("demo")
    runner = DeskRunner(home=tmp_path, store=store, paint=lambda *_: None)
    payload = runner._chrome_from(
        {
            "rows": [object()],
            "poll_busy": False,
            "poll_label": "5s",
            "eta": 5,
        }
    )
    assert payload.get("page") is not True
    assert payload["text"] == "  5s"


def test_fetch_live_start_does_not_repaint_table(tmp_path, monkeypatch) -> None:
    from unittest.mock import patch

    from optionda.desk_live import DeskRunner
    from optionda.market.router import MarketRouter
    from optionda.market.session import SessionSyncResult
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
    from optionda.models import Position, RowMark

    acc = store.require_current()
    router = MarketRouter(tmp_path)
    rows = [
        RowMark(
            position=Position(
                occ_symbol="AAPL261120C00350000",
                underlying="AAPL",
                expiry=date(2026, 11, 20),
                strike=350.0,
                option_type="call",
                qty=1,
                side="long",
                iv_frozen=0.25,
                iv_as_of=datetime(2026, 8, 12, 20, tzinfo=timezone.utc),
                entry_premium=3.5,
            ),
            spot=210.0,
            theo=12.0,
            delta=0.3,
            dte=90.0,
            notional=1200.0,
            cost=3.5,
            close_premium=10.0,
            theo_chg=2.0,
        )
    ]
    runner._update_view(
        acc=acc,
        router=router,
        rows=rows,
        poll_busy=False,
        eta=15,
        continuous=True,
    )
    paints.clear()
    chromes.clear()
    with (
        patch("optionda.desk_live.mark_account", return_value=rows),
        patch(
            "optionda.desk_live.sync_completed_session",
            return_value=SessionSyncResult(),
        ),
        patch("optionda.desk_live.session_due", return_value=False),
        patch("optionda.desk_live.sync_book"),
        patch("optionda.desk_live.append_export_log"),
    ):
        runner.fetch_live(acc, router, rows)
    assert paints == []
    assert chromes
    assert all(item.get("page") is not True for item in chromes)


def test_cached_quotes_paint_before_the_live_fetch(tmp_path, monkeypatch) -> None:
    from datetime import date, datetime, timezone
    from unittest.mock import patch

    from optionda.desk_live import DeskRunner
    from optionda.market.session import SessionSyncResult
    from optionda.models import Position, RowMark
    from optionda.quotes import load_latest_rows, quote_count, save_quotes
    from optionda.store import AccountStore

    monkeypatch.setenv("OPTIONDA_HOME", str(tmp_path))
    monkeypatch.setenv("OPTIONDA_ACTIVE", "demo")
    store = AccountStore(tmp_path)
    store.create("demo")
    store.activate("demo")
    cached = RowMark(
        position=Position(
            id="msft1",
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
        spot=500.0,
        theo=18.0,
        delta=0.4,
        dte=170.0,
        notional=1800.0,
        cost=12.4,
        upnl=560.0,
    )
    live = cached.model_copy(update={"spot": 516.0, "theo": 18.77})
    save_quotes("demo", [cached], home=tmp_path)
    order: list[object] = []
    runner = DeskRunner(
        home=tmp_path,
        store=store,
        paint=lambda _renderable: order.append(runner.last_view["rows"][0].spot),
        on_chrome=lambda _payload: None,
    )

    def mark(*_args, **_kwargs):
        order.append("mark")
        return [live]

    with (
        patch("optionda.desk_live.mark_account", side_effect=mark),
        patch(
            "optionda.desk_live.sync_completed_session",
            return_value=SessionSyncResult(),
        ),
        patch("optionda.desk_live.sync_book"),
        patch("optionda.desk_live.append_export_log"),
    ):
        _acc, _router, rows = runner.fetch_first(console=None)
    assert order[0] == 500.0
    assert order.index("mark") > 0
    assert rows[0].spot == 516.0
    assert load_latest_rows("demo", tmp_path)[0].spot == 516.0
    assert quote_count("demo", tmp_path) == 2


def test_empty_busy_poll_does_not_paint_table(tmp_path, monkeypatch) -> None:
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
    runner._poll(
        acc,
        router,
        [],
        poll_busy=True,
        poll_label="updating…",
        full=True,
    )
    assert paints == []
    assert chromes
    assert chromes[0]["page"] is True
    assert "Getting the latest marks." in chromes[0]["text"]


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


def test_failed_clock_keeps_the_known_close_and_retries(tmp_path) -> None:
    from datetime import datetime, timedelta, timezone

    from optionda.desk_live import DeskRunner
    from optionda.market.session import SessionSyncResult

    runner = DeskRunner(home=tmp_path, store=object(), paint=lambda _renderable: None)
    known = datetime(2026, 9, 28, 20, tzinfo=timezone.utc)
    runner.next_close_at = known
    runner.notes.append("stale")
    failed = SessionSyncResult(
        unavailable="[SSL: UNEXPECTED_EOF_WHILE_READING]",
        next_retry_at=known + timedelta(minutes=2),
    )
    runner._remember_sync(failed, announce=True)
    assert runner.next_close_at == known
    assert runner.next_retry_at == failed.next_retry_at
    assert any("calendar/clock unavailable" in line for line in runner.notes)
    runner._remember_sync(SessionSyncResult(next_close_at=known), announce=True)
    assert runner.notes == []
    assert runner.next_close_at == known


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
