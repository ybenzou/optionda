"""Stats widgets: KPI, calendar, trades, behavior, performance chart."""

from __future__ import annotations

from datetime import date

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QButtonGroup,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from optionda.analytics import DailyPnl, StatsReport, daily_map, month_cells, shift_months
from optionda.gui.charts import (
    day_ts,
    mark_xy,
    position_mark_xy,
    position_sell_points,
    position_sells,
    sell_points,
    step_xy,
)
from optionda.gui.format import (
    kpi_line,
    month_title,
    occ_short,
    signed_money,
)
from optionda.gui.theme import BG, CYAN, GREEN, HAIR, MUTED, PROMPT, RED, TEXT, mono_font


def _tone_color(tone: str) -> str:
    if tone == "pos":
        return GREEN
    if tone == "neg":
        return RED
    return TEXT


def _polish(widget: QWidget) -> None:
    widget.style().unpolish(widget)
    widget.style().polish(widget)


class KpiBar(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 2, 0, 6)
        row.setSpacing(0)
        self._line = QLabel()
        self._line.setObjectName("kpiLine")
        self._line.setFont(mono_font(12))
        row.addWidget(self._line, 1)

    def show_report(self, report: StatsReport) -> None:
        self._line.setText(kpi_line(report))
        tone = "pos" if report.realized > 0 else "neg" if report.realized < 0 else "neutral"
        self._line.setStyleSheet(f"color: {_tone_color(tone)};")


