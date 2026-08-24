"""Transcript plus an in-place live desk pane."""

from __future__ import annotations

import html

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPen, QTextCursor, QTextDocument
from PySide6.QtWidgets import QLabel, QSizePolicy, QTextEdit, QVBoxLayout, QWidget

from optionda.gui.richview import wrap_desk_html
from optionda.gui.splash import WORD, mark_html
from optionda.gui.theme import CYAN, MUTED, PROMPT, mono_font


class _Pane(QTextEdit):
    def __init__(self, parent: QWidget | None = None, name: str = "term") -> None:
        super().__init__(parent)
        self.setObjectName(name)
        self.setReadOnly(True)
        self.setUndoRedoEnabled(False)
        self.setFont(mono_font(12))
        self.document().setDefaultFont(mono_font(12))
        self.setLineWrapMode(QTextEdit.LineWrapMode.NoWrap)
        self.setAcceptRichText(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setFrameShape(QTextEdit.Shape.NoFrame)
        self.document().setDocumentMargin(0)

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


class TerminalView(QWidget):
    def __init__(self, parent: QWidget | None = None, *, splash: bool = False) -> None:
        super().__init__(parent)
        self.history = _Pane(self, "term")
        self.live = _Pane(self, "termLive")
        self._status = QLabel()
        self._status.setObjectName("liveChrome")
        self._status.setFont(mono_font(12))
        self._status.hide()
        inner = QWidget()
        inner_l = QVBoxLayout(inner)
        inner_l.setContentsMargins(0, 0, 0, 0)
        inner_l.setSpacing(0)
        inner_l.addWidget(self._status, 0)
        inner_l.addWidget(self.live, 1)
        self.desk = _DashFrame(inner, self)
        self.desk.hide()
        self._splash = QWidget(self)
        self._splash.setObjectName("splash")
        splash_l = QVBoxLayout(self._splash)
        splash_l.setContentsMargins(16, 16, 16, 16)
        splash_l.setSpacing(18)
        mark = _splash_label(mark_html(), "splashMark", MUTED, rich=True)
        word = _splash_label(WORD, "splashWord", CYAN)
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
        lower_l.addStretch(1)
        splash_l.addWidget(upper, 1)
        splash_l.addWidget(lower, 1)
        self._splash.setVisible(splash)
        self._chrome: dict = {}
        self._cell: tuple[float, float] | None = None
        self.history.setLineWrapMode(QTextEdit.LineWrapMode.WidgetWidth)
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
        self._fit_history()

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._fit_history()

    def splash_visible(self) -> bool:
        return not self._splash.isHidden()

    def hide_splash(self) -> None:
        if self._splash.isHidden():
            return
        self._splash.hide()
        if self.history.isHidden():
            self.history.show()

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

    def set_live_html(self, markup: str, *, wrap: bool = False) -> None:
        self.live.setLineWrapMode(
            QTextEdit.LineWrapMode.WidgetWidth
            if wrap
            else QTextEdit.LineWrapMode.NoWrap
        )
        if not wrap:
            self._chrome = {}
            self._status.clear()
            self._status.hide()
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

        self._chrome = dict(chrome)
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
        if page:
            escaped = html.escape(text).replace("\n", "<br>")
            self.set_live_html(wrap_desk_html(escaped, wrap=True), wrap=True)
            self._status.clear()
            self._status.hide()
            if self.history.isHidden():
                self.history.show()
            self._fit_history()
            return
        self._status.setText(text)
        self._status.setVisible(bool(text))
        if self.history.isHidden():
            self.history.show()
        if self.desk.isHidden():
            self.desk.show()
        if keep_table and self.live.toPlainText():
            self.live.show()
        elif not keep_table:
            self.live.hide()
        self._fit_history()

    def bump_live_spin(self) -> bool:
        if not self._chrome.get("poll_busy"):
            return False
        from optionda.display.table import format_add_progress, spinner_frame

        tick = int(self._chrome.get("_tick") or 0) + 1
        self._chrome["_tick"] = tick
        self._chrome["spin"] = spinner_frame(tick)
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
        keep = bool(self.live.toPlainText()) or page
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
        self._status.clear()
        self._status.hide()
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
