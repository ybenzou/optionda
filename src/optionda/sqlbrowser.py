"""Read-only catalog of the desk sqlite files."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

PREVIEW_LIMIT = 200
HARD_CAP = 2000

_READ_HEADS = frozenset({"select", "with", "pragma", "explain"})
_ALLOW = frozenset(
    {
        sqlite3.SQLITE_READ,
        sqlite3.SQLITE_SELECT,
        sqlite3.SQLITE_FUNCTION,
        sqlite3.SQLITE_PRAGMA,
        sqlite3.SQLITE_TRANSACTION,
        sqlite3.SQLITE_RECURSIVE,
    }
)


class SqlReadError(Exception):
    pass


@dataclass(frozen=True)
class SqlDatabase:
    label: str
    path: Path
    size: int


@dataclass(frozen=True)
class SqlTable:
    name: str
    rows: int


@dataclass(frozen=True)
class SqlResult:
    columns: list[str]
    rows: list[tuple]
    truncated: bool


def discover(home: Path) -> list[SqlDatabase]:
    found: list[SqlDatabase] = []
    if not home.is_dir():
        return found
    for path in sorted(home.rglob("*.sqlite")):
        label = path.relative_to(home).with_suffix("").as_posix()
        found.append(SqlDatabase(label=label, path=path, size=path.stat().st_size))
    return found


def list_tables(path: Path) -> list[SqlTable]:
    conn = connect(path)
    try:
        names = [
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type = 'table' AND name NOT LIKE 'sqlite_%' "
                "ORDER BY name"
            )
        ]
        tables: list[SqlTable] = []
        for name in names:
            count = conn.execute(f"SELECT COUNT(*) FROM {quote_ident(name)}").fetchone()
            tables.append(SqlTable(name=name, rows=int(count[0])))
        return tables
    finally:
        conn.close()


def preview_sql(table: str) -> str:
    return f"SELECT * FROM {quote_ident(table)} LIMIT {PREVIEW_LIMIT}"


def run_query(path: Path, sql: str, *, cap: int = HARD_CAP) -> SqlResult:
    text = one_statement(sql)
    conn = connect(path)
    try:
        try:
            cursor = conn.execute(text)
        except sqlite3.DatabaseError as exc:
            message = str(exc)
            lowered = message.lower()
            if "not authorized" in lowered or "readonly" in lowered:
                raise SqlReadError("read-only") from exc
            raise SqlReadError(message) from exc
        if cursor.description is None:
            return SqlResult(columns=[], rows=[], truncated=False)
        columns = [str(item[0]) for item in cursor.description]
        fetched = cursor.fetchmany(cap + 1)
        truncated = len(fetched) > cap
        rows = [tuple(row) for row in fetched[:cap]]
        return SqlResult(columns=columns, rows=rows, truncated=truncated)
    finally:
        conn.close()


def one_statement(sql: str) -> str:
    text = sql.strip()
    if not text:
        raise SqlReadError("enter a SQL statement")
    if text.endswith(";"):
        text = text[:-1].rstrip()
    if ";" in text:
        raise SqlReadError("one statement at a time")
    head = text.lstrip("(").lstrip().split(None, 1)[0].lower()
    if head not in _READ_HEADS:
        raise SqlReadError("read-only: SELECT, WITH, PRAGMA, or EXPLAIN")
    return text


def quote_ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def format_size(size: int) -> str:
    if size >= 1024 * 1024:
        return f"{size / 1024 / 1024:.1f} MB"
    if size >= 1024:
        return f"{size / 1024:.0f} KB"
    return f"{size} B"


def connect(path: Path) -> sqlite3.Connection:
    uri = path.resolve().as_posix()
    conn = sqlite3.connect(f"file:{uri}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    conn.set_authorizer(_authorizer)
    return conn


def _authorizer(action, arg1, arg2, db_name, source) -> int:  # noqa: ANN001
    if action in _ALLOW:
        return sqlite3.SQLITE_OK
    return sqlite3.SQLITE_DENY
