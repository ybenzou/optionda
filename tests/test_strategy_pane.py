from datetime import date, datetime, timezone

from PySide6.QtCore import QPointF, Qt
from PySide6.QtWidgets import QFrame, QLabel, QSplitter, QWidget

from optionda.strategy import ContractSeries, StrategyPoint, TradeMark


def _fragment(browser, text: str):
    block = browser.document().begin()
    while block.isValid():
        it = block.begin()
        while not it.atEnd():
            frag = it.fragment()
            if text in frag.text():
                return frag.charFormat()
            it += 1
        block = block.next()
    return None


def _series() -> ContractSeries:
    return ContractSeries(
        occ="SPCX261218C00205000",
        points=[
            StrategyPoint(date(2026, 8, 31), 13, 3.6, 140.0, 3.0, -16.0),
            StrategyPoint(date(2026, 9, 4), 14, 3.65, 149.0, 3.2, -12.0),
        ],
        trades=[TradeMark(date(2026, 9, 4), "add", 1, 3.9)],
        payback_days=None,
    )


def test_same_strike_keeps_its_expiry_in_the_legend_name() -> None:
    from optionda.gui.strategy_pane import _line_name

    assert _line_name("INTC261016C00140000") == "INTC 140 10/16/26"
    assert _line_name("INTC261218C00140000") == "INTC 140 12/18/26"


def test_run_desk_keeps_strategy_on_the_right(qtbot) -> None:
    from optionda.gui.terminal_view import TerminalView

    view = TerminalView()
    qtbot.addWidget(view)
    from PySide6.QtWidgets import QApplication

    view.resize(1600, 700)
    view.show()
    view.prepare_live()
    QApplication.processEvents()
    assert view.strategy.isHidden()
    view.set_strategy_visible(True)
    QApplication.processEvents()
    split = view.findChild(QSplitter, "deskSplit")
    assert split is not None
    assert split.orientation() == Qt.Orientation.Horizontal
    assert split.widget(1) is view.strategy
    assert view.strategy.isVisible()
    assert view.live.isVisible()
    left, right = split.sizes()
    assert left >= view.locked_left_width() - 8
    assert right >= 280
    cols, _rows = view.char_size()
    assert cols >= 40


def test_strategy_pane_switches_month_and_year(qtbot) -> None:
    from optionda.gui.strategy_pane import StrategyPane

    pane = StrategyPane()
    qtbot.addWidget(pane)
    pane.resize(420, 640)
    pane.show()
    pane.set_series([_series()], anchor=date(2026, 9, 15))
    assert pane.window_label() == "2026-09-07 – 09-20"
    pane.show_next()
    assert pane.window_label() == "2026-09-14 – 09-27"
    pane.show_previous()
    pane.show_month()
    assert pane.window_label() == "2026-09"
    assert pane.chart_count() == 1
    assert pane.findChildren(QLabel, "strategyPayback") == []
    assert pane.findChild(QWidget, "strategyLegend") is None
    assert any(row[1] == "SPCX 205 12/18/26" for row in pane.book_rows())
    assert any(row[3].endswith("d") for row in pane.book_rows())
    plot = pane.findChild(QWidget, "strategyPlot")
    assert plot is not None
    labels = [text for _pos, text in plot.getAxis("bottom")._tickLevels[0]]
    assert "1" in labels
    assert "4" in labels
    assert plot.cost_line.pen.width() >= 2
    pane.show_previous()
    assert pane.window_label() == "2026-08"
    assert pane.chart_count() == 1
    pane.show_year()
    assert pane.window_label() == "2026"
    assert pane._current_plot() is plot
    plot = pane._current_plot()
    assert plot is not None
    year_labels = [text for _pos, text in plot.getAxis("bottom")._tickLevels[0]]
    assert year_labels[0] == "Aug"
    assert year_labels[-1] == "Dec"
    assert "Jan" not in year_labels
    assert "Sep" in year_labels
    pane.show_month()
    assert pane.window_label() == "2026-08"
    plot = pane.findChild(QWidget, "strategyPlot")
    assert plot is not None
    assert plot.getAxis("right").labelText == "vs cost %"
    assert plot.getAxis("left").labelText == "qty"


