from datetime import datetime, timezone

from optionda.journal import log_path


def _journal(tmp_path) -> None:
    path = log_path("demo", tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(
            [
                '{"ts":"2026-01-10T18:00:00+00:00","event":"add","id":"old","occ":"IBM260618C00200000","qty_added":1}',
                '{"ts":"2026-01-20T18:00:00+00:00","event":"sell","id":"old","occ":"IBM260618C00200000","realized":500,"closed":true}',
                '{"ts":"2026-03-10T18:00:00+00:00","event":"add","id":"new","occ":"HOOD260618C00150000","qty_added":1}',
                '{"ts":"2026-03-12T18:00:00+00:00","event":"sell","id":"new","occ":"HOOD260618C00150000","realized":225,"closed":true}',
                '{"ts":"2026-03-13T18:00:00+00:00","event":"run","sum_upnl":1554,"n":1,"rows":[{"occ":"AAPL261120C00350000","qty":1,"upnl":200,"dte":90,"cost":5.2,"model":7.1,"side":"long"}]}',
            ]
        )
        + "\n",
        encoding="utf-8",
    )


def test_stats_ops_left_chart_tabs_right(tmp_path, qtbot) -> None:
    from optionda.gui.stats_view import StatsView

    _journal(tmp_path)
    view = StatsView("demo", tmp_path, period="all")
    qtbot.addWidget(view)
    assert view.tabs.tabText(0) == "Performance"
    assert view.tabs.tabText(1) == "Behavior"
    assert view.tabs.widget(0) is view.chart
    assert view.tabs.widget(1) is view.behavior
    assert view._side.indexOf(view.calendar) == 0
    assert view._side.indexOf(view.positions) == 1
    assert view._main.indexOf(view._side) == 0
    assert view._main.indexOf(view.tabs) == 1
    assert not hasattr(view.calendar, "_detail")
    codes = [view.positions.list.item(i).text() for i in range(view.positions.list.count())]
    assert codes[0] == "ALL"
    assert "AAPL" in codes[1]
    assert all("open" not in row and "closed" not in row for row in codes)
    assert all("+" not in row and "-" not in row for row in codes)


def test_stats_view_period_and_reload(tmp_path, qtbot) -> None:
    from optionda.gui.stats_view import StatsView

    _journal(tmp_path)
    view = StatsView(
        "demo",
        tmp_path,
        period="all",
    )
    # Freeze as_of by reloading after construction is enough for this journal.
    qtbot.addWidget(view)
    view.report = view._load()
    view.set_period("all")
    assert view.report.n_closed >= 1
    assert view.period == "all"
    view.reload()
    assert view.positions.list.count() >= 3
    assert "ALL" in view.positions.list.item(0).text()
    assert "AAPL" in view.positions.list.item(1).text()
    view.positions.list.setCurrentRow(2)
    assert "HOOD" in view.chart._title.text()
    assert "Realized P&L" in view.kpi._line.text()


def test_calendar_day_cells_stack_number_and_pnl(tmp_path, qtbot) -> None:
    from optionda.gui.stats_view import StatsView

    _journal(tmp_path)
    view = StatsView("demo", tmp_path, period="all")
    qtbot.addWidget(view)
    view.resize(1280, 800)
    view.show()
    qtbot.waitExposed(view)
    view.refresh_visible()
    marked_days = [
        item.day
        for item in view.report.calendar
        if item.mark_delta or item.realized
    ]
    assert marked_days
    target = marked_days[-1]
    view.calendar._month = target.replace(day=1)
    view.calendar._selected = target
    view.calendar._paint()
    live = [button for button in view.calendar._buttons if button.isEnabled()]
    assert live
    assert min(button.height() for button in live) >= 44
    marked = [button for button in live if button._pnl.text()]
    assert marked
    for button in marked:
        assert button._pnl.geometry().top() >= button._day.geometry().bottom()
        assert button._day.text().isdigit()


