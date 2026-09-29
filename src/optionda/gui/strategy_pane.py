"""Right-hand strategy column on the live run desk."""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from datetime import date, datetime, time, timezone
from pathlib import Path
from math import hypot

from PySide6.QtCore import QEvent, QObject, QPoint, QPointF, QSize, Qt, QTimer
from PySide6.QtGui import QBrush, QColor, QCursor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QFrame,
    QGraphicsPathItem,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from optionda.display.color import assign_colors
from optionda.gui.theme import BG, GREEN, HAIR, MUTED, PROMPT, RED, TEXT, mono_font
from optionda.occ import OccError, parse_occ
from optionda.strategy import (
    ContractSeries,
    TradeMark,
    month_bounds,
    shift_month,
    shift_week,
    hold_days,
    slice_window,
    week_bounds,
    year_bounds,
)


_QTY_TICKS = (1, 4, 16, 36, 64, 100, 144, 196, 256, 400, 625, 900)


def _qty_height(qty: float) -> float:
    """Area height. Zero stays on the axis; large books no longer drown a 1-lot."""
    if qty <= 0:
        return 0.0
    return qty ** 0.5


def _qty_ticks(max_qty: float) -> list[tuple[float, str]]:
    return [(_qty_height(qty), f"{qty:g}") for qty in _QTY_TICKS if qty <= max_qty]


def _pnl_limits(values: list[float]) -> tuple[float, float]:
    """Y range for the percent lines. One extreme day does not flatten the rest."""
    ordered = sorted(value for value in values if value == value)
    if not ordered:
        return -10.0, 10.0
    if len(ordered) >= 4:
        hi, nxt = ordered[-1], ordered[-2]
        if hi > 20 and nxt < hi / 2.5:
            ordered = ordered[:-1]
        if ordered[0] < -20 and ordered[1] > ordered[0] / 2.5:
            ordered = ordered[1:]
    lo = min(0.0, ordered[0])
    hi = max(0.0, ordered[-1])
    span = hi - lo
    if span < 10:
        extra = (10 - span) / 2
        lo -= extra
        hi += extra
    else:
        pad = span * 0.08
        lo -= pad
        hi += pad
    return lo, hi


_EPOCH = date(2020, 1, 6)  # Monday
_WEEKEND = 0.35
_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
_SESSIONS: set[date] = set()
_SESSION_YEARS: set[int] = set()
_SPANS: list[tuple[date, date]] = []
_SHIFT: dict[date, int] = {}


def bind_sessions(
    days: set[date] | None,
    spans: list[tuple[date, date]] | None = None,
) -> None:
    """Open sessions from the stored calendar. Empty keeps every weekday.

    ``spans`` are the ranges that were actually fetched. A missing weekday
    counts as a holiday only inside those ranges, so a short recent calendar
    does not erase the rest of the year.
    """
    global _SESSIONS, _SESSION_YEARS, _SPANS, _SHIFT
    from optionda.market.calendar_file import known_holiday

    _SESSIONS = set(days or ())
    if spans is None and _SESSIONS:
        spans = [(min(_SESSIONS), max(_SESSIONS))]
    _SPANS = list(spans or ())
    _SESSION_YEARS = {day.year for day in _SESSIONS}
    _SHIFT = {}
    if not _SESSIONS:
        return
    count = 0
    cursor = _EPOCH
    horizon = max((end for _start, end in _SPANS), default=_EPOCH)
    horizon = date(max(horizon.year, max(_SESSION_YEARS, default=horizon.year)) + 1, 12, 31)
    while cursor <= horizon:
        if cursor > _EPOCH and known_holiday(cursor, _SESSIONS, _SPANS):
            count += 1
        _SHIFT[cursor] = count
        cursor = date.fromordinal(cursor.toordinal() + 1)


def _closed(day: date) -> bool:
    from optionda.market.calendar_file import known_holiday

    return known_holiday(day, _SESSIONS, _SPANS)


def _x(day: date) -> float:
    """Weekday steps stay 1. Friday to Monday is only a narrow weekend gap."""
    weeks, dow = divmod((day - _EPOCH).days, 7)
    stride = 4 + _WEEKEND
    if dow <= 4:
        raw = weeks * stride + dow
    else:
        raw = weeks * stride + 4 + (dow - 4) / 3 * _WEEKEND
    return raw - _SHIFT.get(day, 0)


def _axis_ticks(first: date, last: date, *, months: bool = False) -> list[tuple[float, str]]:
    span = (last - first).days
    ticks: list[tuple[float, str]] = []
    seen: set[tuple[int, int]] = set()
    day = first
    while day <= last:
        dow = day.weekday()
        if dow < 5 and not _closed(day):
            if months or span > 80:
                key = (day.year, day.month)
                if key not in seen:
                    seen.add(key)
                    name = _MONTHS[day.month - 1]
                    if first.year != last.year:
                        name = f"{name} {day.year % 100:02d}"
                    ticks.append((_x(day), name))
            elif span > 16 and dow not in (0, 2, 4) and day.day != 1:
                pass
            else:
                ticks.append((_x(day), str(day.day)))
        day = date.fromordinal(day.toordinal() + 1)
    return ticks