def test_week_shows_a_close_inside_the_window(qtbot) -> None:
    from optionda.gui.strategy_pane import StrategyPane

    closed = ContractSeries(
        occ="INTC261016C00140000",
        points=[
            StrategyPoint(date(2026, 9, 8), 5, 2.0, 20.0, 1.5, -25.0),
            StrategyPoint(date(2026, 9, 10), 0, 2.0, 20.0, 1.8, -10.0),
        ],
        trades=[TradeMark(date(2026, 9, 10), "sell", 5, 1.8)],
        payback_days=None,
    )
    earlier = ContractSeries(
        occ="HOOD261218C00150000",
        points=[
            StrategyPoint(date(2026, 7, 10), 2, 3.0, 40.0, 2.0, -30.0),
            StrategyPoint(date(2026, 7, 20), 0, 3.0, 40.0, 2.5, -16.0),
        ],
        trades=[],
        payback_days=None,
    )
    pane = StrategyPane()
    qtbot.addWidget(pane)
    pane.resize(900, 640)
    pane.show()
    pane.set_series([_series(), closed, earlier], anchor=date(2026, 9, 10))
    names = {row[1]: row[3] for row in pane.book_rows()}
    assert any(name.startswith("INTC") for name in names)
    assert any(name.startswith("SPCX") for name in names)
    assert not any(name.startswith("HOOD") for name in names)
    held = next(days for name, days in names.items() if name.startswith("INTC"))
    assert held == "3d"


def test_book_lists_larger_positions_first(qtbot) -> None:
    from optionda.gui.strategy_pane import StrategyPane

    def held(occ: str, qty: float) -> ContractSeries:
        return ContractSeries(
            occ=occ,
            points=[StrategyPoint(date(2026, 9, 4), qty, 1.0, 100.0, 1.0, 0.0)],
            trades=[],
            payback_days=None,
        )

    flat = ContractSeries(
        occ="INTC261016C00140000",
        points=[
            StrategyPoint(date(2026, 9, 8), 30, 2.0, 20.0, 1.5, -25.0),
            StrategyPoint(date(2026, 9, 10), 0, 2.0, 20.0, 1.8, -10.0),
        ],
        trades=[],
        payback_days=None,
    )
    pane = StrategyPane()
    qtbot.addWidget(pane)
    pane.resize(900, 640)
    pane.show()
    pane.set_series(
        [
            held("CSCO261218C00130000", 4),
            held("AVGO261218C00500000", 40),
            held("IBM261218C00300000", 12),
            flat,
        ],
        anchor=date(2026, 9, 15),
    )
    pane.show_month()
    names = [row[1] for row in pane.book_rows()]
    assert [name.split()[0] for name in names] == ["AVGO", "IBM", "CSCO", "INTC"]


def test_book_panel_sits_under_the_desk_list(qtbot) -> None:
    from PySide6.QtWidgets import QApplication

    from optionda.gui.terminal_view import TerminalView

    view = TerminalView()
    qtbot.addWidget(view)
    view.resize(1400, 800)
    view.show()
    view.prepare_live()
    view.desk_list.show()
    view.desk_list.set_lines([("SPCX261218C00205000", (("SPCX 205", "#cccccc", "", False),))])
    view.set_strategy_visible(True)
    view.strategy.set_series([_series()], anchor=date(2026, 9, 15))
    view.strategy.show_month()
    QApplication.processEvents()
    view._left.activate()
    assert view.book.isVisible()
    text = view.book.plain_text()
    assert "held" in text and "realized" in text
    assert "SPCX 205 12/18/26" in text
    assert view.book.geometry().top() >= view.desk_list.geometry().bottom() - 2
    from PySide6.QtCore import QPoint
    from PySide6.QtTest import QTest

    QTest.mouseClick(view.book, Qt.MouseButton.LeftButton, pos=QPoint(24, view.book._line_h + 4))
    assert view.strategy.focus_occ() == "SPCX261218C00205000"


