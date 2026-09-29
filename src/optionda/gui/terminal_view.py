"""Transcript plus an in-place live desk pane."""

from __future__ import annotations

import html
from datetime import date

from PySide6.QtCore import QSize, Qt, QUrl, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPen, QTextCursor, QTextDocument
from PySide6.QtWidgets import (
    QLabel,
    QSizePolicy,
    QSplitter,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from optionda import __version__
from optionda.gui.strategy_pane import StrategyPane, remaining_tone
from optionda.occ import OccError, parse_occ

from optionda.gui.richview import wrap_desk_html
from optionda.gui.splash import WORD, mark_html
from optionda.gui.theme import ACCENT, BG, CYAN, GREEN, HAIR, MUTED, PROMPT, RED, TEXT, mono_font

# Compact run desk. The table renders at 53 columns; one extra keeps the last glyph off the splitter.
_DESK_COLS = 54
_CHART_MIN_PX = 280


class _Pane(QTextBrowser):
    contract_clicked = Signal(str)

    def __init__(self, parent: QWidget | None = None, name: str = "term") -> None:
        super().__init__(parent)
        self.setObjectName(name)
        self.setReadOnly(True)
        self.setUndoRedoEnabled(False)
        self.setOpenLinks(False)
        self.setOpenExternalLinks(False)
        self.setFont(mono_font(12))
        self.document().setDefaultFont(mono_font(12))
        self.setLineWrapMode(QTextBrowser.LineWrapMode.NoWrap)
        self.setAcceptRichText(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setFrameShape(QTextBrowser.Shape.NoFrame)
        self.document().setDocumentMargin(0)
        self.document().setDefaultStyleSheet("a { text-decoration: none; }")
        self.anchorClicked.connect(self._emit_contract)

    def _emit_contract(self, url: QUrl) -> None:
        href = url.toString()
        if href.startswith("optionda:"):
            self.contract_clicked.emit(href.removeprefix("optionda:"))

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(100, 80)

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        return QSize(0, 0)


class _DashFrame(QWidget):
    """Qt frame that fills the live pane, drawn as a terminal-style dashed box."""

    def __init__(self, child: QWidget, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("deskFrame")
        self._frame = True
        child.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(0)
        layout.addWidget(child)

    def paintEvent(self, event) -> None:  # noqa: N802
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        pen = QPen(QColor(PROMPT))
        pen.setWidth(1)
        pen.setCosmetic(True)
        pen.setDashPattern([3.0, 3.0])
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        box = self.rect().adjusted(2, 2, -3, -3)
        painter.drawRoundedRect(box, 6, 6)
        painter.end()


def measure_html_cell(font: QFont) -> tuple[float, float]:
    """Glyph size as QTextEdit actually paints the desk HTML."""
    probe = wrap_desk_html(("0" * 80) + "\n0")
    doc = QTextDocument()
    doc.setDefaultFont(font)
    doc.setDocumentMargin(0)
    doc.setHtml(probe)
    advance = float(doc.idealWidth()) / 80.0
    line = float(doc.size().height()) / 2.0
    return max(advance, 4.0), max(line, 10.0)


def _splash_label(
    text: str,
    name: str,
    color: str,
    *,
    rich: bool = False,
) -> QLabel:
    label = QLabel()
    label.setObjectName(name)
    label.setFont(mono_font(11))
    label.setAlignment(Qt.AlignmentFlag.AlignLeft)
    label.setWordWrap(False)
    label.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
    if rich:
        label.setTextFormat(Qt.TextFormat.RichText)
        label.setStyleSheet("background: transparent;")
    else:
        label.setTextFormat(Qt.TextFormat.PlainText)
        label.setStyleSheet(f"color: {color}; background: transparent;")
    label.setText(text)
    return label


class _ChromeLine(QWidget):
    """Spinner line. Text changes repaint this strip only, not the chart beside it."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("liveChrome")
        self._text = ""
        self.setFont(mono_font(12))
        self.setFixedHeight(max(self.fontMetrics().height() + 4, 16))
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)

    def text(self) -> str:
        return self._text

    def setText(self, text: str) -> None:  # noqa: N802
        if text == self._text:
            return
        self._text = text
        self.update()

    def clear(self) -> None:
        self.setText("")

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(BG))
        painter.setFont(self.font())
        painter.setPen(QColor(TEXT))
        painter.drawText(
            self.rect(),
            int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
            self._text,
        )


class _DeskList(QWidget):
    """Run desk rows. A paint, not a text document, so refresh and clicks stay cheap."""

    contract_clicked = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("deskList")
        self._font = mono_font(12)
        self._bold = QFont(self._font)
        self._bold.setBold(True)
        self.setFont(self._font)
        regular = self.fontMetrics()
        bold_metrics = QFontMetrics(self._bold)
        self._line_h = max(regular.height(), bold_metrics.height(), 1)
        self._ascent = max(regular.ascent(), bold_metrics.ascent())
        self._lines: tuple = ()
        self._content_h = 0
        self._scroll = 0
        self._colors: dict[str, QColor] = {}
        self.setMouseTracking(True)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, True)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)
        self.hide()

    def sizeHint(self) -> QSize:  # noqa: N802
        if self._content_h:
            return QSize(0, self._content_h)
        return QSize(0, 0)

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        return self.sizeHint()

    def line_count(self) -> int:
        return len(self._lines)

    def plain_text(self) -> str:
        rows = []
        for _occ, spans in self._lines:
            rows.append("".join(span[0] for span in spans))
        return "\n".join(rows)

    def clear(self) -> None:
        self.set_lines(())

    def set_lines(self, lines) -> None:
        packed = tuple(lines)
        if packed == self._lines:
            return
        self._lines = packed
        self._clamp_scroll()
        self.update()

    def _ink(self, color: str) -> QColor:
        found = self._colors.get(color)
        if found is None:
            found = QColor(color)
            self._colors[color] = found
        return found

    def _clamp_scroll(self) -> None:
        limit = max(0, self._line_h * len(self._lines) - self.height())
        if self._scroll > limit:
            self._scroll = limit
        if self._scroll < 0:
            self._scroll = 0

    def _occ_at(self, y: float) -> str:
        if self._line_h <= 0:
            return ""
        index = int((y + self._scroll) // self._line_h)
        if index < 0 or index >= len(self._lines):
            return ""
        return str(self._lines[index][0] or "")

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(BG))
        top = -self._scroll
        for _occ, spans in self._lines:
            if top + self._line_h >= 0 and top < self.height():
                x = 0
                baseline = top + self._ascent
                for text, fg, bg, bold in spans:
                    font = self._bold if bold else self._font
                    painter.setFont(font)
                    width = painter.fontMetrics().horizontalAdvance(text)
                    if bg:
                        painter.fillRect(x, top, width, self._line_h, self._ink(bg))
                    painter.setPen(self._ink(fg))
                    painter.drawText(x, baseline, text)
                    x += width
            top += self._line_h
            if top > self.height():
                break
        painter.end()

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        y = event.position().y() if hasattr(event, "position") else event.y()
        want = (
            Qt.CursorShape.PointingHandCursor
            if self._occ_at(y)
            else Qt.CursorShape.ArrowCursor
        )
        if self.cursor().shape() != want:
            self.setCursor(want)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            y = event.position().y() if hasattr(event, "position") else event.y()
            occ = self._occ_at(y)
            if occ:
                self.contract_clicked.emit(occ)
                event.accept()
                return
        super().mouseReleaseEvent(event)

    def wheelEvent(self, event) -> None:  # noqa: N802
        delta = event.angleDelta().y()
        if delta:
            notches = delta / 120.0
            self._scroll -= int(notches * self._line_h * 3)
            self._clamp_scroll()
            self.update()
        event.accept()

    def resizeEvent(self, event) -> None:  # noqa: N802
        self._clamp_scroll()
        super().resizeEvent(event)


def share_tone(label: str) -> str:
    """Share ink: above 8% red, 5–8% yellow, below 5% blue. A flat book is gray."""
    text = label.strip()
    if text == "0%":
        return MUTED
    if text.startswith("<"):
        return ACCENT
    number = text[:-1] if text.endswith("%") else text
    try:
        pct = int(number)
    except ValueError:
        return MUTED
    if pct > 8:
        return RED
    if pct >= 5:
        return PROMPT
    return ACCENT


class _BookPanel(QWidget):
    """Legend, hold, and realized cash for the contracts on the chart."""

    contract_clicked = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("strategyBook")
        self._font = mono_font(12)
        self.setFont(self._font)
        metrics = self.fontMetrics()
        self._line_h = max(metrics.height(), 1)
        self._ascent = metrics.ascent()
        self._rows: list[tuple[str, str, str, str, str]] = []
        self._focus = ""
        self._as_of = date.today()
        self._scroll = 0
        self._ink: dict[str, QColor] = {}
        self.setMouseTracking(True)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, True)
        self.hide()

    def set_rows(self, rows, focus: str | None = None, as_of: date | None = None) -> None:
        self._rows = list(rows)
        self._focus = focus or ""
        if as_of is not None:
            self._as_of = as_of
        self._clamp_scroll()
        self.update()

    def plain_text(self) -> str:
        lines = ["held  realized  share"]
        for row in self._rows:
            _occ, name, _color, held, money, share = self._fields(row)
            lines.append(f"{name}  {held}  {money}  {share}".rstrip())
        return "\n".join(lines)

    def _color(self, token: str) -> QColor:
        found = self._ink.get(token)
        if found is None:
            found = QColor(token)
            self._ink[token] = found
        return found

    def _clamp_scroll(self) -> None:
        height = self._line_h * (len(self._rows) + 1)
        limit = max(0, height - self.height())
        self._scroll = min(max(self._scroll, 0), limit)

    def _row_at(self, y: float) -> str:
        index = int((y + self._scroll) // self._line_h) - 1
        if index < 0 or index >= len(self._rows):
            return ""
        return self._rows[index][0]

    @staticmethod
    def _fields(row) -> tuple[str, str, str, str, str, str]:
        share = row[5] if len(row) > 5 else ""
        return row[0], row[1], row[2], row[3], row[4], share

    def _column_layout(self, metrics: QFontMetrics, width: int) -> tuple[int, int, int, int, int, int, int]:
        """Name, held, realized, then share. Widths come from the draw font."""
        em = max(metrics.horizontalAdvance("0"), 1)
        gap = em * 3
        pad = em + 4
        held_w = metrics.horizontalAdvance("held")
        money_w = metrics.horizontalAdvance("realized")
        share_w = metrics.horizontalAdvance("share")
        for row in self._rows:
            _occ, _name, _color, held, money, share = self._fields(row)
            if held:
                held_w = max(held_w, metrics.horizontalAdvance(held))
            if money:
                money_w = max(money_w, metrics.horizontalAdvance(money))
            if share:
                share_w = max(share_w, metrics.horizontalAdvance(share))
        held_w = max(held_w, em * 5)
        money_w = max(money_w, em * 8)
        share_w = max(share_w, em * 4)
        right = max(pad, width - pad)
        share_x = right - share_w
        money_x = share_x - gap - money_w
        held_x = money_x - gap - held_w
        name_right = held_x - gap
        return name_right, held_x, held_w, money_x, money_w, share_x, share_w

    def paintEvent(self, _event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setFont(self._font)
        metrics = painter.fontMetrics()
        painter.fillRect(self.rect(), self._color(BG))
        painter.fillRect(0, 0, self.width(), 1, self._color(HAIR))
        name_right, held_x, held_w, money_x, money_w, share_x, share_w = self._column_layout(
            metrics, self.width()
        )
        top = -self._scroll
        painter.setPen(self._color(MUTED))
        baseline = top + self._ascent
        self._draw_fit(painter, metrics, held_x, held_w, baseline, "held")
        self._draw_fit(painter, metrics, money_x, money_w, baseline, "realized")
        self._draw_fit(painter, metrics, share_x, share_w, baseline, "share")
        top += self._line_h
        dot = metrics.horizontalAdvance("● ")
        for row in self._rows:
            occ, name, color, held, money, share = self._fields(row)
            if top + self._line_h >= 0 and top < self.height():
                baseline = top + self._ascent
                dim = bool(self._focus) and occ != self._focus
                painter.setPen(self._color(color))
                painter.drawText(4, baseline, "●")
                room = name_right - 4 - dot
                label = name
                if room > 0 and metrics.horizontalAdvance(label) > room:
                    label = metrics.elidedText(label, Qt.TextElideMode.ElideRight, int(room))
                if room > 0 and label:
                    painter.setClipRect(0, int(top), max(int(name_right), 0), self._line_h)
                    painter.setPen(self._color(MUTED if dim else color))
                    painter.drawText(4 + dot, baseline, label)
                    painter.setClipping(False)
                self._draw_held(painter, metrics, held_x, held_w, baseline, occ, held, dim)
                if money:
                    tone = GREEN if money.startswith("+") else RED if money.startswith("-") else MUTED
                    painter.setPen(self._color(MUTED if dim else tone))
                    self._draw_fit(painter, metrics, money_x, money_w, baseline, money)
                if share:
                    tone = share_tone(share)
                    painter.setPen(self._color(MUTED if dim else tone))
                    self._draw_fit(painter, metrics, share_x, share_w, baseline, share)
            top += self._line_h
            if top > self.height():
                break
        painter.end()

    def _draw_held(
        self,
        painter: QPainter,
        metrics: QFontMetrics,
        x: int,
        width: int,
        baseline: int,
        occ: str,
        held: str,
        dim: bool,
    ) -> None:
        mark = held.rfind(" (")
        if mark < 0:
            painter.setPen(self._color(MUTED))
            self._draw_fit(painter, metrics, x, width, baseline, held)
            return
        prefix = held[:mark]
        suffix = held[mark:]
        tone = MUTED if dim else self._remaining_ink(occ)
        full = metrics.horizontalAdvance(held)
        origin = int(x + max(0, width - full))
        painter.setPen(self._color(MUTED))
        if prefix:
            painter.drawText(origin, baseline, prefix)
        painter.setPen(self._color(tone))
        painter.drawText(origin + metrics.horizontalAdvance(prefix), baseline, suffix)

    def _remaining_ink(self, occ: str) -> str:
        try:
            expiry = parse_occ(occ).expiry
        except OccError:
            return MUTED
        return remaining_tone(self._as_of, expiry)

    @staticmethod
    def _draw_fit(painter: QPainter, metrics: QFontMetrics, x: int, width: int, baseline: int, text: str) -> None:
        if not text or width <= 0:
            return
        advance = metrics.horizontalAdvance(text)
        painter.drawText(int(x + max(0, width - advance)), baseline, text)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        y = event.position().y() if hasattr(event, "position") else event.y()
        want = (
            Qt.CursorShape.PointingHandCursor
            if self._row_at(y)
            else Qt.CursorShape.ArrowCursor
        )
        if self.cursor().shape() != want:
            self.setCursor(want)
        super().mouseMoveEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        if self.cursor().shape() != Qt.CursorShape.ArrowCursor:
            self.setCursor(Qt.CursorShape.ArrowCursor)
        super().leaveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            y = event.position().y() if hasattr(event, "position") else event.y()
            occ = self._row_at(y)
            if occ:
                self.contract_clicked.emit(occ)
                event.accept()
                return
        super().mouseReleaseEvent(event)

    def wheelEvent(self, event) -> None:  # noqa: N802
        delta = event.angleDelta().y()
        if delta:
            self._scroll -= int(delta / 120.0 * self._line_h * 3)
            self._clamp_scroll()
            self.update()
        event.accept()

    def resizeEvent(self, event) -> None:  # noqa: N802
        self._clamp_scroll()
        super().resizeEvent(event)


class TerminalView(QWidget):
    def __init__(self, parent: QWidget | None = None, *, splash: bool = False) -> None:
        super().__init__(parent)
        self.history = _Pane(self, "term")
        self.live = _Pane(self, "termLive")
        self._status = _ChromeLine()
        self._status.hide()
        inner = QWidget()
        inner.setMinimumWidth(0)
        inner.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Expanding)
        inner_l = QVBoxLayout(inner)
        inner_l.setContentsMargins(0, 0, 0, 0)
        inner_l.setSpacing(0)
        self._left = inner_l
        inner_l.addWidget(self._status, 0)
        self.desk_list = _DeskList()
        self.book = _BookPanel()
        inner_l.addWidget(self.desk_list, 1)
        inner_l.addWidget(self.book, 1)
        inner_l.addWidget(self.live, 1)
        self.strategy = StrategyPane(self)
        self.strategy.hide()
        self.live.contract_clicked.connect(self.strategy.focus_contract)
        self.desk_list.contract_clicked.connect(self.strategy.focus_contract)
        self.book.contract_clicked.connect(self.strategy.focus_contract)
        self.strategy.set_book_sink(self._show_book)
        self._split = QSplitter(Qt.Orientation.Horizontal)
        self._split.setObjectName("deskSplit")
        self._split.setChildrenCollapsible(False)
        self._split.addWidget(inner)
        self._split.addWidget(self.strategy)
        self._split.setStretchFactor(0, 0)
        self._split.setStretchFactor(1, 1)
        handle = self._split.handle(1)
        if handle is not None:
            handle.setEnabled(False)
        self.strategy.setMinimumSize(0, 0)
        self.strategy.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        self.desk = _DashFrame(self._split, self)
        self.desk.hide()
        self._splash = QWidget(self)
        self._splash.setObjectName("splash")
        splash_l = QVBoxLayout(self._splash)
        splash_l.setContentsMargins(16, 16, 16, 16)
        splash_l.setSpacing(18)
        mark = _splash_label(mark_html(), "splashMark", MUTED, rich=True)
        word = _splash_label(WORD, "splashWord", CYAN)
        version = _splash_label(__version__, "splashVersion", TEXT)
        upper = QWidget()
        upper.setObjectName("splashLockup")
        upper_l = QVBoxLayout(upper)
        upper_l.setContentsMargins(0, 0, 0, 0)
        upper_l.addStretch(1)
        upper_l.addWidget(mark, 0, Qt.AlignmentFlag.AlignHCenter)
        lower = QWidget()
        lower_l = QVBoxLayout(lower)
        lower_l.setContentsMargins(0, 0, 0, 0)
        lower_l.addWidget(word, 0, Qt.AlignmentFlag.AlignHCenter)
        lower_l.addWidget(version, 0, Qt.AlignmentFlag.AlignHCenter)
        lower_l.addStretch(1)
        splash_l.addWidget(upper, 1)
        splash_l.addWidget(lower, 1)
        self._splash.setVisible(splash)
        self._chrome: dict = {}
        self._chrome_slot = False
        self._cell: tuple[float, float] | None = None
        self.history.setLineWrapMode(QTextBrowser.LineWrapMode.WidgetWidth)
        self.history.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
        )
        self.desk.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored
        )
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self.history, 0)
        layout.addWidget(self._splash, 1)
        layout.addWidget(self.desk, 1)
        if splash:
            self.history.hide()
        self.set_strategy_visible(False)
        self._fit_history()

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._fit_history()
        self._lock_split()
        if not self.book.isHidden():
            self._pin_desk_above_book()

    def splash_visible(self) -> bool:
        return not self._splash.isHidden()

    def hide_splash(self) -> None:
        if self._splash.isHidden():
            return
        self._splash.hide()
        if self.history.isHidden():
            self.history.show()

    def locked_left_width(self) -> int:
        """Pixel width that fits contract, spot, cost, and model."""
        advance, _line = self._cell_size()
        return int(advance * _DESK_COLS) + 4

    def set_strategy_visible(self, visible: bool) -> None:
        self.strategy.setVisible(visible)
        if not visible:
            self.book.hide()
            self._unpin_desk_list()
        else:
            self.strategy.publish_book()
        self._lock_split()

    def _show_book(self, rows, focus: str | None = None, as_of: date | None = None) -> None:
        self.book.set_rows(rows, focus, as_of=as_of)
        show = self.strategy.isVisible() and bool(rows)
        self.book.setVisible(show)
        if not show:
            self._unpin_desk_list()
            return
        self.live.hide()
        self._pin_desk_above_book()

    def _pin_desk_above_book(self) -> None:
        """Keep every desk row above the book. The book only fills what is left."""
        line_h = max(self.desk_list._line_h, 1)
        content = line_h * self.desk_list.line_count()
        if content <= 0 or self.book.isHidden():
            return
        host = self._left.parentWidget()
        status_h = self._status.height() if self._status.isVisible() else 0
        room = max((host.height() if host is not None else 0) - status_h, 0)
        book_min = self.book._line_h * 3
        if room <= 0 or content + book_min <= room:
            height = content
        else:
            height = max(line_h, ((room - book_min) // line_h) * line_h)
            height = min(height, content)
        if self.desk_list._content_h == height and self.desk_list.height() == height:
            return
        self.desk_list._content_h = height
        self.desk_list.setFixedHeight(height)
        self.desk_list.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed
        )
        self.desk_list.updateGeometry()
        self._left.setStretch(self._left.indexOf(self.desk_list), 0)
        self._left.setStretch(self._left.indexOf(self.book), 1)
        self._left.activate()

    def _unpin_desk_list(self) -> None:
        self.desk_list._content_h = 0
        self.desk_list.setMinimumHeight(0)
        self.desk_list.setMaximumHeight(16777215)
        self.desk_list.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored
        )
        self.desk_list.updateGeometry()
        self._left.setStretch(self._left.indexOf(self.desk_list), 1)

    def _lock_split(self) -> None:
        total = self._split.width()
        handle = self._split.handleWidth()
        usable = total - handle
        if usable < 2:
            return
        if self.strategy.isHidden():
            sizes = [usable, 0]
        else:
            left = self.locked_left_width()
            right = usable - left
            if right < _CHART_MIN_PX:
                right = min(_CHART_MIN_PX, max(usable // 2, 1))
                left = usable - right
            sizes = [left, right]
        if self._split.sizes() == sizes:
            return
        self._split.setSizes(sizes)

    def prepare_live(self) -> None:
        self.hide_splash()
        if self.history.isHidden():
            self.history.show()
        self._fit_history()
        if self.desk.isHidden():
            self.desk.show()
        if self.live.isHidden():
            self.live.show()
        if self.layout() is not None:
            self.layout().activate()

    def _cell_size(self) -> tuple[float, float]:
        if self._cell is None:
            self._cell = measure_html_cell(self.live.font())
        return self._cell

    def _pane_pixels(self) -> tuple[int, int]:
        self.prepare_live()
        view = self.live.viewport()
        width = view.width()
        height = view.height()
        if width < 20:
            width = max(self.desk.width() - 16, self.contentsRect().width(), 1)
        if height < 20:
            height = max(self.desk.height() - 16, 1)
        return width, height

    def char_size(self) -> tuple[int, int]:
        advance, line = self._cell_size()
        px_w, px_h = self._pane_pixels()
        cols = max(int((px_w - 8) / advance), 40)
        rows = max(int((px_h - 8) / line), 8)
        return cols, rows

    def char_width(self) -> int:
        return self.char_size()[0]

    def _fit_history(self) -> None:
        width = self.history.viewport().width()
        if width > 20:
            self.history.document().setTextWidth(width)
        doc_h = int(self.history.document().size().height())
        _, line = self._cell_size()
        cap = int(line) * 8 + 8
        self.history.setFixedHeight(max(min(doc_h + 8, cap), int(line) + 8))

    def append_block(self, text: str) -> None:
        if not text:
            return
        self.hide_splash()
        if self.history.isHidden():
            self.history.show()
        cursor = self.history.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        if self.history.toPlainText():
            cursor.insertHtml("<br>")
        escaped = html.escape(text).replace("\n", "<br>")
        cursor.insertHtml(wrap_desk_html(escaped, wrap=True))
        cursor.movePosition(QTextCursor.MoveOperation.Start)
        self.history.setTextCursor(cursor)
        self._fit_history()

    def pin_live_chrome(self) -> None:
        """Keep a one-line status slot after the first table is on screen."""
        self._chrome_slot = True
        if not self._status.text().strip():
            text = str(self._chrome.get("text") or "").strip()
            if not text:
                from optionda.display.table import format_chrome_plain

                text = format_chrome_plain(
                    spin=self._chrome.get("spin"),
                    poll_label=self._chrome.get("poll_label"),
                    poll_busy=bool(self._chrome.get("poll_busy")),
                    poll_done=self._chrome.get("poll_done"),
                    poll_total=self._chrome.get("poll_total"),
                    eta_sec=self._chrome.get("eta"),
                )
            self._status.setText(text or " ")
        self._status.show()

    def _table_on_screen(self) -> bool:
        if not self.desk_list.isHidden() and self.desk_list.line_count():
            return True
        return bool(self.live.toPlainText())

    def set_live_frame(self, payload) -> None:
        if isinstance(payload, (list, tuple)):
            self.set_live_lines(payload)
        else:
            self.set_live_html(str(payload))

    def set_live_lines(self, lines) -> None:
        """Paint the run list. Later refreshes only repaint this widget."""
        self.desk_list.set_lines(lines)
        if not self.live.isHidden():
            self.live.hide()
        if self.desk_list.isHidden():
            self.desk_list.show()
        if not self.book.isHidden():
            self._pin_desk_above_book()
        if self.desk.isHidden():
            self.desk.show()

    def set_live_html(self, markup: str, *, wrap: bool = False) -> None:
        self.live.setLineWrapMode(
            QTextBrowser.LineWrapMode.WidgetWidth
            if wrap
            else QTextBrowser.LineWrapMode.NoWrap
        )
        if not wrap and not self._chrome_slot:
            self._chrome = {}
            self._status.clear()
            self._status.hide()
        if not self.desk_list.isHidden():
            self.desk_list.hide()
        self.live.setHtml(markup)
        if self.desk.isHidden():
            self.desk.show()
        if self.live.isHidden():
            self.live.show()
        cursor = self.live.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.Start)
        self.live.setTextCursor(cursor)

    def set_live_chrome(self, chrome: dict, *, keep_table: bool = True) -> None:
        from optionda.display.table import format_chrome_plain
        from optionda.gui.richview import wrap_desk_html

        self._chrome = dict(chrome)
        body = chrome.get("html")
        if body and (chrome.get("page") or chrome.get("rows")):
            self.set_live_html(wrap_desk_html(str(body), wrap=True), wrap=True)
            self._status.clear()
            self._status.hide()
            if self.history.isHidden():
                self.history.show()
            self._fit_history()
            return
        text = str(chrome.get("text") or "").strip()
        if not text:
            text = format_chrome_plain(
                spin=chrome.get("spin"),
                poll_label=chrome.get("poll_label"),
                poll_busy=bool(chrome.get("poll_busy")),
                poll_done=chrome.get("poll_done"),
                poll_total=chrome.get("poll_total"),
                eta_sec=chrome.get("eta"),
            )
        page = bool(chrome.get("page")) or "\n" in text
        if page and not self._chrome_slot:
            escaped = html.escape(text).replace("\n", "<br>")
            self.set_live_html(wrap_desk_html(escaped, wrap=True), wrap=True)
            self._status.clear()
            self._status.hide()
            if self.history.isHidden():
                self.history.show()
            self._fit_history()
            return
        if page and self._chrome_slot:
            from optionda.display.table import format_chrome_plain

            text = format_chrome_plain(
                spin=chrome.get("spin"),
                poll_label=chrome.get("poll_label"),
                poll_busy=bool(chrome.get("poll_busy")),
                poll_done=chrome.get("poll_done"),
                poll_total=chrome.get("poll_total"),
                eta_sec=chrome.get("eta"),
            )
        self._status.setText(text or " ")
        self._status.setVisible(bool(text) or self._chrome_slot)
        if self.history.isHidden():
            self.history.show()
        if self.desk.isHidden():
            self.desk.show()
        if keep_table and self._table_on_screen():
            if not self.desk_list.isHidden():
                self.desk_list.show()
            elif self.live.toPlainText():
                self.live.show()
        elif not keep_table:
            self.live.hide()
            self.desk_list.hide()

    def bump_live_spin(self) -> bool:
        if not self._chrome.get("poll_busy"):
            return False
        from optionda.display.table import format_add_progress, spinner_frame

        tick = int(self._chrome.get("_tick") or 0) + 1
        self._chrome["_tick"] = tick
        self._chrome["spin"] = spinner_frame(tick)
        rows = self._chrome.get("rows")
        if isinstance(rows, list):
            from optionda.display.table import format_add_rows

            self._chrome["page"] = True
            self._chrome["html"] = format_add_rows(
                rows,
                done=int(self._chrome.get("poll_done") or 0),
                active=self._chrome.get("active"),
                tick=tick,
                spin=self._chrome["spin"],
                note=self._chrome.get("note"),
            )
            self.set_live_chrome(self._chrome, keep_table=True)
            return True
        page = bool(self._chrome.get("page")) or "\n" in str(self._chrome.get("text") or "")
        if page:
            from optionda.display.table import format_load_progress

            self._chrome["page"] = True
            formatter = (
                format_load_progress
                if self._chrome.get("explain")
                else format_add_progress
            )
            self._chrome["text"] = formatter(
                spin=self._chrome["spin"],
                label=self._chrome.get("poll_label"),
                done=self._chrome.get("poll_done"),
                total=self._chrome.get("poll_total"),
            )
        else:
            self._chrome.pop("text", None)
        keep = self._table_on_screen() or page
        self.set_live_chrome(self._chrome, keep_table=keep)
        return True

    def chrome_busy(self) -> bool:
        return bool(self._chrome.get("poll_busy"))

    def show_add_result(self, markup: str) -> None:
        """Keep the dashed pane and paint the add table into it."""
        self._chrome = {}
        self._status.clear()
        self._status.hide()
        self.set_live_html(markup, wrap=False)

    def clear_live(self) -> None:
        self._chrome = {}
        self._chrome_slot = False
        self._status.clear()
        self._status.hide()
        self.desk_list.clear()
        self.desk_list.hide()
        self.live.clear()
        self.live.hide()
        self.desk.hide()
        if self.history.isHidden():
            self.history.show()
        self._fit_history()

    def clear_term(self) -> None:
        self.hide_splash()
        self.history.clear()
        self.clear_live()
        self._fit_history()

    def begin_turn(self, command_line: str) -> None:
        """One command, one page: drop the previous transcript first."""
        self.clear_term()
        self.append_block(command_line)