def test_stats_left_calendar_fits_and_list_is_tall(tmp_path, qtbot) -> None:
    from PySide6.QtWidgets import QApplication

    from optionda.gui.stats_view import StatsView

    _journal(tmp_path)
    view = StatsView("demo", tmp_path, period="all")
    qtbot.addWidget(view)
    view.resize(1280, 800)
    view.show()
    qtbot.waitExposed(view)
    view.refresh_visible()
    QApplication.processEvents()
    assert view.calendar.width() >= 360
    assert view.positions.height() >= 240
    box = view.calendar.rect()
    for button in view.calendar._buttons:
        if button.isVisible() and button.isEnabled():
            assert box.contains(button.geometry())


def test_stats_restores_splitters_after_hide(tmp_path, qtbot) -> None:
    from PySide6.QtWidgets import QApplication

    from optionda.gui.stats_view import StatsView

    _journal(tmp_path)
    view = StatsView("demo", tmp_path, period="all")
    qtbot.addWidget(view)
    view.resize(1280, 800)
    view.show()
    qtbot.waitExposed(view)
    view.apply_layout(1280, 800)
    QApplication.processEvents()
    view.hide()
    view._main.setSizes([0, 0])
    view._side.setSizes([0, 0])
    view.show()
    qtbot.waitExposed(view)
    QApplication.processEvents()
    view.refresh_visible()
    assert min(view._main.sizes()) >= 40
    assert min(view._side.sizes()) >= 40


def test_position_clicks_keep_chart_in_view(tmp_path, qtbot) -> None:
    from optionda.gui.stats_view import StatsView

    _journal(tmp_path)
    view = StatsView("demo", tmp_path, period="all")
    qtbot.addWidget(view)
    view.show()
    lst = view.positions.list
    assert lst.count() >= 3
    for _ in range(3):
        for row in range(lst.count()):
            lst.setCurrentRow(row)
            xs, ys = view.chart._line.getData()
            assert xs is not None and ys is not None
            assert len(xs) >= 2
            x0, x1 = view.chart.plot.viewRange()[0]
            y0, y1 = view.chart.plot.viewRange()[1]
            assert x1 > x0
            assert y1 > y0
            assert min(xs) <= x1 and max(xs) >= x0
            assert min(ys) <= y1 and max(ys) >= y0


def test_performance_last_label_stays_inside_plot(tmp_path, qtbot) -> None:
    from PySide6.QtWidgets import QApplication

    from optionda.gui.stats_view import StatsView

    _journal(tmp_path)
    view = StatsView("demo", tmp_path, period="all")
    qtbot.addWidget(view)
    view.resize(1280, 800)
    view.show()
    qtbot.waitExposed(view)
    view.refresh_visible()
    QApplication.processEvents()
    xs, _ys = view.chart._line.getData()
    x0, x1 = view.chart.plot.viewRange()[0]
    span = max(xs) - min(xs) or 1.0
    assert x1 >= max(xs) + span * 0.08
    tip = view.chart._tip
    tip_box = tip.mapRectToScene(tip.boundingRect())
    plot_box = view.chart.plot.getPlotItem().vb.sceneBoundingRect()
    assert tip_box.right() <= plot_box.right() + 1
    assert tip_box.left() >= plot_box.left() - 1


def test_long_add_command_wraps_in_history(qtbot) -> None:
    from PySide6.QtWidgets import QTextEdit

    from optionda.gui.terminal_view import TerminalView

    view = TerminalView()
    qtbot.addWidget(view)
    view.resize(720, 500)
    view.show()
    command = (
        'add "SKHY 261218 250 C x1 @ 9.80; INTC 261016 140 C x5 @ 1.85; '
        "HOOD 261218 150 C x2 @ 2.30; AVGO 261218 500 C x1 @ 11.80; "
        'SPCX 261218 205 C x1 @ 6.45"'
    )
    view.begin_turn(command)
    assert view.history.lineWrapMode() == QTextEdit.LineWrapMode.WidgetWidth
    assert "SKHY 261218 250 C" in view.history.toPlainText()
    assert "SPCX 261218 205 C" in view.history.toPlainText()
    _, line = view._cell_size()
    assert view.history.document().size().height() > line * 1.4