def test_book_realized_column_stays_clear_of_held(qtbot) -> None:
    from optionda.gui.terminal_view import _BookPanel

    book = _BookPanel()
    qtbot.addWidget(book)
    book.resize(420, 220)
    book.set_rows(
        [
            ("A", "AVGO 500 12/18/26", "#8c53ea", "44d", "+2,506"),
            ("B", "SPCX 205 12/18/26", "#16c60c", "22d", "-6.36"),
            ("C", "XLV 160 12/18/26", "#cccccc", "149d", ""),
        ]
    )
    metrics = book.fontMetrics()
    name_right, held_x, held_w, money_x, money_w = book._column_layout(metrics, book.width())
    assert held_x + held_w <= money_x
    assert name_right <= held_x
    assert money_w >= metrics.horizontalAdvance("realized")
    assert money_w >= metrics.horizontalAdvance("+2,506")
    assert held_w >= metrics.horizontalAdvance("149d")
    assert money_x + money_w <= book.width()


def test_book_stays_under_the_full_desk_list(qtbot) -> None:
    from PySide6.QtWidgets import QApplication

    from optionda.gui.terminal_view import TerminalView

    view = TerminalView()
    qtbot.addWidget(view)
    view.resize(1400, 800)
    view.show()
    view.prepare_live()
    lines = [
        (f"OCC{i:02d}", ((f"NAME {i}", "#cccccc", "", False),))
        for i in range(18)
    ]
    view.desk_list.show()
    view.desk_list.set_lines(lines)
    view.set_strategy_visible(True)
    view.strategy.set_series([_series()], anchor=date(2026, 9, 15))
    view.strategy.show_month()
    QApplication.processEvents()
    view._left.activate()
    assert view.book.isVisible()
    assert view.book.geometry().top() >= view.desk_list.geometry().bottom() - 1
    content = view.desk_list._line_h * len(lines)
    host = view._left.parentWidget()
    assert host is not None
    if content + view.book._line_h * 3 <= host.height():
        assert view.desk_list.height() == content


def test_book_row_shows_a_pointing_hand(qtbot) -> None:
    from PySide6.QtCore import QPoint
    from PySide6.QtTest import QTest

    from optionda.gui.terminal_view import _BookPanel

    book = _BookPanel()
    qtbot.addWidget(book)
    book.resize(420, 200)
    book.set_rows([("SPCX261218C00205000", "SPCX 205 12/18/26", "#8c53ea", "22d", "+1")])
    book.show()
    QTest.mouseMove(book, QPoint(24, book._line_h + 4))
    assert book.cursor().shape() == Qt.CursorShape.PointingHandCursor
    QTest.mouseMove(book, QPoint(24, 2))
    assert book.cursor().shape() == Qt.CursorShape.ArrowCursor


def test_partial_calendar_does_not_collapse_month_or_year(qtbot) -> None:
    from optionda.gui.strategy_pane import StrategyPane, bind_sessions

    bind_sessions(
        {
            date(2026, 9, 21),
            date(2026, 9, 22),
            date(2026, 9, 23),
            date(2026, 9, 24),
            date(2026, 9, 25),
            date(2026, 9, 28),
            date(2026, 9, 29),
            date(2026, 9, 30),
            date(2026, 10, 1),
            date(2026, 10, 2),
            date(2026, 10, 5),
            date(2026, 10, 6),
        }
    )
    try:
        pane = StrategyPane()
        qtbot.addWidget(pane)
        pane.resize(900, 640)
        pane.show()
        held = _series()
        held.points.append(StrategyPoint(date(2026, 9, 29), 14, 3.7, 150.0, 3.3, -10.0))
        pane.set_series([held], anchor=date(2026, 9, 29))
        week = pane._current_plot().getViewBox().viewRange()[0]
        pane.show_month()
        assert pane.window_label() == "2026-09"
        month = pane._current_plot().getViewBox().viewRange()[0]
        labels = [text for _pos, text in pane._current_plot().getAxis("bottom")._tickLevels[0]]
        assert "1" in labels
        assert month[1] - month[0] > (week[1] - week[0]) + 4
        pane.show_year()
        assert pane.window_label() == "2026"
        year_labels = [text for _pos, text in pane._current_plot().getAxis("bottom")._tickLevels[0]]
        assert year_labels[0] == "Aug"
        assert year_labels[-1] == "Dec"
        assert "Jan" not in year_labels
    finally:
        bind_sessions(None)