class StrategyPane(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("strategyPane")
        self._mode = "week"
        self._anchor = date.today()
        self._series: list[ContractSeries] = []
        self._shown: list[ContractSeries] = []
        self._focus: str | None = None
        self._book_sink = None
        self._home: Path | None = None
        self._account = ""
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(6)

        bar = QHBoxLayout()
        bar.setSpacing(6)
        self._prev = QPushButton("<")
        self._next = QPushButton(">")
        self._label = QLabel("—")
        self._label.setObjectName("strategyWindow")
        self._label.setFont(mono_font(12))
        self._week = QPushButton("Week")
        self._month = QPushButton("Month")
        self._year = QPushButton("Year")
        for button in (self._prev, self._next, self._week, self._month, self._year):
            button.setFont(mono_font(11))
            button.setFixedHeight(24)
        self._label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._prev.clicked.connect(self.show_previous)
        self._next.clicked.connect(self.show_next)
        self._week.clicked.connect(self.show_week)
        self._month.clicked.connect(self.show_month)
        self._year.clicked.connect(self.show_year)
        bar.addWidget(self._prev)
        bar.addWidget(self._label, 1)
        bar.addWidget(self._next)
        bar.addWidget(self._week)
        bar.addWidget(self._month)
        bar.addWidget(self._year)
        root.addLayout(bar)

        self._empty = QLabel("loading")
        self._empty.setObjectName("muted")
        self._empty.setFont(mono_font(11))
        self._board = QWidget()
        self._board_layout = QVBoxLayout(self._board)
        self._board_layout.setContentsMargins(0, 0, 0, 0)
        self._board_layout.addWidget(self._empty)
        root.addWidget(self._board, 1)
        self._plot = None
        self._paint_window()

    def set_book(self, account: str, home: Path | None) -> None:
        self._account = account
        self._home = home
        from optionda.market.calendar_file import load_calendar

        bind_sessions(*load_calendar(home))

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        return QSize(0, 0)

    def set_series(
        self,
        series: list[ContractSeries],
        *,
        anchor: date | None = None,
    ) -> None:
        self._series = list(series)
        if anchor is not None:
            self._anchor = anchor
        self._render()

    def focus_contract(self, occ: str) -> None:
        occ = occ.strip()
        if not occ:
            return
        self._focus = None if self._focus == occ else occ
        if self._plot is not None and self._mode == "week":
            self._render()
            return
        apply = getattr(self._plot, "apply_focus", None)
        if apply is not None:
            apply(self._focus)
        self._publish_book()

    def focus_occ(self) -> str | None:
        return self._focus

    def set_book_sink(self, sink) -> None:
        self._book_sink = sink

    def publish_book(self) -> None:
        self._publish_book()

    def book_rows(self) -> list[tuple[str, str, str, str, str]]:
        """Rows for the lower-left book: occ, name, color, hold, realized."""
        from optionda.gui.format import signed_money

        colors = assign_colors(series.occ for series in self._shown)
        realized: dict[str, float] = {}
        if self._account and self._home is not None:
            from optionda.store import realized_pnl_summary

            realized = realized_pnl_summary(self._account, self._home).get("by_occ") or {}
        full = {series.occ: series for series in self._series}
        ranked = sorted(
            self._shown,
            key=lambda series: _book_size(full.get(series.occ, series)),
            reverse=True,
        )
        rows: list[tuple[str, str, str, str, str]] = []
        for window in ranked:
            source = full.get(window.occ, window)
            days = hold_days(source.points)
            held = f"{days}d" if days is not None else ""
            amount = realized.get(window.occ.upper())
            money = "" if amount is None else signed_money(amount)
            rows.append((window.occ, _line_name(window.occ), colors[window.occ], held, money))
        return rows

    def _publish_book(self) -> None:
        if self._book_sink is None:
            return
        self._book_sink(self.book_rows(), self._focus)

    def _live_marks(self) -> list[tuple[float, float]]:
        if self._mode != "week" or not self._focus or not self._account or self._home is None:
            return []
        from zoneinfo import ZoneInfo

        from optionda.quotes import intraday_pnl

        day = datetime.now(ZoneInfo("America/New_York")).date()
        marks = intraday_pnl(self._account, self._focus, day, self._home)
        if len(marks) < 2:
            return []
        return [(_live_x(when, day), pnl) for when, pnl in marks]

    def keyPressEvent(self, event) -> None:  # noqa: N802
        key = event.key()
        if key in (Qt.Key.Key_Up, Qt.Key.Key_Down):
            self._move_focus(-1 if key == Qt.Key.Key_Up else 1)
            return
        if key == Qt.Key.Key_BracketLeft:
            self.show_previous()
            return
        if key == Qt.Key.Key_BracketRight:
            self.show_next()
            return
        super().keyPressEvent(event)

    def _move_focus(self, step: int) -> None:
        occs = [series.occ for series in self._shown]
        if not occs:
            return
        if self._focus not in occs:
            picked = occs[0] if step > 0 else occs[-1]
        else:
            index = occs.index(self._focus)
            picked = occs[(index + step) % len(occs)]
        if picked == self._focus:
            return
        self.focus_contract(picked)

    def window_label(self) -> str:
        return self._label.text()

    def chart_count(self) -> int:
        return 1 if self._plot is not None else 0

    def _current_plot(self) -> QWidget | None:
        host = self._plot
        if host is None:
            return None
        return host.findChild(QWidget, "strategyPlot")

    def show_crosshair(self, day: date) -> None:
        plot = self._current_plot()
        show = getattr(plot, "show_crosshair", None)
        if show is not None:
            show(day)

    def crosshair_text(self) -> str:
        plot = self._current_plot()
        if plot is None:
            return ""
        box = plot.findChild(QFrame, "strategyCrosshair")
        if box is None or box.isHidden():
            return ""
        return str(box.property("crosshairPlain") or "")

    def show_previous(self) -> None:
        self._shift(-1)

    def show_next(self) -> None:
        self._shift(1)

    def show_week(self) -> None:
        self._mode = "week"
        self._render()

    def show_month(self) -> None:
        self._mode = "month"
        self._render()

    def show_year(self) -> None:
        self._mode = "year"
        self._render()

    def _shift(self, delta: int) -> None:
        if self._mode == "year":
            self._anchor = date(self._anchor.year + delta, self._anchor.month, 1)
        elif self._mode == "week":
            self._anchor = shift_week(self._anchor, delta)
        else:
            self._anchor = shift_month(self._anchor, delta)
        today = date.today()
        if self._anchor > today:
            if self._mode == "week":
                self._anchor = today
            elif self._mode == "year":
                self._anchor = date(today.year, today.month, 1)
            else:
                self._anchor = today.replace(day=1)
        self._render()

    def _bounds(self) -> tuple[date, date]:
        if self._mode == "year":
            return year_bounds(self._anchor, opened=self._first_record(self._anchor.year))
        if self._mode == "week":
            return week_bounds(self._anchor)
        return month_bounds(self._anchor)

    def _first_record(self, year: int) -> date | None:
        days = [
            point.day
            for series in self._series
            for point in series.points
            if point.day.year == year
        ]
        return min(days) if days else None

    def _paint_window(self) -> None:
        if self._mode == "year":
            self._label.setText(str(self._anchor.year))
        elif self._mode == "week":
            start, end = week_bounds(self._anchor)
            if start.year == end.year:
                self._label.setText(f"{start.isoformat()} – {end.month:02d}-{end.day:02d}")
            else:
                self._label.setText(f"{start.isoformat()} – {end.isoformat()}")
        else:
            self._label.setText(f"{self._anchor.year}-{self._anchor.month:02d}")

    def _render(self) -> None:
        self._paint_window()
        start, end = self._bounds()
        shown = [
            window
            for series in self._series
            if (window := slice_window(series, start, end)).points
        ]
        self._shown = shown
        if not shown:
            if self._plot is not None:
                self._plot.hide()
            self._empty.setText("no positions in this window")
            self._empty.show()
            self._publish_book()
            return
        self._empty.hide()
        book = [series.occ for series in self._series]
        if self._plot is None:
            self._plot = _pnl_chart(
                shown,
                focus=self._focus,
                book=book,
                on_pick=self.focus_contract,
                months=self._mode == "year",
                live=self._live_marks(),
                window=(start, end),
            )
            self._board_layout.addWidget(self._plot)
        else:
            self._plot.reload_chart(
                shown,
                focus=self._focus,
                book=book,
                on_pick=self.focus_contract,
                months=self._mode == "year",
                live=self._live_marks(),
                window=(start, end),
            )
            self._plot.show()
        self._publish_book()


def _book_size(series: ContractSeries) -> float:
    """Current contracts. A flat name sorts after anything still held."""
    if not series.points:
        return 0.0
    return max(series.points[-1].qty, 0.0)


def _line_name(occ: str) -> str:
    try:
        parts = parse_occ(occ)
    except OccError:
        return occ
    expiry = parts.expiry
    return f"{parts.underlying} {parts.strike:g} {expiry.month}/{expiry.day}/{expiry:%y}"


def _nearest_stroke(
    strokes: list[tuple[str, list[float], list[float], float, float]],
    x: float,
    y: float,
    px: float,
    py: float,
    *,
    slop: float = 8.0,
) -> str | None:
    """Closest pnl line to a click, measured in pixels. Misses stay misses."""
    if px <= 0 or py <= 0 or not strokes:
        return None
    best: str | None = None
    best_d = slop
    pad_x = slop * px
    pad_y = slop * py
    for occ, xs, ys, low, high in strokes:
        if x < xs[0] - pad_x or x > xs[-1] + pad_x or y < low - pad_y or y > high + pad_y:
            continue
        dist = _polyline_px(xs, ys, x, y, px, py)
        if dist < best_d:
            best_d = dist
            best = occ
    return best


def _polyline_px(xs: list[float], ys: list[float], x: float, y: float, px: float, py: float) -> float:
    ax = xs[0] / px
    ay = ys[0] / py
    qx = x / px
    qy = y / py
    best = hypot(qx - ax, qy - ay)
    for index in range(1, len(xs)):
        bx = xs[index] / px
        by = ys[index] / py
        dx = bx - ax
        dy = by - ay
        span = dx * dx + dy * dy
        if span == 0:
            dist = hypot(qx - bx, qy - by)
        else:
            t = ((qx - ax) * dx + (qy - ay) * dy) / span
            if t < 0.0:
                t = 0.0
            elif t > 1.0:
                t = 1.0
            dist = hypot(qx - (ax + t * dx), qy - (ay + t * dy))
        if dist < best:
            best = dist
        ax, ay = bx, by
    return best


def _outcome_tone(start: float, end: float) -> str:
    """Net direction of one stretch. Flat stays the contract color."""
    if end > start + 1e-9:
        return "up"
    if end < start - 1e-9:
        return "down"
    return "flat"


def _fill_side(trades: list[TradeMark]) -> str:
    net = 0.0
    for trade in trades:
        net += trade.qty if trade.side == "add" else -trade.qty
    if net > 1e-9:
        return "add"
    if net < -1e-9:
        return "sell"
    return trades[-1].side if trades else "add"


def _fill_caption(trades: list[TradeMark]) -> str:
    parts = []
    for trade in trades:
        sign = "+" if trade.side == "add" else "−"
        qty = f"{trade.qty:g}"
        if trade.price is None:
            parts.append(f"{sign}{qty}")
        else:
            parts.append(f"{sign}{qty} @ {trade.price:.2f}")
    return "  ".join(parts)


def _pnl_value(contract: ContractSeries, day: date) -> float | None:
    point = _point_on(contract, day)
    if point is None:
        return None
    return point.pnl_pct


def _priced_points(contract: ContractSeries) -> list:
    return [point for point in contract.points if point.pnl_pct is not None]


def _session_for(priced, day: date):
    """That session, or the next one that has a percent.

    A fill logged before the first mark (a Sunday open, for example) still
    belongs to the first day the line is actually drawn.
    """
    for point in priced:
        if point.day >= day:
            return point
    return None


def _sessions(contract: ContractSeries) -> list[tuple[object, list[TradeMark]]]:
    """Each priced day that received a fill, including one logged just before it."""
    priced = _priced_points(contract)
    if not priced:
        return []
    buckets: dict[date, list[TradeMark]] = {}
    for trade in contract.trades:
        point = _session_for(priced, trade.day)
        if point is None:
            continue
        buckets.setdefault(point.day, []).append(trade)
    return [(point, buckets[point.day]) for point in priced if point.day in buckets]


def _landed_on(contract: ContractSeries, day: date) -> list[TradeMark]:
    for point, fills in _sessions(contract):
        if point.day == day:
            return fills
    return []


def _gap_runs(
    contract: ContractSeries,
) -> list[tuple[str, list[tuple[float, float, float]]]]:
    """After each fill, the band between the area top and the percent line.

    Each sample is view x, area height, and pnl. The tone is the net move to the
    next fill, or to the last point. The opening fill starts the first band.
    """
    priced = _priced_points(contract)
    sessions = _sessions(contract)
    if not priced or not sessions:
        return []
    last = priced[-1]
    runs: list[tuple[str, list[tuple[float, float, float]]]] = []
    for index, (start, _fills) in enumerate(sessions):
        end = sessions[index + 1][0] if index + 1 < len(sessions) else last
        if end.day <= start.day:
            continue
        tone = _outcome_tone(start.pnl_pct, end.pnl_pct)
        if tone == "flat":
            continue
        samples = [
            (_x(point.day), _qty_height(point.qty), point.pnl_pct)
            for point in priced
            if start.day <= point.day <= end.day
        ]
        if len(samples) >= 2:
            runs.append((tone, samples))
    return runs


def _trade_anchors(contract: ContractSeries) -> list[tuple[float, float, float, str]]:
    """View x, area height, pnl, and add-or-sell. The open lands on the first drawn day."""
    anchors = []
    for point, fills in _sessions(contract):
        anchors.append(
            (_x(point.day), _qty_height(point.qty), point.pnl_pct, _fill_side(fills))
        )
    return anchors


def _triangle(head: QPointF, tail: QPointF) -> QPainterPath:
    """Filled arrow head only. No shaft, so it cannot lie across either line."""
    path = QPainterPath()
    dx = head.x() - tail.x()
    dy = head.y() - tail.y()
    length = hypot(dx, dy)
    if length < 1:
        return path
    ux, uy = dx / length, dy / length
    half = max(3.0, min(8.0, length * 0.45))
    path.moveTo(head)
    path.lineTo(tail.x() - uy * half, tail.y() + ux * half)
    path.lineTo(tail.x() + uy * half, tail.y() - ux * half)
    path.closeSubpath()
    return path


def _vertical_arrow(area: QPointF, pnl: QPointF, *, upward: bool) -> QPainterPath:
    """Add points up, sell points down, and the mark stays off both strokes.

    Screen y grows downward. A wide gap keeps a triangle in the middle, no
    taller than half the gap and never more than 22px. A tight gap or a
    crossing uses an 8px triangle just outside the percent line.
    """
    x = area.x()
    gap = abs(area.y() - pnl.y())
    if gap >= 16.0:
        length = min(22.0, gap / 2.0)
        mid = (area.y() + pnl.y()) / 2.0
        head_y = mid - length / 2.0 if upward else mid + length / 2.0
        tail_y = mid + length / 2.0 if upward else mid - length / 2.0
        return _triangle(QPointF(x, head_y), QPointF(x, tail_y))
    head_y, tail_y = _tight_arrow(area.y(), pnl.y(), upward=upward)
    return _triangle(QPointF(x, head_y), QPointF(x, tail_y))


def _tight_arrow(area_y: float, pnl_y: float, *, upward: bool) -> tuple[float, float]:
    """8px head and tail. Prefer the percent line; step outside if that hits the area."""
    length = 8.0
    pad = 4.0

    def beside(origin: float) -> tuple[float, float]:
        if upward:
            tail_y = origin - pad
            return tail_y - length, tail_y
        tail_y = origin + pad
        return tail_y + length, tail_y

    head_y, tail_y = beside(pnl_y)
    lo, hi = (head_y, tail_y) if head_y <= tail_y else (tail_y, head_y)
    if lo - 1.0 <= area_y <= hi + 1.0:
        outer = min(area_y, pnl_y) if upward else max(area_y, pnl_y)
        head_y, tail_y = beside(outer)
    return head_y, tail_y


class _TradeLink(QGraphicsPathItem):
    """Scene-space arrow from the area top to that day's percent."""

    def __init__(self, color: str, side: str) -> None:
        super().__init__()
        self.side = side
        self.setZValue(16)
        self.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        self.setPen(QPen(Qt.PenStyle.NoPen))
        self.setBrush(QBrush(QColor(color)))
        self.setCacheMode(QGraphicsPathItem.CacheMode.DeviceCoordinateCache)
        self.upward = side == "add"

    def paint(self, painter, option, widget=None) -> None:  # noqa: N802
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        super().paint(painter, option, widget)


class _GapFill(QGraphicsPathItem):
    """Green or red band between the area top and the percent line."""

    def __init__(self, tone: str, samples: list[tuple[float, float, float]]) -> None:
        super().__init__()
        self.tone = tone
        self.samples = samples
        self.setZValue(5)
        self.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        color = QColor(GREEN if tone == "up" else RED)
        color.setAlpha(110)
        self.setBrush(QBrush(color))
        self.setPen(QPen(Qt.PenStyle.NoPen))
        self.setCacheMode(QGraphicsPathItem.CacheMode.DeviceCoordinateCache)
        self.hide()

    def place(self, qty_view, price_view) -> None:
        if len(self.samples) < 2:
            return
        area_pts = []
        pnl_pts = []
        for view_x, height, pnl in self.samples:
            area_pts.append(qty_view.mapViewToScene(QPointF(view_x, height)))
            pnl_pts.append(price_view.mapViewToScene(QPointF(view_x, pnl)))
        path = QPainterPath()
        path.moveTo(area_pts[0])
        for point in area_pts[1:]:
            path.lineTo(point)
        for point in reversed(pnl_pts):
            path.lineTo(point)
        path.closeSubpath()
        self.setPath(path)


def _fit_window(plot_item, first: date, last: date) -> None:
    """Show the selected week, month, or year, not only the days that have points."""
    import pyqtgraph as pg

    box = plot_item.getViewBox()
    box.enableAutoRange(axis=pg.ViewBox.XAxis, enable=False)
    x0 = _x(first)
    x1 = _x(last)
    if x1 < x0:
        x0, x1 = x1, x0
    if x1 == x0:
        x1 = x0 + 1.0
    pad = (x1 - x0) * 0.02
    box.setXRange(x0 - pad, x1 + pad, padding=0)


def _live_x(when: datetime, day: date) -> float:
    """Place a snapshot inside today's slot so it cannot move yesterday's close."""
    from zoneinfo import ZoneInfo

    et = when.astimezone(ZoneInfo("America/New_York"))
    minutes = et.hour * 60 + et.minute
    opened = 9 * 60 + 30
    closed = 16 * 60
    span = closed - opened
    frac = 0.0 if span <= 0 else (minutes - opened) / span
    if frac < 0.0:
        frac = 0.0
    elif frac > 1.0:
        frac = 1.0
    return _x(day) + frac * 0.8


def _pnl_chart(
    series: list[ContractSeries],
    *,
    focus: str | None = None,
    book: list[str] | None = None,
    on_pick=None,
    months: bool = False,
    live: list[tuple[float, float]] | None = None,
    window: tuple[date, date] | None = None,
) -> QWidget:
    import pyqtgraph as pg

    host = QWidget()
    layout = QVBoxLayout(host)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(4)
    plot = pg.PlotWidget()
    plot.setObjectName("strategyPlot")
    plot.setBackground(BG)
    plot.setAntialiasing(False)
    plot.showGrid(x=True, y=True, alpha=0.08)
    item = plot.getPlotItem()
    item.hideButtons()
    plot.setMenuEnabled(False)
    item.showAxis("right")
    price = pg.ViewBox()
    item.scene().addItem(price)
    right = item.getAxis("right")
    right.linkToView(price)
    price.setXLink(item)
    price.setZValue(10)
    for axis_name in ("left", "right", "bottom"):
        axis = plot.getAxis(axis_name)
        axis.setPen(pg.mkPen(HAIR))
        axis.setTextPen(MUTED)
    plot.setLabel("left", "qty")
    right.setLabel("vs cost %")
    plot.getAxis("left").setWidth(40)
    right.setWidth(56)
    plot.setMinimumSize(0, 0)
    plot.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
    for box in (item.vb, price):
        box.setMouseEnabled(False, False)
        box.wheelEvent = lambda ev, axis=None: None  # type: ignore[method-assign]

    def _sync_price() -> None:
        rect = item.vb.sceneBoundingRect()
        if price.geometry() == rect:
            return
        price.setGeometry(rect)
        price.linkedViewChanged(item.vb, price.XAxis)

    item.vb.sigResized.connect(_sync_price)
    cost = pg.InfiniteLine(
        pos=0,
        angle=0,
        pen=pg.mkPen(PROMPT, width=2),
        label="cost",
        labelOpts={
            "position": 0.98,
            "color": PROMPT,
            "fill": (12, 12, 12, 210),
        },
    )
    cost.setZValue(15)
    price.addItem(cost)
    plot.cost_line = cost  # type: ignore[attr-defined]
    drawn: list[tuple[str, object]] = []
    slots = {
        "apply_focus": lambda _selected: None,
        "place_links": lambda *_args: None,
        "place_guides": lambda: None,
        "on_click": lambda _event: None,
    }
    host.level_guides = {}  # type: ignore[attr-defined]
    host.outcome_segments = {}  # type: ignore[attr-defined]
    host.trade_links = {}  # type: ignore[attr-defined]
    host.focus_roles = {}
    host.focused = None  # type: ignore[attr-defined]

    def _on_xrange(*_args) -> None:
        slots["place_guides"]()
        slots["place_links"]()

    def _dispatch_click(event) -> None:
        slots["on_click"](event)

    price.sigXRangeChanged.connect(_on_xrange)
    item.vb.sigResized.connect(lambda *_args: slots["place_links"]())
    item.vb.sigRangeChanged.connect(lambda *_args: slots["place_links"]())
    price.sigYRangeChanged.connect(lambda *_args: slots["place_links"]())
    plot.scene().sigMouseClicked.connect(_dispatch_click)
    plot._line_click = _dispatch_click  # type: ignore[attr-defined]
    _bind_crosshair(plot, item, series, {}, focus)

    def reload(
        next_series,
        *,
        focus=None,
        book=None,
        on_pick=None,
        months=False,
        live: list[tuple[float, float]] | None = None,
        window: tuple[date, date] | None = None,
    ) -> None:
        slots["place_links"] = lambda *_args: None
        slots["place_guides"] = lambda: None
        slots["on_click"] = lambda _event: None
        for kind, obj in drawn:
            if kind == "qty":
                item.removeItem(obj)
            elif kind == "price":
                price.removeItem(obj)
            else:
                plot.scene().removeItem(obj)
        drawn.clear()
        colors = assign_colors(
            book if book is not None else (contract.occ for contract in next_series)
        )
        painted: list[tuple[str, str, object, object]] = []
        ends: dict[str, tuple[float, float]] = {}
        strokes: list[tuple[str, list[float], list[float], float, float]] = []
        guides: dict[str, object] = {}
        outcomes: dict[str, list] = {}
        anchors: dict[str, list] = {}
        host.level_guides = guides  # type: ignore[attr-defined]
        host.outcome_segments = outcomes  # type: ignore[attr-defined]
        host.trade_links = anchors  # type: ignore[attr-defined]
        draw_order = sorted(
            range(len(next_series)),
            key=lambda index: max((point.qty for point in next_series[index].points), default=0),
            reverse=True,
        )
        for index in draw_order:
            contract = next_series[index]
            color = colors[contract.occ]
            area = None
            curve = None
            qty_days = [_x(point.day) for point in contract.points]
            if qty_days:
                area = pg.PlotDataItem(
                    qty_days,
                    [_qty_height(point.qty) for point in contract.points],
                    fillLevel=0,
                )
                item.addItem(area)
                drawn.append(("qty", area))
            price_points = [
                (point.day, point.pnl_pct)
                for point in contract.points
                if point.pnl_pct is not None
            ]
            if price_points:
                _last_day, last_pnl = price_points[-1]
                xs = [_x(day) for day, _value in price_points]
                ys = [value for _day, value in price_points]
                ends[contract.occ] = (xs[-1], last_pnl)
                strokes.append((contract.occ, xs, ys, min(ys), max(ys)))
                curve = pg.PlotDataItem(xs, ys, antialias=True)
                price.addItem(curve)
                drawn.append(("price", curve))
                guide = pg.PlotDataItem(antialias=True)
                price.addItem(guide, ignoreBounds=True)
                drawn.append(("price", guide))
                guides[contract.occ] = guide
            bands = []
            for tone, samples in _gap_runs(contract):
                band = _GapFill(tone, samples)
                plot.scene().addItem(band)
                drawn.append(("scene", band))
                bands.append(band)
            outcomes[contract.occ] = bands
            marks = []
            for view_x, height, pnl, side in _trade_anchors(contract):
                link = _TradeLink(color, side)
                link.anchor = (view_x, height, pnl)  # type: ignore[attr-defined]
                link.hide()
                plot.scene().addItem(link)
                drawn.append(("scene", link))
                marks.append(link)
            anchors[contract.occ] = marks
            painted.append((contract.occ, color, area, curve))
        peak = max(
            (point.qty for contract in next_series for point in contract.points),
            default=0,
        )
        plot.getAxis("left").setTicks([_qty_ticks(peak)])
        point_days = [point.day for contract in next_series for point in contract.points]
        if window is not None:
            first_day, last_day = window
        elif point_days:
            first_day, last_day = min(point_days), max(point_days)
        else:
            first_day = last_day = None
        if first_day is not None and last_day is not None:
            plot.getAxis("bottom").setTicks(
                [_axis_ticks(first_day, last_day, months=months)]
            )
        item.enableAutoRange()
        ys = [value for _occ, _xs, stroke, _low, _high in strokes for value in stroke]
        low, high = _pnl_limits(ys)
        price.setYRange(low, high, padding=0)
        _sync_price()

        link_stamp = {"sig": None}

        def _place_links(*_args) -> None:
            active = getattr(host, "focused", None)
            _sync_price()
            rect = item.vb.sceneBoundingRect()
            x_low, x_high = item.vb.viewRange()[0]
            px_low, px_high = price.viewRange()[0]
            y_low, y_high = price.viewRange()[1]
            signature = (
                active,
                round(rect.left(), 1),
                round(rect.top(), 1),
                round(rect.width(), 1),
                round(rect.height(), 1),
                round(float(x_low), 4),
                round(float(x_high), 4),
                round(float(px_low), 4),
                round(float(px_high), 4),
                round(float(y_low), 3),
                round(float(y_high), 3),
            )
            if link_stamp["sig"] == signature:
                return
            link_stamp["sig"] = signature
            ready = not rect.isEmpty()
            for occ, bands in outcomes.items():
                for band in bands:
                    if not ready or occ != active:
                        band.hide()
                        continue
                    band.place(item.vb, price)
                    band.show()
            for occ, marks in anchors.items():
                show = ready and occ == active
                for link in marks:
                    if not show:
                        link.hide()
                        continue
                    view_x, height, pnl = link.anchor
                    link.setPath(
                        _vertical_arrow(
                            item.vb.mapViewToScene(QPointF(view_x, height)),
                            price.mapViewToScene(QPointF(view_x, pnl)),
                            upward=link.upward,
                        )
                    )
                    link.show()

        placed = {"right": None}

        def _place_guides() -> None:
            right = round(float(price.viewRange()[0][1]), 4)
            if placed["right"] == right:
                return
            placed["right"] = right
            for occ, guide in guides.items():
                end_x, end_y = ends[occ]
                if end_x >= right:
                    guide.hide()
                    continue
                guide.setData([end_x, right], [end_y, end_y])
                guide.show()

        def _apply_focus(selected: str | None) -> None:
            present = {occ for occ, _color, _area, _curve in painted}
            active = selected if selected in present else None
            host.focus_roles = {}
            for occ, color, area, curve in painted:
                role = "all" if active is None else ("hot" if occ == active else "dim")
                host.focus_roles[occ] = role
                area_pen, area_brush, curve_pen, level = _series_ink(color, role)
                if area is not None:
                    area.setPen(area_pen)
                    area.setBrush(area_brush)
                    area.setZValue(level)
                if curve is not None:
                    curve.setPen(curve_pen)
                    curve.setZValue(level + 1)
                guide = guides.get(occ)
                if guide is not None:
                    pen, rank = _guide_ink(color, role)
                    guide.setPen(pen)
                    guide.setZValue(rank)
            host.focused = active  # type: ignore[attr-defined]
            _place_links()
            box = getattr(plot, "strategy_crosshair", None)
            if box is not None:
                box.apply_focus(active)

        def _pick(x: float, y: float, px: float, py: float) -> None:
            if on_pick is None:
                return
            occ = _nearest_stroke(strokes, x, y, px, py)
            if occ is not None:
                on_pick(occ)

        def _on_click(event) -> None:
            if on_pick is None or event.button() != Qt.MouseButton.LeftButton or event.double():
                return
            pos = event.scenePos()
            bounds = price.sceneBoundingRect()
            if bounds.isEmpty() or not bounds.contains(pos):
                return
            view = price.mapSceneToView(pos)
            px, py = price.viewPixelSize()
            _pick(float(view.x()), float(view.y()), float(px), float(py))

        slots["place_links"] = _place_links
        slots["place_guides"] = _place_guides
        slots["on_click"] = _on_click
        slots["apply_focus"] = _apply_focus
        plot.pick_view = _pick  # type: ignore[attr-defined]
        host.apply_focus = _apply_focus  # type: ignore[attr-defined]
        if live and focus:
            xs = [point[0] for point in live]
            ys = [point[1] for point in live]
            color = colors.get(focus, MUTED)
            trace = pg.PlotDataItem(xs, ys, antialias=True)
            red, green, blue = pg.colorTuple(pg.mkColor(color))[:3]
            trace.setPen(pg.mkPen(red, green, blue, 120, width=1.4))
            price.addItem(trace)
            drawn.append(("price", trace))
            host.live_trace = trace  # type: ignore[attr-defined]
        else:
            host.live_trace = None  # type: ignore[attr-defined]
        refresh = getattr(plot, "refresh_crosshair", None)
        if refresh is not None:
            refresh(next_series, colors, focus)
        _place_guides()
        _apply_focus(focus)
        if first_day is not None and last_day is not None:
            _fit_window(item, first_day, last_day)

    host.reload_chart = reload  # type: ignore[attr-defined]
    layout.addWidget(plot, 1)
    reload(
        series,
        focus=focus,
        book=book,
        on_pick=on_pick,
        months=months,
        live=live,
        window=window,
    )
    return host


def _guide_ink(color: str, role: str):
    import pyqtgraph as pg

    red, green, blue = pg.colorTuple(pg.mkColor(color))[:3]
    if role == "hot":
        return pg.mkPen(red, green, blue, 255, width=1.8, style=Qt.PenStyle.DashLine), 13
    if role == "dim":
        return pg.mkPen(red, green, blue, 48, width=1, style=Qt.PenStyle.DashLine), 2
    return pg.mkPen(color, width=1, style=Qt.PenStyle.DashLine), 4


def _series_ink(color: str, role: str):
    import pyqtgraph as pg

    red, green, blue = pg.colorTuple(pg.mkColor(color))[:3]
    if role == "hot":
        return (
            pg.mkPen(red, green, blue, 255, width=1.2),
            pg.mkBrush(red, green, blue, 96),
            pg.mkPen(red, green, blue, 255, width=3.2),
            8,
        )
    if role == "dim":
        return (
            pg.mkPen(red, green, blue, 36, width=1),
            pg.mkBrush(red, green, blue, 12),
            pg.mkPen(red, green, blue, 48, width=1.1),
            0,
        )
    return (
        pg.mkPen(color, width=1),
        _area_brush(color),
        pg.mkPen(color, width=2.6),
        1,
    )


class _Hairline(QWidget):
    """One-pixel crosshair drawn on the viewport, not in the plot scene.

    Moving a scene line repaints every series under it. A child of the viewport
    only dirties a one-pixel strip.
    """

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.hide()

    def paintEvent(self, _event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(TEXT))


def _bind_crosshair(
    plot,
    item,
    series: list[ContractSeries],
    colors: dict[str, str],
    focus: str | None = None,
) -> None:
    days = sorted({point.day for contract in series for point in contract.points})
    feed = {"series": series, "colors": colors, "days": days}
    line = _Hairline(plot.viewport())
    popup = _Crosshair(plot)
    popup.set_focus(focus)
    plot.strategy_crosshair = popup  # type: ignore[attr-defined]
    state = {"day": None}
    pending: dict[str, object] = {"pos": None}
    timer = QTimer(plot)
    timer.setInterval(32)
    timer.setSingleShot(True)

    def _move_line(day: date) -> None:
        viewport = plot.viewport()
        local = plot.mapFromScene(item.vb.mapViewToScene(QPointF(_x(day), 0)))
        line.setGeometry(int(local.x()), 0, 1, max(viewport.height(), 1))
        line.raise_()
        if line.isHidden():
            line.show()

    def _hide() -> None:
        pending["pos"] = None
        timer.stop()
        state["day"] = None
        line.hide()
        popup.hide()

    def _place(local: QPoint) -> None:
        origin = plot.mapToGlobal(local)
        x = origin.x() + 16
        y = origin.y() - popup.height() // 2
        screen = plot.screen()
        if screen is not None:
            bounds = screen.availableGeometry()
            if x + popup.width() > bounds.right() - 8:
                x = origin.x() - popup.width() - 16
            if y + popup.height() > bounds.bottom() - 8:
                y = bounds.bottom() - popup.height() - 8
            x = max(bounds.left() + 8, x)
            y = max(bounds.top() + 8, y)
        popup.move(x, y)
        if popup.isHidden():
            popup.show()

    def _show(day: date, local: QPoint | None = None) -> None:
        if not feed["days"]:
            _hide()
            return
        snapped = day if day in feed["days"] else _nearest_day(feed["days"], _x(day))
        if snapped is None:
            _hide()
            return
        if state["day"] == snapped:
            return
        state["day"] = snapped
        popup.set_rows(feed["series"], snapped, feed["colors"])
        _move_line(snapped)
        if local is None:
            popup.adjustSize()
            popup.move(12, 12)
            popup.show()
            popup.raise_()
        else:
            _place(local)

    def _handle(pos) -> None:
        bounds = item.vb.sceneBoundingRect()
        if bounds.isEmpty() or not bounds.contains(pos):
            _hide()
            return
        when = _nearest_day(feed["days"], float(item.vb.mapSceneToView(pos).x()))
        if when is None:
            _hide()
            return
        mapped = plot.mapFromScene(pos)
        if hasattr(mapped, "toPoint"):
            mapped = mapped.toPoint()
        _show(when, mapped)

    def _on_move(pos) -> None:
        pending["pos"] = pos
        if not timer.isActive():
            timer.start()

    def _flush() -> None:
        pos = pending["pos"]
        pending["pos"] = None
        if pos is None:
            return
        viewport = plot.viewport()
        if not viewport.rect().contains(viewport.mapFromGlobal(QCursor.pos())):
            _hide()
            return
        _handle(pos)

    timer.timeout.connect(_flush)

    def _track_line(*_args) -> None:
        day = state["day"]
        if day is not None and not line.isHidden():
            _move_line(day)

    item.vb.sigResized.connect(_track_line)
    plot.scene().sigMouseMoved.connect(_on_move)
    plot._crosshair_timer = timer  # type: ignore[attr-defined]
    watcher = _LeaveWatcher(plot.viewport(), _hide)
    plot.viewport().installEventFilter(watcher)
    plot._crosshair_watcher = watcher  # type: ignore[attr-defined]
    plot.show_crosshair = lambda day: _show(day)  # type: ignore[attr-defined]

    def refresh(next_series, next_colors, next_focus=None) -> None:
        feed["series"] = next_series
        feed["colors"] = next_colors
        feed["days"] = sorted(
            {point.day for contract in next_series for point in contract.points}
        )
        popup.set_focus(next_focus)
        popup._widths = None
        popup._sized = False
        state["day"] = None
        line.hide()
        popup.hide()

    plot.refresh_crosshair = refresh  # type: ignore[attr-defined]


class _Crosshair(QFrame):
    """Desk-colored readout. A child of the plot so it stays above the curves."""

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setObjectName("strategyCrosshair")
        self.setFont(mono_font(11))
        self.setStyleSheet(
            "QFrame#strategyCrosshair {"
            f"background: {BG}; color: {TEXT}; border: 1px solid {HAIR};"
            "}"
            "QFrame#strategyCrosshair QLabel { background: transparent; border: none; }"
        )
        self.setWindowFlags(
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowDoesNotAcceptFocus
            | Qt.WindowType.WindowTransparentForInput
            | Qt.WindowType.NoDropShadowWindowHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(4)
        self._title = QLabel(self)
        self._title.setFont(mono_font(11))
        self._title.setStyleSheet(f"color: {MUTED};")
        layout.addWidget(self._title)
        self._grid = QGridLayout()
        self._grid.setHorizontalSpacing(self.fontMetrics().horizontalAdvance("  "))
        self._grid.setVerticalSpacing(1)
        self._grid.setContentsMargins(0, 0, 0, 0)
        layout.addLayout(self._grid)
        self._rows: list[tuple[QLabel, QLabel, QLabel, QLabel]] = []
        self._sized = False
        self._focus: str | None = None
        self._last: tuple[list[ContractSeries], date, dict[str, str]] | None = None
        self._widths: tuple[int, int, int, int] | None = None
        self._shown = -1
        self._title_chars = -1
        self.hide()

    def set_focus(self, occ: str | None) -> None:
        self._focus = occ

    def apply_focus(self, occ: str | None) -> None:
        self._focus = occ
        if self._last is not None:
            series, day, colors = self._last
            self.set_rows(series, day, colors)

    def set_rows(self, series: list[ContractSeries], day: date, colors: dict[str, str]) -> None:
        self._last = (series, day, colors)
        cells = [
            (contract.occ, cell)
            for contract in series
            if (cell := _crosshair_cells(contract, day, colors)) is not None
        ]
        if self._widths is None:
            self._widths = _column_widths(series)
        widths = self._widths
        title = day.isoformat()
        if self._focus:
            focused = next((contract for contract in series if contract.occ == self._focus), None)
            caption = "" if focused is None else _fill_caption(_landed_on(focused, day))
            if caption:
                title = f"{title}  {caption}"
        self._title.setText(title)
        while len(self._rows) < len(cells):
            labels = []
            row_index = len(self._rows)
            aligns = (
                Qt.AlignmentFlag.AlignLeft,
                Qt.AlignmentFlag.AlignRight,
                Qt.AlignmentFlag.AlignRight,
                Qt.AlignmentFlag.AlignRight,
            )
            for column in range(4):
                label = QLabel(self)
                label.setFont(mono_font(11))
                label.setAlignment(aligns[column] | Qt.AlignmentFlag.AlignVCenter)
                self._grid.addWidget(label, row_index, column)
                labels.append(label)
            row = (labels[0], labels[1], labels[2], labels[3])
            self._rows.append(row)
            if self._sized:
                for column, label in enumerate(row):
                    label.setFixedWidth(self._px[column])
        plain = [title]
        pnl_colors: list[tuple[str, str]] = []
        if not self._sized:
            metrics = self.fontMetrics()
            self._px = [metrics.horizontalAdvance("0" * width) for width in widths]
            for row in self._rows:
                for column, label in enumerate(row):
                    label.setFixedWidth(self._px[column])
            self._sized = True
        for labels, (occ, (name, qty, model, pnl, name_color, pnl_color)) in zip(self._rows, cells):
            fields = (name, qty, model, pnl)
            if self._focus is not None and occ != self._focus:
                paints = (MUTED, MUTED, MUTED, MUTED)
            else:
                paints = (name_color, name_color, name_color, pnl_color)
            for column, label in enumerate(labels):
                if label.text() != fields[column]:
                    label.setText(fields[column])
                if label.property("ink") != paints[column]:
                    label.setProperty("ink", paints[column])
                    label.setStyleSheet(
                        f"color: {paints[column]}; background: transparent; border: none;"
                    )
                if label.isHidden():
                    label.show()
            plain.append(
                f"{name:<{widths[0]}}  {qty:>{widths[1]}}  {model:>{widths[2]}}  {pnl:>{widths[3]}}"
            )
            pnl_colors.append((pnl, paints[3]))
        for extra in self._rows[len(cells) :]:
            for label in extra:
                label.hide()
                label.clear()
        self.setProperty("crosshairPlain", "\n".join(plain))
        self.setProperty("crosshairPnl", pnl_colors)
        if self._shown != len(cells) or self._title_chars != len(title):
            self._shown = len(cells)
            self._title_chars = len(title)
            self.adjustSize()


class _LeaveWatcher(QObject):
    def __init__(self, viewport: QWidget, hide) -> None:
        super().__init__(viewport)
        self._viewport = viewport
        self._hide = hide
        self._alive = True
        viewport.destroyed.connect(self._retire)

    def _retire(self) -> None:
        self._alive = False

    def eventFilter(self, watched, event) -> bool:  # noqa: N802
        if not self._alive or watched is not self._viewport or event.type() != QEvent.Type.Leave:
            return False
        try:
            inside = self._viewport.rect().contains(self._viewport.mapFromGlobal(QCursor.pos()))
        except RuntimeError:
            self._alive = False
            return False
        if not inside:
            try:
                self._hide()
            except RuntimeError:
                self._alive = False
        return False


def _nearest_day(days: list[date], ts: float) -> date | None:
    if not days:
        return None
    index = bisect_left(days, ts, key=lambda day: _x(day))
    if index <= 0:
        return days[0]
    if index >= len(days):
        return days[-1]
    before, after = days[index - 1], days[index]
    if abs(_x(after) - ts) < abs(_x(before) - ts):
        return after
    return before


def _point_on(contract: ContractSeries, day: date):
    """Value on this day only while the line actually crosses it."""
    points = contract.points
    if not points or day < points[0].day or day > points[-1].day:
        return None
    index = bisect_right(points, day, key=lambda point: point.day) - 1
    if index < 0:
        return None
    return points[index]


def _column_widths(series: list[ContractSeries]) -> tuple[int, int, int, int]:
    name_w = qty_w = model_w = pnl_w = 1
    for contract in series:
        name_w = max(name_w, len(_line_name(contract.occ)))
        for point in contract.points:
            qty_w = max(qty_w, len(f"{point.qty:g}"))
            if point.model is not None:
                model_w = max(model_w, len(f"{point.model:.2f}"))
            if point.pnl_pct is not None:
                pnl_w = max(pnl_w, len(f"{point.pnl_pct:+.1f}%"))
    return name_w, qty_w, model_w, pnl_w


def _pnl_color(pnl: float | None) -> str:
    if pnl is None or pnl == 0:
        return MUTED
    return GREEN if pnl > 0 else RED


def _crosshair_cells(
    contract: ContractSeries,
    day: date,
    colors: dict[str, str],
) -> tuple[str, str, str, str, str, str] | None:
    point = _point_on(contract, day)
    if point is None:
        return None
    color = colors[contract.occ]
    name = _line_name(contract.occ)
    model = "—" if point.model is None else f"{point.model:.2f}"
    pnl = "—" if point.pnl_pct is None else f"{point.pnl_pct:+.1f}%"
    return name, f"{point.qty:g}", model, pnl, color, _pnl_color(point.pnl_pct)


def _area_brush(color: str):
    import pyqtgraph as pg

    red, green, blue = pg.colorTuple(pg.mkColor(color))[:3]
    return pg.mkBrush(red, green, blue, 32)