def test_add_summary_fills_live_pane(tmp_path, qtbot) -> None:
    from optionda.batch import BatchResult, BatchRow
    from optionda.gui.main_window import MainWindow

    _journal(tmp_path)
    window = MainWindow("demo", tmp_path, period="all", initial_view="term")
    qtbot.addWidget(window)
    window.resize(900, 600)
    window.show()
    window.terminal.begin_turn(
        'add "HOOD 261218 150 C x2 @ 2.30; AVGO 261218 500 C x1 @ 11.80"'
    )
    result = BatchResult(
        ok=2,
        rows=[
            BatchRow(
                status="ok",
                label="HOOD 261218 150 C x2 @ 2.30",
                occ="HOOD261218C00150000",
                iv=0.41,
                source="market",
                detail="qty=2 cost=2.3",
            ),
            BatchRow(
                status="ok",
                label="AVGO 261218 500 C x1 @ 11.80",
                occ="AVGO261218C00500000",
                iv=0.33,
                source="market",
                detail="qty=1 cost=11.8",
            ),
        ],
    )
    window._on_add_done(result)
    live = window.terminal.live.toPlainText()
    assert window.terminal.desk.isVisible()
    assert window.terminal.live.isVisible()
    assert "HOOD261218C00150000" in live
    assert "AVGO261218C00500000" in live
    assert "ok" in live
    history = window.terminal.history.toPlainText()
    assert "HOOD 261218 150 C" in history
    assert "HOOD261218C00150000" not in history


def test_add_progress_fills_live_pane(tmp_path, qtbot) -> None:
    from optionda.display.table import format_add_progress
    from optionda.gui.terminal_view import TerminalView

    view = TerminalView()
    qtbot.addWidget(view)
    view.resize(900, 600)
    view.show()
    view.begin_turn(
        'add "SKHY 261218 250 C x1 @ 9.80; HOOD 261218 150 C x2 @ 2.30"'
    )
    page = format_add_progress(
        spin="⠋",
        label="1/2 chain  AVGO chain…",
        done=0,
        total=5,
    )
    view.set_live_chrome(
        {
            "text": page,
            "page": True,
            "poll_busy": True,
            "poll_label": "1/2 chain  AVGO chain…",
            "poll_done": 0,
            "poll_total": 5,
        },
        keep_table=False,
    )
    live = view.live.toPlainText()
    assert "AVGO" in live
    assert "0/5" in live
    assert view.live.isVisible()
    assert not view._status.isVisible()
    assert "SKHY 261218 250 C" in view.history.toPlainText()
    assert view.bump_live_spin()
    assert "AVGO" in view.live.toPlainText()
    assert not view._status.isVisible()


def test_desk_table_paint_stops_page_progress_spin(qtbot) -> None:
    from optionda.display.table import format_load_progress
    from optionda.gui.richview import wrap_desk_html
    from optionda.gui.terminal_view import TerminalView

    view = TerminalView()
    qtbot.addWidget(view)
    view.resize(900, 600)
    view.show()
    view.set_live_chrome(
        {
            "poll_busy": True,
            "poll_label": "updating…",
            "poll_done": 1,
            "poll_total": 1,
            "page": True,
            "explain": True,
            "text": format_load_progress(
                spin="⠋",
                label="updating…",
                done=1,
                total=1,
            ),
        },
        keep_table=False,
    )
    assert "Getting the latest marks." in view.live.toPlainText()
    view.set_live_html(wrap_desk_html("AVGO261218C00500000"), wrap=False)
    assert view.chrome_busy() is False
    assert view.bump_live_spin() is False
    live = view.live.toPlainText()
    assert "AVGO261218C00500000" in live
    assert "Getting the latest marks." not in live


def test_term_start_does_not_build_stats(tmp_path, qtbot, monkeypatch) -> None:
    from optionda.gui.main_window import MainWindow

    calls = {"n": 0}

    def boom(*_args, **_kwargs):
        calls["n"] += 1
        raise AssertionError("build_report should not run on term start")

    monkeypatch.setattr("optionda.gui.stats_view.build_report", boom)
    _journal(tmp_path)
    window = MainWindow("demo", tmp_path, period="all", initial_view="term")
    qtbot.addWidget(window)
    window.show()
    assert calls["n"] == 0
    assert window._stats is None