def test_run_desk_compact_shows_cost_and_model(qtbot) -> None:
    from io import StringIO

    from rich.console import Console

    from optionda.display.table import render_snapshot
    from optionda.models import Position, RowMark

    row = RowMark(
        position=Position(
            occ_symbol="SPCX261218C00205000",
            underlying="SPCX",
            expiry=date(2026, 12, 18),
            strike=205.0,
            option_type="call",
            qty=14,
            side="long",
            iv_frozen=0.25,
            iv_as_of=datetime(2026, 8, 12, 20, tzinfo=timezone.utc),
            entry_premium=3.5,
        ),
        spot=149.0,
        theo=3.2,
        delta=0.2,
        dte=80.0,
        notional=4480.0,
        cost=3.5,
        upnl=-420.0,
    )
    group = render_snapshot(
        account="demo",
        feed="alpaca",
        refresh_sec=15,
        rows=[row],
        framed=False,
        header_bar=False,
        compact=True,
    )
    buf = StringIO()
    Console(file=buf, force_terminal=False, width=80, color_system=None).print(group)
    text = buf.getvalue()
    assert "Contract" in text
    assert "|Contract" not in text
    assert "Spot" in text
    assert "149.00" in text
    assert "Cost" in text
    assert "Model$" in text
    assert "SPCX 205" in text
    assert "Model IV" not in text
    assert "uPnL$" not in text
    from optionda.gui.richview import renderable_html

    assert "optionda:SPCX261218C00205000" in renderable_html(group, 80)
    from optionda.display.color import assign_colors, contract_color

    html = renderable_html(group, 80)
    assert contract_color("SPCX261218C00205000") in html
    from PySide6.QtWidgets import QTextBrowser

    browser = QTextBrowser()
    qtbot.addWidget(browser)
    browser.document().setDefaultStyleSheet("a { text-decoration: none; }")
    browser.setHtml(html)
    painted = _fragment(browser, "SPCX 205")
    assert painted is not None
    assert painted.foreground().color().name() == contract_color("SPCX261218C00205000")
    assert painted.fontUnderline() is False
    book = [
        "AVGO261218C00500000",
        "CRWV261218C00130000",
        "CSCO261218C00130000",
        "GOOG261218C00400000",
        "IBM261218C00300000",
        "MSFT270319C00600000",
        "RDDT261218C00200000",
        "SKHY261218C00250000",
        "SPCX261218C00205000",
        "XLV261218C00180000",
    ]
    forward = assign_colors(book)
    backward = assign_colors(reversed(book))
    partial = assign_colors(book[:4])
    assert forward == backward
    assert all(partial[occ] == forward[occ] == contract_color(occ) for occ in book[:4])
    assert len(set(forward.values())) == len(book)


def test_area_height_uses_sqrt_and_ticks_stay_in_contracts(qtbot) -> None:
    from optionda.gui.strategy_pane import StrategyPane, _qty_height, _qty_ticks

    assert _qty_height(0) == 0
    assert _qty_height(1) == 1
    assert _qty_height(120) == 120 ** 0.5
    assert _qty_height(120) < 12
    labels = [label for _height, label in _qty_ticks(120)]
    assert labels == ["1", "4", "16", "36", "64", "100"]
    assert _qty_ticks(120)[-1][0] == 10
    pane = StrategyPane()
    qtbot.addWidget(pane)
    pane.resize(640, 480)
    pane.show()
    pane.set_series([_series()], anchor=date(2026, 9, 15))
    pane.show_month()
    plot = pane.findChild(QWidget, "strategyPlot")
    _xs, ys = plot.listDataItems()[0].getData()
    assert float(ys[0]) == _qty_height(13)
    assert float(ys[1]) == _qty_height(14)
    tick_labels = [label for _pos, label in plot.getAxis("left")._tickLevels[0]]
    assert tick_labels == ["1", "4"]