class PerformanceChart(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 8, 8, 8)
        layout.setSpacing(4)
        self._title = QLabel("Performance")
        self._title.setObjectName("title")
        layout.addWidget(self._title)
        import pyqtgraph as pg
        from pyqtgraph.graphicsItems.DateAxisItem import DateAxisItem

        pg.setConfigOptions(antialias=True)
        axis = DateAxisItem(orientation="bottom")
        self.plot = pg.PlotWidget(axisItems={"bottom": axis})
        self.plot.setBackground(BG)
        self.plot.setStyleSheet("border: none; background: transparent;")
        self.plot.showGrid(x=True, y=True, alpha=0.08)
        self.plot.getPlotItem().hideButtons()
        self.plot.getPlotItem().setContentsMargins(4, 4, 8, 4)
        self.plot.getAxis("left").setWidth(56)
        self.plot.getAxis("bottom").setHeight(28)
        for name in ("left", "bottom"):
            axis_item = self.plot.getAxis(name)
            axis_item.setPen(pg.mkPen(HAIR))
            axis_item.setTextPen(MUTED)
        self.plot.setLabel("left", "")
        self._zero = pg.InfiniteLine(pos=0, angle=0, pen=pg.mkPen(HAIR, style=Qt.PenStyle.DotLine))
        self.plot.addItem(self._zero)
        self._line = self.plot.plot(pen=pg.mkPen(TEXT, width=1.5))
        self._realized = self.plot.plot(
            pen=pg.mkPen(MUTED, width=1.0, style=Qt.PenStyle.DashLine)
        )
        self._dots = self.plot.plot(
            pen=None,
            symbol="o",
            symbolSize=6,
            symbolBrush=GREEN,
            symbolPen=pg.mkPen(BG),
        )
        self._tip = pg.TextItem(color=TEXT, anchor=(0, 1))
        self.plot.addItem(self._tip)
        self._proxy = pg.SignalProxy(
            self.plot.scene().sigMouseMoved,
            rateLimit=40,
            slot=self._on_mouse,
        )
        self._sells: list[tuple[float, float, date]] = []
        layout.addWidget(self.plot, 1)

    def show_report(self, report: StatsReport, position_id: str | None = None) -> None:
        if position_id:
            self._show_position(report, position_id)
            return
        self._title.setText("Performance")
        xs, ys = mark_xy(report)
        rx, ry = step_xy(report)
        px, py = sell_points(report)
        if report.mark_curve:
            px = [day_ts(day) for day, _value in report.mark_curve]
            py = [value for _day, value in report.mark_curve]
        self._apply_series(xs, ys, px, py, rx, ry)
        self._sells = [
            (day_ts(day), value, day) for day, value in (report.mark_curve or report.cumulative)
        ]
        if not xs:
            self._tip.setText("no marks in this window")
            return
        last_day = (report.mark_curve or report.cumulative or [(report.as_of, 0.0)])[-1][0]
        self._tip.setText(f"{last_day.isoformat()}  {signed_money(ys[-1])}")
        self._tip.setPos(xs[-1], ys[-1])

    def _show_position(self, report: StatsReport, position_id: str) -> None:
        label = position_id
        for lot in (*report.closed_lots, *report.open_lots):
            if lot.position_id == position_id:
                label = occ_short(lot.occ)
                break
        self._title.setText(label)
        xs, ys = position_mark_xy(report, position_id)
        px, py = position_sell_points(report, position_id)
        if report.position_curves.get(position_id):
            px, py = xs, ys
        self._apply_series(xs, ys, px, py)
        sells = []
        running = 0.0
        for sell in position_sells(report, position_id):
            running += sell.realized
            sells.append((day_ts(sell.et_date), running, sell.et_date))
        self._sells = sells
        if not sells:
            open_lot = next((lot for lot in report.open_lots if lot.position_id == position_id), None)
            mark = signed_money(open_lot.upnl) if open_lot is not None else "—"
            self._tip.setText(f"open  mark {mark}")
            if xs:
                self._tip.setPos(xs[-1], ys[-1])
            return
        last_x, last_y, last_day = sells[-1]
        self._tip.setText(f"{last_day.isoformat()}  {signed_money(last_y)}")
        self._tip.setPos(last_x, last_y)

    def _apply_series(
        self,
        xs: list[float],
        ys: list[float],
        px: list[float],
        py: list[float],
        realized_xs: list[float] | None = None,
        realized_ys: list[float] | None = None,
    ) -> None:
        self._line.setData(xs, ys)
        self._dots.setData(list(px), list(py))
        self._realized.setData(realized_xs or [], realized_ys or [])
        if not xs:
            return
        x0, x1 = min(xs), max(xs)
        if x1 <= x0:
            x0, x1 = x0 - 86400, x0 + 86400
        y_vals = list(ys) + list(py) + list(realized_ys or [])
        y0, y1 = min(y_vals), max(y_vals)
        if y1 <= y0:
            pad = max(abs(y0) * 0.12, 1.0)
            y0, y1 = y0 - pad, y1 + pad
        self.plot.enableAutoRange(enable=False)
        self.plot.setXRange(x0, x1, padding=0.04)
        self.plot.setYRange(y0, y1, padding=0.12)

    def _on_mouse(self, event) -> None:
        if not self._sells:
            return
        pos = event[0]
        if not self.plot.sceneBoundingRect().contains(pos):
            return
        mouse = self.plot.getPlotItem().vb.mapSceneToView(pos)
        nearest = min(self._sells, key=lambda item: abs(item[0] - mouse.x()))
        self._tip.setText(f"{nearest[2].isoformat()}  {signed_money(nearest[1])}")
        self._tip.setPos(nearest[0], nearest[1])