def test_stats_view_reads_journal_once(tmp_path, qtbot, monkeypatch) -> None:
    from optionda.analytics import build_report
    from optionda.gui.stats_view import StatsView

    calls = {"n": 0}
    real = build_report

    def wrapped(*args, **kwargs):
        calls["n"] += 1
        return real(*args, **kwargs)

    monkeypatch.setattr("optionda.gui.stats_view.build_report", wrapped)
    _journal(tmp_path)
    view = StatsView("demo", tmp_path, period="all")
    qtbot.addWidget(view)
    assert calls["n"] == 1
    assert view.report.n_closed >= 1


def test_run_foreground_claims_identity_before_qt() -> None:
    import inspect

    from optionda.gui import launch

    src = inspect.getsource(launch.run_foreground)
    claim_at = src.find("claim_windows_identity")
    qt_at = src.find("PySide6")
    assert claim_at >= 0
    assert qt_at >= 0
    assert claim_at < qt_at


def test_main_window_reapplies_chrome_on_winid_change(tmp_path, qtbot, monkeypatch) -> None:
    from PySide6.QtCore import QEvent

    from optionda.gui.main_window import MainWindow

    calls = {"n": 0}

    def fake(_window) -> None:
        calls["n"] += 1

    monkeypatch.setattr("optionda.gui.main_window.apply_native_chrome", fake)
    _journal(tmp_path)
    window = MainWindow("demo", tmp_path, period="all", initial_view="term")
    qtbot.addWidget(window)
    before = calls["n"]
    window.changeEvent(QEvent(QEvent.Type.WinIdChange))
    assert calls["n"] > before


def test_apply_native_chrome_before_first_show(tmp_path, qtbot) -> None:
    from optionda.gui.main_window import MainWindow
    from optionda.gui.theme import apply_native_chrome

    _journal(tmp_path)
    window = MainWindow("demo", tmp_path, period="all", initial_view="term")
    qtbot.addWidget(window)
    apply_native_chrome(window)
    assert int(window.winId()) != 0
    assert not window.windowIcon().isNull()


def test_window_uses_optionda_icon(tmp_path, qtbot) -> None:
    from optionda.gui.main_window import MainWindow
    from optionda.gui.theme import _assets_dir, _paint_app_mark, app_icon

    _journal(tmp_path)
    assert (_assets_dir() / "app.ico").is_file()
    mark = _paint_app_mark(32).toImage()
    assert mark.pixelColor(0, 0).alpha() == 0
    assert mark.pixelColor(31, 0).alpha() == 0
    assert mark.pixelColor(16, 16).alpha() > 0
    icon = app_icon()
    assert not icon.isNull()
    assert not icon.pixmap(32, 32).isNull()
    window = MainWindow("demo", tmp_path, period="all", initial_view="stats")
    qtbot.addWidget(window)
    window.show()
    assert not hasattr(window, "_period_buttons")
    assert not window.windowIcon().isNull()
    assert not window.windowIcon().pixmap(32, 32).isNull()


def test_live_desk_has_no_external_progress(tmp_path, qtbot) -> None:
    from PySide6.QtWidgets import QWidget

    from optionda.gui.main_window import MainWindow

    _journal(tmp_path)
    window = MainWindow("demo", tmp_path, period="all", initial_view="term")
    qtbot.addWidget(window)
    window.show()
    assert window.terminal.findChild(QWidget, "deskProgress") is None
    assert not hasattr(window.terminal, "set_progress")
    window.terminal.append_block("PS main> [optionda] run")
    assert window.terminal.history.isVisible()
    window.terminal.prepare_live()
    assert window.terminal.history.isVisible()
    assert window.terminal.live.isVisible()
    assert window.terminal.live.objectName() == "termLive"
    assert window.terminal.desk.objectName() == "deskFrame"
    assert window.terminal.desk.isVisible()
    cols, rows = window.terminal.char_size()
    assert cols >= 40
    assert rows >= 8