def test_pnl_axis_keeps_one_extreme_day_from_flattening_the_rest() -> None:
    from optionda.gui.strategy_pane import _pnl_limits

    low, high = _pnl_limits([-40, -11, 0, 10, 61, 400])
    assert low < 0 < high
    assert high < 120
    assert low > -80
    flat_low, flat_high = _pnl_limits([0, 0, 1])
    assert flat_high - flat_low >= 10
    assert flat_low < 0 < flat_high


def test_weekend_gap_is_narrower_than_a_weekday() -> None:
    from optionda.gui.strategy_pane import _axis_ticks, _x

    friday = date(2026, 9, 25)
    monday = date(2026, 9, 28)
    tuesday = date(2026, 9, 29)
    assert _x(tuesday) - _x(monday) == 1
    assert _x(monday) - _x(friday) < 0.5
    labels = [text for _pos, text in _axis_ticks(date(2026, 9, 21), date(2026, 9, 28))]
    assert labels == ["21", "22", "23", "24", "25", "28"]
    year_labels = [
        text for _pos, text in _axis_ticks(date(2026, 8, 12), date(2026, 9, 28), months=True)
    ]
    assert year_labels == ["Aug", "Sep"]


def test_nearest_stroke_uses_pixel_distance() -> None:
    from optionda.gui.strategy_pane import _nearest_stroke

    strokes = [
        ("near", [0.0, 100.0], [0.0, 0.0], 0.0, 0.0),
        ("far", [0.0, 100.0], [40.0, 40.0], 40.0, 40.0),
    ]
    assert _nearest_stroke(strokes, 50.0, 5.0, 10.0, 10.0) == "near"
    assert _nearest_stroke(strokes, 50.0, 130.0, 10.0, 10.0) is None
    assert _nearest_stroke(strokes, 50.0, 0.4, 1.0, 1.0) == "near"


def test_clicking_a_line_highlights_that_contract(qtbot) -> None:
    from optionda.gui.strategy_pane import StrategyPane, _x

    other = ContractSeries(
        occ="AVGO261218C00500000",
        points=[
            StrategyPoint(date(2026, 8, 31), 8, 4.0, 300.0, 3.0, 10.0),
            StrategyPoint(date(2026, 9, 4), 8, 4.0, 310.0, 3.2, 12.0),
        ],
        trades=[],
        payback_days=None,
    )
    pane = StrategyPane()
    qtbot.addWidget(pane)
    pane.resize(640, 480)
    pane.show()
    pane.set_series([_series(), other], anchor=date(2026, 9, 15))
    pane.show_month()
    plot = pane.findChild(QWidget, "strategyPlot")
    when = _x(date(2026, 9, 4))
    pane.show_crosshair(date(2026, 9, 4))
    box = pane._current_plot().findChild(QFrame, "strategyCrosshair")
    from optionda.gui.theme import GREEN, MUTED, RED

    assert dict(box.property("crosshairPnl"))["+12.0%"] == GREEN
    plot.pick_view(when, -12.0, 1.0, 1.0)
    assert pane.focus_occ() == "SPCX261218C00205000"
    paints = dict(box.property("crosshairPnl"))
    assert paints["-12.0%"] == RED
    assert paints["+12.0%"] == MUTED
    plot.pick_view(when, 12.0, 1.0, 1.0)
    assert pane.focus_occ() == "AVGO261218C00500000"
    plot.pick_view(when, 80.0, 1.0, 1.0)
    assert pane.focus_occ() == "AVGO261218C00500000"
    plot.pick_view(when, 12.0, 1.0, 1.0)
    assert pane.focus_occ() is None


