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
    assert window._stack.currentWidget() is window.terminal


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
