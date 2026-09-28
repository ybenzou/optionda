"""Read-only desk database window. Tables on the left, rows on the right."""

from __future__ import annotations

import time
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QKeyEvent
from PySide6.QtWidgets import (
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from optionda.gui.theme import BG, BRIGHT, HAIR, MUTED, RED, TEXT, mono_font
from optionda.sqlbrowser import (
    SqlDatabase,
    SqlReadError,
    SqlTable,
    discover,
    format_size,
    list_tables,
    preview_sql,
    run_query,
)

_DB_ROLE = Qt.ItemDataRole.UserRole
_TABLE_ROLE = Qt.ItemDataRole.UserRole + 1


class _SqlEdit(QPlainTextEdit):
    def __init__(self, run, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._run = run

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self._run()
            return
        super().keyPressEvent(event)


class SqlWindow(QWidget):
    def __init__(self, home: Path) -> None:
        super().__init__()
        self.home = home
        self._db: Path | None = None
        self.setObjectName("sqlView")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        split = QSplitter(Qt.Orientation.Horizontal)
        split.setChildrenCollapsible(False)
        self.tree = QTreeWidget()
        self.tree.setObjectName("sqlTree")
        self.tree.setHeaderHidden(True)
        self.tree.setColumnCount(2)
        self.tree.setFont(mono_font(11))
        self.tree.setStyleSheet(
            f"QTreeWidget {{ background: {BG}; color: {TEXT}; border: none; outline: none; }}"
            "QTreeWidget::item:selected { background: #264f78; color: "
            f"{BRIGHT}; }}"
        )
        self.tree.itemClicked.connect(self._on_click)
        split.addWidget(self.tree)

        right = QWidget()
        column = QVBoxLayout(right)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(8)
        self.editor = _SqlEdit(self.run_sql)
        self.editor.setObjectName("sqlEditor")
        self.editor.setFont(mono_font(12))
        self.editor.setFixedHeight(96)
        self.editor.setPlaceholderText('SELECT * FROM "events" LIMIT 200')
        self.editor.setStyleSheet(
            f"QPlainTextEdit {{ background: {BG}; color: {TEXT}; border: 1px solid {HAIR}; }}"
        )
        column.addWidget(self.editor)

        bar = QHBoxLayout()
        bar.setContentsMargins(0, 0, 0, 0)
        self._run = QPushButton("Run")
        self._run.setObjectName("primary")
        self._run.setFont(mono_font(11))
        self._run.clicked.connect(self.run_sql)
        self.status = QLabel("read-only")
        self.status.setObjectName("sqlStatus")
        self.status.setFont(mono_font(11))
        self.status.setStyleSheet(f"color: {MUTED};")
        bar.addWidget(self._run)
        bar.addWidget(self.status, 1)
        column.addLayout(bar)

        self.grid = QTableWidget()
        self.grid.setObjectName("sqlGrid")
        self.grid.setFont(mono_font(11))
        self.grid.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.grid.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.grid.setWordWrap(False)
        self.grid.verticalHeader().setVisible(False)
        self.grid.horizontalHeader().setStretchLastSection(True)
        self.grid.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.grid.horizontalHeader().setFont(mono_font(11))
        column.addWidget(self.grid, 1)
        split.addWidget(right)
        split.setStretchFactor(0, 0)
        split.setStretchFactor(1, 1)
        split.setSizes([280, 860])
        layout.addWidget(split, 1)
        self._fill_tree()

    def _fill_tree(self) -> None:
        self.tree.clear()
        first: QTreeWidgetItem | None = None
        for database in discover(self.home):
            try:
                tables = list_tables(database.path)
            except SqlReadError as exc:
                node = self._db_node(database, None)
                node.setText(1, str(exc))
                self.tree.addTopLevelItem(node)
                continue
            node = self._db_node(database, tables)
            self.tree.addTopLevelItem(node)
            node.setExpanded(True)
            if first is None and node.childCount():
                first = node.child(0)
        if first is not None:
            self.tree.setCurrentItem(first)
            self._open_table(first)

    def _db_node(self, database: SqlDatabase, tables: list[SqlTable] | None) -> QTreeWidgetItem:
        node = QTreeWidgetItem([database.label, format_size(database.size)])
        node.setData(0, _DB_ROLE, str(database.path))
        node.setForeground(1, QColor(MUTED))
        node.setFont(0, mono_font(11))
        node.setTextAlignment(1, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        if tables is None:
            return node
        for table in tables:
            child = QTreeWidgetItem([table.name, f"{table.rows:,}"])
            child.setData(0, _DB_ROLE, str(database.path))
            child.setData(0, _TABLE_ROLE, table.name)
            child.setForeground(1, QColor(MUTED))
            child.setTextAlignment(1, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            node.addChild(child)
        return node

    def _on_click(self, item: QTreeWidgetItem, _column: int) -> None:
        path = item.data(0, _DB_ROLE)
        if path:
            self._db = Path(str(path))
        if item.data(0, _TABLE_ROLE):
            self._open_table(item)

    def _open_table(self, item: QTreeWidgetItem) -> None:
        path = item.data(0, _DB_ROLE)
        name = item.data(0, _TABLE_ROLE)
        if not path or not name:
            return
        self._db = Path(str(path))
        self.editor.setPlainText(preview_sql(str(name)))
        self.run_sql()

    def run_sql(self) -> None:
        if self._db is None:
            self._set_status("select a database", error=True)
            return
        started = time.perf_counter()
        try:
            result = run_query(self._db, self.editor.toPlainText())
        except SqlReadError as exc:
            self._set_status(str(exc), error=True)
            return
        self._show(result.columns, result.rows)
        elapsed = (time.perf_counter() - started) * 1000
        label = self._db.relative_to(self.home).as_posix() if self._db.is_relative_to(self.home) else self._db.name
        note = f"{label}  ·  {len(result.rows):,} rows  ·  {elapsed:.0f} ms"
        if result.truncated:
            note += "  ·  more rows not shown"
        self._set_status(note, error=False)

    def _show(self, columns: list[str], rows: list[tuple]) -> None:
        self.grid.clear()
        self.grid.setColumnCount(len(columns))
        self.grid.setHorizontalHeaderLabels(columns)
        self.grid.setRowCount(len(rows))
        muted = QColor(MUTED)
        for row_index, row in enumerate(rows):
            for column, value in enumerate(row):
                if value is None:
                    item = QTableWidgetItem("NULL")
                    item.setForeground(muted)
                else:
                    item = QTableWidgetItem(str(value))
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                self.grid.setItem(row_index, column, item)
        self.grid.resizeColumnsToContents()
        header = self.grid.horizontalHeader()
        for index in range(self.grid.columnCount()):
            if header.sectionSize(index) > 360:
                header.resizeSection(index, 360)

    def _set_status(self, text: str, *, error: bool) -> None:
        self.status.setText(text)
        self.status.setStyleSheet(f"color: {RED if error else MUTED};")