def test_clicking_a_contract_highlights_its_line(qtbot) -> None:
    from optionda.gui.strategy_pane import StrategyPane

    pane = StrategyPane()
    qtbot.addWidget(pane)
    pane.resize(640, 480)
    pane.show()
    pane.set_series([_series()], anchor=date(2026, 9, 15))
    pane.show_month()
    occ = "SPCX261218C00205000"
    host = pane.findChild(QWidget, "strategyPlot").parent()
    guide = host.level_guides[occ]
    assert guide.isVisible()
    xs, ys = guide.getData()
    from optionda.gui.strategy_pane import _x

    assert float(ys[0]) == float(ys[1]) == -12.0
    assert float(xs[0]) == _x(date(2026, 9, 4))
    assert float(xs[1]) > float(xs[0])
    assert guide.opts["pen"].widthF() == 1
    pane.focus_contract(occ)
    assert pane.focus_occ() == occ
    assert host.focus_roles[occ] == "hot"
    assert guide.isVisible()
    assert guide.opts["pen"].widthF() == 1.8
    pane.focus_contract(occ)
    assert pane.focus_occ() is None
    assert host.focus_roles[occ] == "all"
    assert guide.isVisible()
    assert guide.opts["pen"].widthF() == 1
    assert host.focus_roles[occ] == "all"


def test_crosshair_lists_only_lines_crossing_that_day(qtbot) -> None:
    from optionda.gui.strategy_pane import StrategyPane

    later = ContractSeries(
        occ="AVGO261218C00500000",
        points=[
            StrategyPoint(date(2026, 9, 18), 60, 3.85, 349.0, 2.34, -39.0),
        ],
        trades=[],
        payback_days=None,
    )
    up = ContractSeries(
        occ="MSFT270319C00600000",
        points=[
            StrategyPoint(date(2026, 9, 4), 1, 8.0, 420.0, 8.4, 5.0),
        ],
        trades=[],
        payback_days=None,
    )
    pane = StrategyPane()
    qtbot.addWidget(pane)
    pane.resize(640, 480)
    pane.show()
    flat = ContractSeries(
        occ="IBM261218C00300000",
        points=[
            StrategyPoint(date(2026, 9, 4), 2, 4.0, 200.0, None, None),
        ],
        trades=[],
        payback_days=None,
    )
    pane.set_series([_series(), later, up, flat], anchor=date(2026, 9, 15))
    pane.show_month()
    pane.show_crosshair(date(2026, 9, 4))
    text = pane.crosshair_text()
    assert "2026-09-04" in text
    assert "SPCX 205" in text
    assert "14" in text
    assert "3.20" in text
    assert "-12.0%" in text
    assert "AVGO 500" not in text
    assert "+5.0%" in text
    body = text.splitlines()[1:]
    assert len(body) == 3
    assert len({len(line) for line in body}) == 1
    box = pane._current_plot().findChild(QFrame, "strategyCrosshair")
    paints = dict(box.property("crosshairPnl"))
    from optionda.gui.theme import GREEN, MUTED, RED

    assert paints["-12.0%"] == RED
    assert paints["+5.0%"] == GREEN
    assert paints["—"] == MUTED
    pane.show_crosshair(date(2026, 9, 18))
    later_text = pane.crosshair_text()
    assert "AVGO 500" in later_text
    assert "SPCX 205" not in later_text
    assert "MSFT 600" not in later_text


def test_crosshair_hides_as_soon_as_the_pointer_leaves(qtbot) -> None:
    from PySide6.QtCore import QCoreApplication, QEvent

    from optionda.gui.strategy_pane import StrategyPane

    pane = StrategyPane()
    qtbot.addWidget(pane)
    pane.resize(640, 480)
    pane.move(-8000, -8000)
    pane.show()
    pane.set_series([_series()], anchor=date(2026, 9, 15))
    pane.show_month()
    pane.show_crosshair(date(2026, 9, 4))
    assert "2026-09-04" in pane.crosshair_text()
    plot = pane._current_plot()
    QCoreApplication.sendEvent(plot.viewport(), QEvent(QEvent.Type.Leave))
    assert pane.crosshair_text() == ""