class CalendarWidget(QWidget):
    day_changed = Signal(object)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._month = date.today().replace(day=1)
        self._selected: date | None = None
        self._days: dict[date, DailyPnl] = {}
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 0, 8)
        root.setSpacing(4)
        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        self._prev = QPushButton("<")
        self._next = QPushButton(">")
        self._label = QLabel()
        self._label.setObjectName("title")
        self._prev.clicked.connect(lambda: self._shift(-1))
        self._next.clicked.connect(lambda: self._shift(1))
        header.addWidget(self._prev)
        header.addWidget(self._label, 1, Qt.AlignmentFlag.AlignCenter)
        header.addWidget(self._next)
        root.addLayout(header)
        self._grid = QGridLayout()
        self._grid.setSpacing(0)
        self._grid.setContentsMargins(0, 0, 0, 0)
        for col, name in enumerate(("Su", "Mo", "Tu", "We", "Th", "Fr", "Sa")):
            label = QLabel(name)
            label.setObjectName("muted")
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self._grid.addWidget(label, 0, col)
        self._buttons: list[QPushButton] = []
        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        for index in range(42):
            button = QPushButton("")
            button.setObjectName("calDay")
            button.setCheckable(True)
            button.setEnabled(False)
            button.clicked.connect(self._on_click)
            self._group.addButton(button)
            self._grid.addWidget(button, 1 + index // 7, index % 7)
            self._buttons.append(button)
        root.addLayout(self._grid)
        self._detail = QLabel("no sells on this day")
        self._detail.setObjectName("muted")
        self._detail.setWordWrap(True)
        root.addWidget(self._detail)

    def show_report(self, report: StatsReport, selected: date | None = None) -> None:
        self._days = daily_map(report)
        self._month = date(report.selected_month.year, report.selected_month.month, 1)
        month_days = [
            item.day
            for item in report.calendar
            if item.day.year == self._month.year and item.day.month == self._month.month
        ]
        self._selected = selected or (month_days[-1] if month_days else None)
        self._paint()

    def cycle_day(self, step: int) -> None:
        days = sorted(
            day
            for day in self._days
            if day.year == self._month.year and day.month == self._month.month
        )
        if not days:
            return
        if self._selected not in days:
            self._selected = days[0] if step >= 0 else days[-1]
        else:
            index = days.index(self._selected)
            self._selected = days[(index + step) % len(days)]
        self._paint()
        self.day_changed.emit(self._selected)

    def _shift(self, months: int) -> None:
        self._month = shift_months(self._month.replace(day=1), -months)
        self._paint()

    def _on_click(self) -> None:
        button = self.sender()
        raw = button.property("day") if button is not None else None
        if not raw:
            return
        day = date.fromisoformat(str(raw))
        self._selected = day
        self._paint()
        self.day_changed.emit(day)

    def _paint(self) -> None:
        self._label.setText(f"Calendar  {month_title(self._month)}")
        cells = month_cells(self._month.year, self._month.month)
        for button, cell in zip(self._buttons, cells):
            if cell is None:
                button.setText("")
                button.setEnabled(False)
                button.setChecked(False)
                button.setProperty("day", "")
                button.setProperty("tone", "neutral")
                _polish(button)
                continue
            daily = self._days.get(cell)
            text = f"{cell.day}"
            shown = None
            if daily is not None:
                shown = daily.mark_delta if daily.mark_delta is not None else daily.realized
                text += f"\n{signed_money(shown, compact=True)}"
            button.setText(text)
            button.setEnabled(True)
            button.setProperty("day", cell.isoformat())
            tone = "neutral"
            if shown is not None:
                tone = "pos" if shown > 0 else "neg" if shown < 0 else "neutral"
            button.setProperty("tone", tone)
            button.setChecked(cell == self._selected)
            _polish(button)
        daily = self._days.get(self._selected) if self._selected else None
        if daily is None:
            self._detail.setText("no marks on this day")
            return
        if daily.total is None and not daily.sells:
            self._detail.setText("no sells on this day")
            return
        lines = []
        if daily.total is not None:
            lines.append(
                f"{daily.day.isoformat()}  total {signed_money(daily.total)}  "
                f"day {signed_money(daily.mark_delta)}"
            )
            if daily.open_upnl is not None:
                lines.append(f"open mark {signed_money(daily.open_upnl)}")
        if daily.sells:
            lines.append(
                f"realized {signed_money(daily.realized)}  "
                f"{daily.n_sells} sell{'s' if daily.n_sells != 1 else ''}"
            )
        for sell in daily.sells:
            lines.append(f"{occ_short(sell.occ)}  {signed_money(sell.realized)}")
        self._detail.setText("\n".join(lines))


class PositionList(QWidget):
    picked = Signal(object)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 8, 8, 0)
        layout.setSpacing(4)
        title = QLabel("Positions")
        title.setObjectName("title")
        layout.addWidget(title)
        self.list = QListWidget()
        self.list.setFont(mono_font(11))
        self.list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.list.currentItemChanged.connect(self._on_current)
        self.list.itemClicked.connect(self._on_item)
        layout.addWidget(self.list, 1)

    def show_report(self, report: StatsReport) -> None:
        self.list.blockSignals(True)
        self.list.clear()
        book = QListWidgetItem(
            f"ALL    {signed_money(report.realized)}    {report.n_closed} closed"
        )
        book.setData(Qt.ItemDataRole.UserRole, "")
        self.list.addItem(book)
        for lot in report.open_lots:
            item = QListWidgetItem(
                f"{occ_short(lot.occ)}    {signed_money(lot.upnl)}    open"
            )
            item.setData(Qt.ItemDataRole.UserRole, lot.position_id)
            item.setForeground(QColor(CYAN if lot.upnl is None or lot.upnl >= 0 else RED))
            self.list.addItem(item)
        for lot in report.closed_lots:
            item = QListWidgetItem(
                f"{occ_short(lot.occ)}    {signed_money(lot.realized)}    closed"
            )
            item.setData(Qt.ItemDataRole.UserRole, lot.position_id)
            item.setForeground(QColor(GREEN if lot.realized >= 0 else RED))
            self.list.addItem(item)
        self.list.setCurrentRow(0)
        self.list.blockSignals(False)

    def _on_current(self, current: QListWidgetItem | None, _previous) -> None:
        self._emit(current)

    def _on_item(self, item: QListWidgetItem) -> None:
        self._emit(item)

    def _emit(self, item: QListWidgetItem | None) -> None:
        if item is None:
            return
        key = item.data(Qt.ItemDataRole.UserRole)
        self.picked.emit(key or None)