def test_char_size_tracks_window(tmp_path, qtbot) -> None:
    from PySide6.QtWidgets import QApplication

    from optionda.gui.main_window import MainWindow

    _journal(tmp_path)
    window = MainWindow("demo", tmp_path, period="all", initial_view="term")
    qtbot.addWidget(window)
    window.resize(900, 600)
    window.show()
    qtbot.waitExposed(window)
    window.terminal.prepare_live()
    QApplication.processEvents()
    small_cols, small_rows = window.terminal.char_size()
    window.resize(1500, 960)
    QApplication.processEvents()
    large_cols, large_rows = window.terminal.char_size()
    assert large_cols > small_cols
    assert large_rows > small_rows
    window.resize(900, 600)
    QApplication.processEvents()
    back_cols, back_rows = window.terminal.char_size()
    assert back_cols < large_cols
    assert back_rows < large_rows


def test_resize_debounces_desk_relayout(tmp_path, qtbot) -> None:
    from types import SimpleNamespace

    from optionda.gui.main_window import MainWindow

    _journal(tmp_path)
    window = MainWindow("demo", tmp_path, period="all", initial_view="term")
    qtbot.addWidget(window)
    window.show()
    window._desk = SimpleNamespace(cols=80, rows=20, runner=None, isRunning=lambda: False)
    window.resize(1100, 720)
    assert window._resizing is True
    assert window._desk_resize.isActive()
    assert window._desk_resize.isSingleShot()
    assert window._desk_resize.interval() >= 80
    window._desk_resize.stop()
    window._finish_desk_resize()
    assert window._resizing is False
    window._desk = None


def test_stop_does_not_keep_fetch_chrome(tmp_path, qtbot) -> None:
    from types import SimpleNamespace

    from optionda.gui.main_window import MainWindow

    class FakeDesk:
        def __init__(self) -> None:
            self._stop = False
            self.runner = SimpleNamespace(
                last_view={
                    "poll_busy": True,
                    "poll_label": "1/2 fetch  spots",
                    "poll_done": 0,
                    "poll_total": 1,
                },
                bump_spin=lambda: {
                    "poll_busy": True,
                    "poll_label": "1/2 fetch  spots",
                    "poll_done": 0,
                    "poll_total": 1,
                    "text": "1/2 fetch   0/1  spots",
                },
            )

        def isRunning(self) -> bool:
            return True

        def request_stop(self) -> None:
            self._stop = True

        def stopping(self) -> bool:
            return self._stop

        def wait(self, _msec: int = 0) -> bool:
            return True

    _journal(tmp_path)
    window = MainWindow("demo", tmp_path, period="all", initial_view="term")
    qtbot.addWidget(window)
    window.show()
    window._page().desk = FakeDesk()
    window.terminal.set_live_chrome(
        {
            "poll_busy": True,
            "poll_label": "1/2 fetch  spots",
            "poll_done": 0,
            "poll_total": 1,
        },
        keep_table=False,
    )
    window._spin_timer.start()
    window._input.setText("stop")
    window._submit()
    window._tick_spinner()
    assert "stop" in window.terminal.history.toPlainText()
    assert "fetch" not in window.terminal._status.text()
    assert not window.terminal.desk.isVisible()
    assert not window._spin_timer.isActive()
    window._on_desk_done()
    assert "stopped" in window.terminal.history.toPlainText().lower()
    assert window._page().desk is None


def test_add_during_run_explains_stop_first(tmp_path, qtbot) -> None:
    from types import SimpleNamespace

    from optionda.gui.main_window import MainWindow

    class FakeDesk:
        def isRunning(self) -> bool:
            return True

        def request_stop(self) -> None:
            return None

        def stopping(self) -> bool:
            return False

        def wait(self, _msec: int = 0) -> bool:
            return True

        runner = SimpleNamespace(last_view={"poll_busy": True})

    _journal(tmp_path)
    window = MainWindow("demo", tmp_path, period="all", initial_view="term")
    qtbot.addWidget(window)
    window.show()
    window.terminal.append_block("PS main> [optionda] run")
    window.terminal.prepare_live()
    window._page().desk = FakeDesk()
    window._input.setText(
        'optionda add "CRWV 261218 130 C x1 @ 7.7"'
    )
    window._submit()
    text = window.terminal.history.toPlainText()
    assert "stop first" in text.lower()
    assert window._page().desk is not None
    assert window._page().add is None
    assert window._input.text() == ""