def test_fast_exit_does_not_bring_the_crosshair_back(qtbot) -> None:
    from PySide6.QtCore import QCoreApplication, QEvent

    from optionda.gui.strategy_pane import StrategyPane

    pane = StrategyPane()
    qtbot.addWidget(pane)
    pane.resize(640, 480)
    pane.move(-8000, -8000)
    pane.show()
    pane.set_series([_series()], anchor=date(2026, 9, 15))
    pane.show_month()
    plot = pane._current_plot()
    box = plot.getViewBox()
    qtbot.waitUntil(lambda: not box.sceneBoundingRect().isEmpty())
    plot.scene().sigMouseMoved.emit(box.sceneBoundingRect().center())
    QCoreApplication.sendEvent(plot.viewport(), QEvent(QEvent.Type.Leave))
    qtbot.wait(80)
    assert pane.crosshair_text() == ""


def _book(occ: str, points: list[StrategyPoint], trades: list[TradeMark]) -> ContractSeries:
    return ContractSeries(occ=occ, points=points, trades=trades, payback_days=None)


def test_trade_runs_cover_the_four_outcomes() -> None:
    from optionda.gui.strategy_pane import _fill_caption, _gap_runs, _trade_anchors

    added_then_sold = _book(
        "SPCX261218C00205000",
        [
            StrategyPoint(date(2026, 9, 2), 4, 1.0, 100.0, 1.0, 0.0),
            StrategyPoint(date(2026, 9, 5), 4, 1.0, 110.0, 1.2, 10.0),
            StrategyPoint(date(2026, 9, 8), 2, 1.0, 108.0, 1.1, 2.0),
        ],
        [
            TradeMark(date(2026, 9, 2), "add", 4, 1.0),
            TradeMark(date(2026, 9, 5), "sell", 2, 1.5),
        ],
    )
    assert [tone for tone, _samples in _gap_runs(added_then_sold)] == ["up", "down"]
    assert [side for _x, _h, _pnl, side in _trade_anchors(added_then_sold)] == ["add", "sell"]

    sold_then_rose = _book(
        "AVGO261218C00500000",
        [
            StrategyPoint(date(2026, 9, 2), 8, 2.0, 300.0, 2.0, 10.0),
            StrategyPoint(date(2026, 9, 5), 4, 2.0, 290.0, 1.4, 1.0),
            StrategyPoint(date(2026, 9, 8), 4, 2.0, 305.0, 2.4, 8.0),
        ],
        [
            TradeMark(date(2026, 9, 2), "add", 8, 2.0),
            TradeMark(date(2026, 9, 5), "sell", 4, 1.4),
        ],
    )
    assert [tone for tone, _samples in _gap_runs(sold_then_rose)] == ["down", "up"]
    flat = _book(
        "IBM261218C00300000",
        [
            StrategyPoint(date(2026, 9, 2), 2, 1.0, 100.0, 1.0, 3.0),
            StrategyPoint(date(2026, 9, 5), 2, 1.0, 100.0, 1.0, 3.0),
        ],
        [TradeMark(date(2026, 9, 2), "add", 2, 1.0)],
    )
    assert _gap_runs(flat) == []
    assert _fill_caption(added_then_sold.trades[:1]) == "+4 @ 1.00"
    opened_before_the_line = _book(
        "AVGO261218C00500000",
        [
            StrategyPoint(date(2026, 8, 17), 2, 1.0, 100.0, 1.0, -14.0),
            StrategyPoint(date(2026, 8, 19), 5, 1.0, 100.0, 1.0, -36.0),
        ],
        [
            TradeMark(date(2026, 8, 16), "add", 1, 1.0),
            TradeMark(date(2026, 8, 16), "add", 1, 1.0),
        ],
    )
    assert [side for _x, _h, _pnl, side in _trade_anchors(opened_before_the_line)] == ["add"]
    assert [tone for tone, _samples in _gap_runs(opened_before_the_line)] == ["down"]