class BehaviorWidget(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 0, 0)
        layout.setSpacing(4)
        title = QLabel("Behavior")
        title.setObjectName("title")
        layout.addWidget(title)
        import pyqtgraph as pg

        pg.setConfigOptions(antialias=True)
        self._pg = pg
        self.mix = self._mini_plot()
        self.dte = self._mini_plot()
        self.tickers = self._mini_plot()
        layout.addWidget(self.mix, 1)
        layout.addWidget(self.dte, 1)
        layout.addWidget(self.tickers, 1)
        self._note = QLabel()
        self._note.setObjectName("muted")
        self._note.setFont(mono_font(11))
        layout.addWidget(self._note)

    def _mini_plot(self):
        plot = self._pg.PlotWidget()
        plot.setBackground(BG)
        plot.setStyleSheet("border: none; background: transparent;")
        plot.showGrid(x=False, y=True, alpha=0.08)
        plot.getPlotItem().hideButtons()
        plot.getPlotItem().setContentsMargins(2, 2, 4, 2)
        plot.getAxis("left").setWidth(28)
        plot.getAxis("bottom").setHeight(22)
        for name in ("left", "bottom"):
            axis_item = plot.getAxis(name)
            axis_item.setPen(self._pg.mkPen(HAIR))
            axis_item.setTextPen(MUTED)
        plot.setMouseEnabled(x=False, y=False)
        return plot

    def _set_bars(self, plot, labels: list[str], values: list[float], colors: list[str]) -> None:
        plot.clear()
        if not values:
            return
        x = list(range(len(values)))
        brushes = [self._pg.mkBrush(color) for color in colors]
        plot.addItem(
            self._pg.BarGraphItem(x=x, height=values, width=0.62, brushes=brushes)
        )
        plot.getAxis("bottom").setTicks([[(index, label) for index, label in enumerate(labels)]])
        peak = max(values) if values else 1.0
        plot.setYRange(0, max(peak * 1.15, 1.0))

    def show_report(self, report: StatsReport) -> None:
        habit = report.behavior
        self._set_bars(
            self.mix,
            ["C", "P", "L", "S"],
            [habit.call_qty, habit.put_qty, habit.long_qty, habit.short_qty],
            [CYAN, PROMPT, CYAN, PROMPT],
        )
        buckets = ("0-7", "8-30", "31-90", "91+")
        self._set_bars(
            self.dte,
            list(buckets),
            [float(habit.dte_buckets.get(name, 0)) for name in buckets],
            [TEXT, TEXT, TEXT, TEXT],
        )
        if report.by_ticker:
            names = [item.key for item in report.by_ticker[:5]]
            qty = [abs(item.realized) for item in report.by_ticker[:5]]
            colors = [GREEN if item.realized >= 0 else RED for item in report.by_ticker[:5]]
        else:
            names = [name for name, _qty in habit.by_ticker[:5]]
            qty = [float(qty) for _name, qty in habit.by_ticker[:5]]
            colors = [CYAN] * len(names)
        self._set_bars(
            self.tickers,
            names or ["—"],
            qty or [0.0],
            colors or [MUTED],
        )
        bits = [f"adds {habit.n_adds}", f"merges {habit.n_merges}"]
        if habit.n_deletes:
            bits.append(f"deletes {habit.n_deletes}")
        if report.book.n:
            bits.append(f"open {report.book.n}")
        self._note.setText("    ".join(bits))
