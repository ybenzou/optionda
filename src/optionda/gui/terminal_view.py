"""Transcript plus an in-place live desk pane."""

from __future__ import annotations

import html

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPen, QTextCursor, QTextDocument
from PySide6.QtWidgets import QSizePolicy, QTextEdit, QVBoxLayout, QWidget

from optionda.gui.richview import wrap_desk_html
from optionda.gui.theme import PROMPT, mono_font


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


class TerminalView(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.history = _Pane(self, "term")
        self.live = _Pane(self, "termLive")
        self.desk = _DashFrame(self.live, self)
        self.desk.hide()
        self._cell: tuple[float, float] | None = None
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
        layout.addWidget(self.desk, 1)
        self._fit_history()

    def prepare_live(self) -> None:
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
        doc_h = int(self.history.document().size().height())
        _, line = self._cell_size()
        self.history.setFixedHeight(max(doc_h + 4, int(line) + 4))

    def append_block(self, text: str) -> None:
        if not text:
            return
        if self.history.isHidden():
            self.history.show()
        cursor = self.history.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        if self.history.toPlainText():
            cursor.insertHtml("<br>")
        escaped = html.escape(text).replace("\n", "<br>")
        cursor.insertHtml(wrap_desk_html(escaped))
        self.history.setTextCursor(cursor)
        self._fit_history()

    def set_live_html(self, markup: str) -> None:
        self.live.setHtml(markup)
        if self.desk.isHidden():
            self.desk.show()
        if self.live.isHidden():
            self.live.show()
        cursor = self.live.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.Start)
        self.live.setTextCursor(cursor)

    def clear_live(self) -> None:
        self.live.clear()
        self.live.hide()
        self.desk.hide()
        if self.history.isHidden():
            self.history.show()
        self._fit_history()

    def clear_term(self) -> None:
        self.history.clear()
        self.clear_live()
        self._fit_history()