def test_trade_arrow_shrinks_when_the_lines_meet(qtbot) -> None:
    from PySide6.QtCore import QPointF

    from optionda.gui.strategy_pane import _vertical_arrow

    def span(path):
        rect = path.boundingRect()
        return rect.top(), rect.bottom(), rect.height()

    top, bottom, height = span(_vertical_arrow(QPointF(10, 0), QPointF(10, 100), upward=True))
    assert height <= 22.5
    assert top >= 4
    assert bottom <= 96

    top, bottom, height = span(_vertical_arrow(QPointF(10, 0), QPointF(10, 40), upward=False))
    assert 14 <= height <= 20.5
    assert top >= 4
    assert bottom <= 36

    top, bottom, height = span(_vertical_arrow(QPointF(10, 40), QPointF(10, 46), upward=True))
    assert height <= 9
    assert bottom <= 36

    top, bottom, height = span(_vertical_arrow(QPointF(10, 50), QPointF(10, 50), upward=True))
    assert height <= 9
    assert bottom <= 46

    top, bottom, height = span(_vertical_arrow(QPointF(10, 40), QPointF(10, 46), upward=False))
    assert height <= 9
    assert top >= 50


def test_focus_shows_trade_arrows_and_colored_stretches(qtbot) -> None:
    from optionda.gui.strategy_pane import StrategyPane
    from optionda.gui.theme import GREEN, RED

    series = _book(
        "SPCX261218C00205000",
        [
            StrategyPoint(date(2026, 9, 2), 4, 1.0, 100.0, 1.0, 0.0),
            StrategyPoint(date(2026, 9, 5), 4, 1.0, 110.0, 1.2, 10.0),
            StrategyPoint(date(2026, 9, 8), 2, 1.0, 108.0, 1.1, 2.0),
        ],
        [
            TradeMark(date(2026, 9, 2), "add", 4, 1.0),
            TradeMark(date(2026, 9, 5), "sell", 2, 1.5),
        ],
    )
    pane = StrategyPane()
    qtbot.addWidget(pane)
    pane.resize(640, 480)
    pane.show()
    pane.set_series([series], anchor=date(2026, 9, 15))
    pane.show_month()
    plot = pane._current_plot()
    qtbot.waitUntil(lambda: not plot.getViewBox().sceneBoundingRect().isEmpty())
    host = plot.parent()
    occ = series.occ
    assert all(not link.isVisible() for link in host.trade_links[occ])
    assert all(not seg.isVisible() for seg in host.outcome_segments[occ])
    pane.focus_contract(occ)
    links = host.trade_links[occ]
    assert [link.side for link in links] == ["add", "sell"]
    assert all(link.isVisible() for link in links)
    assert links[0].upward is True
    assert links[1].upward is False
    assert all(link.brush().style() != Qt.BrushStyle.NoBrush for link in links)
    bands = host.outcome_segments[occ]
    assert [band.tone for band in bands] == ["up", "down"]
    assert all(band.isVisible() for band in bands)
    assert bands[0].brush().color().name() == GREEN
    assert bands[1].brush().color().name() == RED
    before = bands[0].path().boundingRect().left()
    view = plot.getViewBox()
    left, right = view.viewRange()[0]
    view.setXRange(left + 8, right + 8, padding=0)
    qtbot.waitUntil(lambda: abs(bands[0].path().boundingRect().left() - before) > 1)
    pane.show_year()
    plot = pane._current_plot()
    qtbot.waitUntil(lambda: plot is not None and not plot.getViewBox().sceneBoundingRect().isEmpty())
    host = plot.parent()
    bands = host.outcome_segments[occ]

    def aligned() -> bool:
        if not bands[0].isVisible():
            return False
        rect = bands[0].path().boundingRect()
        if rect.width() <= 0:
            return False
        scene_x = plot.getViewBox().mapViewToScene(QPointF(bands[0].samples[0][0], 0)).x()
        return abs(rect.left() - scene_x) < 2

    qtbot.waitUntil(aligned)
    links = host.trade_links[occ]
    pane.show_crosshair(date(2026, 9, 2))
    assert "2026-09-02  +4 @ 1.00" in pane.crosshair_text()
    pane.focus_contract(occ)
    assert pane.focus_occ() is None
    assert all(not link.isVisible() for link in links)
    assert all(not band.isVisible() for band in bands)
