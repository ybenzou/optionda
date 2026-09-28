import sqlite3
from pathlib import Path

from PySide6.QtWidgets import QTreeWidgetItem
from typer.testing import CliRunner

from optionda.cli import app
from optionda.sqlbrowser import (
    SqlReadError,
    discover,
    format_size,
    list_tables,
    preview_sql,
    run_query,
)

runner = CliRunner()


def _home(tmp_path: Path) -> Path:
    folder = tmp_path / "quotes"
    folder.mkdir()
    path = folder / "demo.sqlite"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE marks (id INTEGER, name TEXT)")
    conn.execute("INSERT INTO marks VALUES (1, 'SPCX')")
    conn.execute("INSERT INTO marks VALUES (2, NULL)")
    conn.commit()
    conn.close()
    return tmp_path


def test_discover_lists_tables_and_preview(tmp_path: Path) -> None:
    home = _home(tmp_path)
    found = discover(home)
    assert [item.label for item in found] == ["quotes/demo"]
    tables = list_tables(found[0].path)
    assert [(table.name, table.rows) for table in tables] == [("marks", 2)]
    result = run_query(found[0].path, preview_sql("marks"))
    assert result.columns == ["id", "name"]
    assert result.rows == [(1, "SPCX"), (2, None)]
    assert result.truncated is False


def test_writes_and_extra_statements_are_rejected(tmp_path: Path) -> None:
    home = _home(tmp_path)
    path = discover(home)[0].path
    try:
        run_query(path, "DELETE FROM marks")
    except SqlReadError as exc:
        assert "read-only" in str(exc)
    else:
        raise AssertionError("delete was accepted")
    try:
        run_query(path, "SELECT 1; SELECT 2")
    except SqlReadError as exc:
        assert "one statement" in str(exc)
    else:
        raise AssertionError("two statements were accepted")


def test_format_size() -> None:
    assert format_size(500) == "500 B"
    assert format_size(2048) == "2 KB"
    assert format_size(401_072_128) == "382.5 MB"


def test_sql_command_opens_inside_optionda(monkeypatch, tmp_path: Path) -> None:
    opened: dict[str, object] = {}

    def launch(account: str, home: Path, **kwargs: object) -> None:
        opened["home"] = home
        opened["view"] = kwargs.get("initial_view")

    monkeypatch.setattr("optionda.gui.launch.run_app", launch)
    monkeypatch.setenv("OPTIONDA_HOME", str(tmp_path))
    result = runner.invoke(app, ["sql"])
    assert result.exit_code == 0
    assert opened["home"] == tmp_path
    assert opened["view"] == "sql"


def test_sql_view_stays_inside_the_optionda_window(qtbot, tmp_path: Path) -> None:
    from optionda.gui.main_window import MainWindow

    window = MainWindow("demo", _home(tmp_path))
    qtbot.addWidget(window)
    window.show()
    window._open_sql()
    assert window._stack.currentWidget() is window._sql
    assert window.isAncestorOf(window._sql)
    assert "marks" in window._sql.editor.toPlainText()


def test_window_opens_the_first_table(qtbot, tmp_path: Path) -> None:
    from optionda.gui.sql_window import SqlWindow

    window = SqlWindow(_home(tmp_path))
    qtbot.addWidget(window)
    window.show()
    assert "marks" in window.editor.toPlainText()
    assert window.grid.rowCount() == 2
    assert window.grid.item(0, 1).text() == "SPCX"
    assert window.grid.item(1, 1).text() == "NULL"
    window.editor.setPlainText("DELETE FROM marks")
    window.run_sql()
    assert "read-only" in window.status.text()
    assert window.grid.rowCount() == 2

    item = _table_item(window.tree.invisibleRootItem(), "marks")
    assert item is not None
    window.tree.itemClicked.emit(item, 0)
    assert window.grid.item(0, 1).text() == "SPCX"


def _table_item(node: QTreeWidgetItem, name: str) -> QTreeWidgetItem | None:
    for index in range(node.childCount()):
        child = node.child(index)
        if child.text(0) == name:
            return child
        found = _table_item(child, name)
        if found is not None:
            return found
    return None