def test_ctrl_c_stops_only_when_desk_is_live(tmp_path, qtbot) -> None:
    from optionda.gui.main_window import MainWindow

    _journal(tmp_path)
    window = MainWindow("demo", tmp_path, period="all", initial_view="term")
    qtbot.addWidget(window)
    window.show()
    window._interrupt()
    assert window._desk is None
    window._input.setText("help")
    window._on_escape()
    assert window._input.text() == ""


def test_prompt_runs_help_in_terminal(tmp_path, qtbot) -> None:
    from optionda.gui.main_window import MainWindow

    _journal(tmp_path)
    window = MainWindow("demo", tmp_path, period="all", initial_view="term")
    qtbot.addWidget(window)
    window.show()
    window._input.setText("help")
    window._submit()
    assert "PS " in window.terminal.history.toPlainText()
    assert "help" in window.terminal.history.toPlainText()
    assert "activate" in window.terminal.history.toPlainText()
    assert window._input.text() == ""
    assert window._stack.currentWidget() is not window.stats


def test_main_window_shortcuts(tmp_path, qtbot) -> None:
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    from optionda.gui.main_window import MainWindow

    _journal(tmp_path)
    window = MainWindow("demo", tmp_path, period="all", initial_view="stats")
    qtbot.addWidget(window)
    window.show()
    window.set_period("1m")
    assert window.stats.period == "1m"
    window.set_period("all")
    assert window.stats.period == "all"
    window.reload()
    assert window.stats.report.n_sells >= 1
    QTest.keyClick(window, Qt.Key.Key_Right)
    window.stats.cycle_day(1)
    window.apply_layout = window.stats.apply_layout
    window.stats.apply_layout(800, 500)
    window.stats.apply_layout(1280, 800)


def test_splash_mark_html_paints_up_green_down_red() -> None:
    from optionda.gui.splash import MARK, mark_html
    from optionda.gui.theme import GREEN, RED

    html = mark_html(MARK)
    assert GREEN in html
    assert RED in html
    assert f'<span style="color:{GREEN}">/</span>' in html or f'color:{GREEN}' in html
    assert "\\" in html
    assert html.count(GREEN) >= 1
    assert "/\\" not in html.replace("\\", "")
    # A rising run is green, a falling run is red.
    assert f'style="color:{GREEN}">' in html
    assert f'style="color:{RED}">' in html
    assert mark_html("/\\") == (
        f'<pre style="margin:0">'
        f'<span style="color:{GREEN}">/</span>'
        f'<span style="color:{RED}">\\</span>'
        f"</pre>"
    )


def test_splash_mark_is_rising_path() -> None:
    from optionda.gui.splash import MARK, WORD

    mark_lines = MARK.splitlines()
    word_lines = WORD.splitlines()
    ink = [line.rstrip() for line in mark_lines if line.strip()]
    assert 10 <= len(ink) <= 14
    assert "\\" in MARK
    assert ink[0].endswith("/")
    assert ink[-1].lstrip().startswith("/")
    assert len(ink[0]) > len(ink[-1])
    assert "////////" not in ink[-1]
    assert "////" in WORD
    mark_w = max(len(line.rstrip()) for line in mark_lines)
    word_w = max(len(line.rstrip()) for line in word_lines)
    assert mark_w == word_w


def test_first_page_shows_slash_splash(tmp_path, qtbot) -> None:
    from PySide6.QtWidgets import QLabel

    from optionda.gui.main_window import MainWindow
    from optionda.gui.splash import splash_plain

    mark = splash_plain()
    assert "//" in mark
    assert "////" in mark

    _journal(tmp_path)
    window = MainWindow("demo", tmp_path, period="all", initial_view="term")
    qtbot.addWidget(window)
    window.resize(1280, 800)
    window.show()
    qtbot.waitExposed(window)
    first = window.terminal
    assert first.splash_visible()
    mark = first._splash.findChild(QLabel, "splashMark")
    word = first._splash.findChild(QLabel, "splashWord")
    assert mark is not None and "////" in mark.text()
    assert word is not None and "////" in word.text()
    assert mark.sizeHint().width() == word.sizeHint().width()
    splash = first._splash
    splash_mid = splash.rect().center().y()
    mark_bottom = mark.mapTo(splash, mark.rect().bottomLeft()).y()
    word_top = word.mapTo(splash, word.rect().topLeft()).y()
    gap_mid = (mark_bottom + word_top) / 2
    assert mark_bottom < splash_mid < word_top
    assert abs(gap_mid - splash_mid) <= 24

    window.add_tab()
    assert not window.terminal.splash_visible()
    window.set_current_tab(0)
    assert window.terminal.splash_visible()

    window._input.setText("help")
    window._submit()
    assert not window.terminal.splash_visible()
    assert "help" in window.terminal.history.toPlainText()


def test_begin_turn_clears_slash_splash(qtbot) -> None:
    from optionda.gui.terminal_view import TerminalView

    view = TerminalView(splash=True)
    qtbot.addWidget(view)
    assert view.splash_visible()
    view.begin_turn("PS main> [optionda] export")
    assert not view.splash_visible()
    assert "export" in view.history.toPlainText()


def test_begin_turn_replaces_transcript(qtbot) -> None:
    from optionda.gui.terminal_view import TerminalView

    view = TerminalView()
    qtbot.addWidget(view)
    view.append_block("old command")
    view.append_block("old output")
    view.begin_turn("PS main> [optionda] export")
    text = view.history.toPlainText()
    assert "old command" not in text
    assert "old output" not in text
    assert "export" in text


def test_submit_starts_a_fresh_command_page(tmp_path, qtbot) -> None:
    from optionda.gui.main_window import MainWindow

    _journal(tmp_path)
    window = MainWindow("demo", tmp_path, period="all", initial_view="term")
    qtbot.addWidget(window)
    window.show()
    window.terminal.append_block("leftover add panel")
    window._input.setText("help")
    window._submit()
    text = window.terminal.history.toPlainText()
    assert "leftover add panel" not in text
    assert "help" in text.lower()


def test_window_has_page_tabs_instead_of_hint(tmp_path, qtbot) -> None:
    from optionda.gui.main_window import MainWindow

    _journal(tmp_path)
    window = MainWindow("demo", tmp_path, period="all", initial_view="term")
    qtbot.addWidget(window)
    window.show()
    assert window.tab_count() == 1
    assert window.tab_title(0) == "term"
    assert window.current_tab() == 0
    message = (window.statusBar().currentMessage() or "").lower()
    assert "enter runs" not in message
    assert window._new_tab is not None


def test_plus_keeps_other_pages(tmp_path, qtbot) -> None:
    from optionda.gui.main_window import MainWindow

    _journal(tmp_path)
    window = MainWindow("demo", tmp_path, period="all", initial_view="term")
    qtbot.addWidget(window)
    window.show()
    window.terminal.append_block("keep page one")
    window.add_tab()
    assert window.tab_count() == 2
    assert window.current_tab() == 1
    assert window.tab_title(1).startswith("term")
    window._input.setText("help")
    window._submit()
    assert "help" in window.terminal.history.toPlainText()
    assert window.tab_title(1) == "help"
    window.set_current_tab(0)
    assert "keep page one" in window.terminal.history.toPlainText()
    assert "help" not in window.terminal.history.toPlainText()
    window.close_tab(1)
    assert window.tab_count() == 1
    window.close_tab(0)
    assert window.tab_count() == 1


def test_start_desk_shows_english_progress_immediately(tmp_path, qtbot, monkeypatch) -> None:
    from optionda.gui.main_window import MainWindow

    monkeypatch.setattr(
        "optionda.gui.main_window._DeskWorker.start",
        lambda self: None,
    )
    _journal(tmp_path)
    window = MainWindow("demo", tmp_path, period="all", initial_view="term")
    qtbot.addWidget(window)
    window.resize(900, 600)
    window.show()
    window.terminal.begin_turn("PS demo> [optionda] export")
    window._start_desk("export")
    live = window.terminal.live.toPlainText()
    assert "Getting the latest marks." in live
    assert "0/1" in live
    assert "(no positions)" not in live
    assert window.terminal.desk.isVisible()


def test_first_desk_frame_reveals_rows_in_order(tmp_path, qtbot) -> None:
    from datetime import date, datetime, timezone
    from types import SimpleNamespace

    from optionda.display.table import DeskReveal, reveal_steps
    from optionda.gui.main_window import MainWindow
    from optionda.gui.richview import renderable_html
    from optionda.models import Position, RowMark

    def make_row(occ: str, *, up: bool) -> RowMark:
        return RowMark(
            position=Position(
                occ_symbol=occ,
                underlying=occ[:4],
                expiry=date(2026, 12, 18),
                strike=100.0,
                option_type="call",
                qty=1,
                side="long",
                iv_frozen=0.25,
                iv_as_of=datetime(2026, 8, 12, 20, tzinfo=timezone.utc),
                entry_premium=3.5,
            ),
            spot=100.0,
            theo=12.0 if up else 8.0,
            delta=0.2,
            dte=90.0,
            notional=4000.0 if up else 800.0,
            cost=3.5,
            close_premium=10.0,
            theo_chg=2.0 if up else -2.0,
        )

    rows = [
        make_row("AVGO261218C00500000", up=True),
        make_row("INTC261016C00140000", up=False),
    ]

    class FakeRunner:
        last_view = {
            "acc": SimpleNamespace(name="demo"),
            "router": SimpleNamespace(feed_name="alpaca"),
            "rows": rows,
            "continuous": False,
            "poll_busy": False,
        }

        def html_at(self, cols, row_count, reveal=None):
            from optionda.display.table import render_snapshot

            snap = render_snapshot(
                account="demo",
                feed="alpaca",
                refresh_sec=15,
                rows=rows,
                framed=False,
                reveal=reveal,
            )
            return renderable_html(snap, cols)

    class FakeDesk:
        cols = 100
        rows = 40
        runner = FakeRunner()

        def isRunning(self) -> bool:
            return True

        def stopping(self) -> bool:
            return False

        def request_stop(self) -> None:
            return None

        def wait(self, _msec: int = 0) -> bool:
            return True

    _journal(tmp_path)
    window = MainWindow("demo", tmp_path, period="all", initial_view="term")
    qtbot.addWidget(window)
    window.resize(900, 600)
    window.show()
    page = window._page()
    page.desk = FakeDesk()
    window.terminal.prepare_live()
    window._on_desk_frame("<pre>full</pre>", page)
    live = window.terminal.live.toPlainText()
    assert "AVGO" not in live
    assert "INTC" not in live
    assert "(no positions)" not in live
    window._tick_reveal(page)
    live = window.terminal.live.toPlainText()
    assert "AVGO" in live
    assert "INTC" not in live
    while page.revealing:
        window._tick_reveal(page)
    live = window.terminal.live.toPlainText()
    assert "AVGO" in live
    assert "INTC" in live
    assert page.revealed
    assert reveal_steps(2)[-1] == DeskReveal(2, True)


def test_set_live_html_keeps_pinned_chrome_slot(qtbot) -> None:
    from optionda.gui.terminal_view import TerminalView

    view = TerminalView()
    qtbot.addWidget(view)
    view.resize(800, 500)
    view.show()
    view.prepare_live()
    view.set_live_chrome(
        {
            "poll_busy": False,
            "eta": 12,
            "text": "12s",
        }
    )
    view.pin_live_chrome()
    assert view._status.isVisible()
    assert view._status.text() == "12s"
    view.set_live_html("<pre>table</pre>")
    assert view._status.isVisible()
    assert view._status.text() == "12s"
    view.set_live_chrome(
        {
            "poll_busy": True,
            "poll_done": 1,
            "poll_total": 4,
            "spin": "⠋",
            "text": "⠋  #---------------  1/4",
        }
    )
    assert view._status.isVisible()
    assert "1/4" in view._status.text()
    assert view.live.toPlainText()
    view.set_live_html("<pre>next</pre>")
    assert view._status.isVisible()
    assert "1/4" in view._status.text()

