"""Latest and historical desk quotes.

The run screen reads the latest row immediately. Each poll appends a
history row for later trade review. The journal stays the trade ledger.
"""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import date, datetime, timezone
from pathlib import Path

from optionda.models import Position, RowMark
from optionda.occ import OccError, parse_occ
from optionda.paths import ensure_home

_TEXT = {
    "ts",
    "position_id",
    "occ",
    "underlying",
    "expiry",
    "option_type",
    "side",
    "valuation_mode",
    "surface_session",
    "last_op_at",
    "spot_source",
    "error",
    "source",
}
_INT = {"iv_stale", "iv_fallback"}
_COLUMNS = (
    "ts",
    "position_id",
    "occ",
    "underlying",
    "expiry",
    "strike",
    "option_type",
    "qty",
    "side",
    "iv",
    "cost",
    "spot",
    "model",
    "upnl",
    "pnl_pct",
    "notional",
    "delta",
    "dte",
    "close_spot",
    "close_premium",
    "valuation_mode",
    "model_iv",
    "iv_stale",
    "iv_fallback",
    "surface_session",
    "last_op_at",
    "spot_source",
    "error",
    "source",
    "live",
)


def quote_db_path(account: str, home: Path | None = None) -> Path:
    folder = ensure_home(home) / "quotes"
    folder.mkdir(parents=True, exist_ok=True)
    return folder / f"{account}.sqlite"


def save_quotes(
    account: str,
    rows: list[RowMark],
    *,
    home: Path | None = None,
    ts: datetime | None = None,
    source: str = "live",
) -> None:
    path = quote_db_path(account, home)
    when = (ts or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat()
    conn = _connect(path)
    try:
        _ensure_schema(conn)
        if not rows:
            conn.execute("DELETE FROM latest")
            conn.commit()
            return
        records = [_record(when, row, source) for row in rows]
        placeholders = ", ".join("?" for _ in _COLUMNS)
        names = ", ".join(_COLUMNS)
        conn.executemany(
            f"INSERT INTO quotes ({names}) VALUES ({placeholders})",
            records,
        )
        _upsert_latest(conn, names, placeholders, records)
        conn.commit()
    finally:
        conn.close()


def load_latest_rows(account: str, home: Path | None = None) -> list[RowMark]:
    path = ensure_home(home) / "quotes" / f"{account}.sqlite"
    if not path.exists():
        return []
    conn = _connect(path)
    try:
        _ensure_schema(conn)
        found = conn.execute(
            "SELECT * FROM latest ORDER BY underlying, expiry, strike, occ"
        ).fetchall()
    finally:
        conn.close()
    return [_row_mark(row) for row in found]


def quote_count(account: str, home: Path | None = None) -> int:
    path = ensure_home(home) / "quotes" / f"{account}.sqlite"
    if not path.exists():
        return 0
    conn = _connect(path)
    try:
        _ensure_schema(conn)
        row = conn.execute("SELECT COUNT(*) AS n FROM quotes").fetchone()
    finally:
        conn.close()
    return int(row["n"] if row is not None else 0)


def _connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
    )
    existing = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'latest'"
    ).fetchone()
    if existing is not None and "occ TEXT PRIMARY KEY" not in str(existing[0]):
        conn.execute("DROP TABLE latest")
    quote_cols = ", ".join(f"{name} {_sql_type(name)}" for name in _COLUMNS)
    latest_cols = ", ".join(_latest_column(name) for name in _COLUMNS)
    conn.execute(f"CREATE TABLE IF NOT EXISTS quotes ({quote_cols})")
    conn.execute(f"CREATE TABLE IF NOT EXISTS latest ({latest_cols})")
    _add_missing_columns(conn, "quotes")
    _add_missing_columns(conn, "latest")
    conn.execute("CREATE INDEX IF NOT EXISTS quotes_ts ON quotes(ts)")
    conn.execute("CREATE INDEX IF NOT EXISTS quotes_occ_ts ON quotes(occ, ts)")


def _add_missing_columns(conn: sqlite3.Connection, table: str) -> None:
    found = {str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})")}
    for name in _COLUMNS:
        if name not in found:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {_sql_type(name)}")


def _latest_column(name: str) -> str:
    kind = _sql_type(name)
    if name == "occ":
        return f"{name} {kind} PRIMARY KEY"
    return f"{name} {kind}"


def _upsert_latest(
    conn: sqlite3.Connection,
    names: str,
    placeholders: str,
    records: list[tuple],
    *,
    prune: bool = True,
) -> None:
    if not records:
        if prune:
            conn.execute("DELETE FROM latest")
        return
    assignments = ", ".join(
        f"{name} = excluded.{name}" for name in _COLUMNS if name != "occ"
    )
    conn.executemany(
        f"INSERT INTO latest ({names}) VALUES ({placeholders}) "
        f"ON CONFLICT(occ) DO UPDATE SET {assignments}",
        records,
    )
    if not prune:
        return
    keep = [record[2] for record in records]
    conn.execute(
        f"DELETE FROM latest WHERE occ NOT IN ({', '.join('?' for _ in keep)})",
        keep,
    )


def _sql_type(name: str) -> str:
    if name in _TEXT:
        return "TEXT"
    if name in _INT:
        return "INTEGER"
    return "REAL"


def _record(ts: str, row: RowMark, source: str = "live") -> tuple:
    pos = row.position
    cost = row.cost if row.cost is not None else pos.entry_premium
    model = row.theo
    pnl = None
    if model is not None and cost not in (None, 0):
        pnl = (model - cost) / cost * 100.0
    session = row.surface_session_date.isoformat() if row.surface_session_date else None
    return (
        ts,
        pos.id,
        pos.occ_symbol,
        pos.underlying,
        pos.expiry.isoformat(),
        pos.strike,
        pos.option_type,
        pos.qty,
        pos.side,
        pos.iv_frozen,
        cost,
        row.spot,
        model,
        row.upnl,
        pnl,
        row.notional,
        row.delta,
        row.dte,
        row.close_spot,
        row.close_premium,
        row.valuation_mode,
        row.model_iv if row.model_iv is not None else pos.iv_frozen,
        1 if row.iv_stale else 0,
        1 if row.iv_fallback else 0,
        session,
        row.last_op_at.isoformat() if row.last_op_at is not None else None,
        row.spot_source,
        row.error,
        source,
        row.live,
    )


def _row_mark(row: sqlite3.Row) -> RowMark:
    opened = _parse_dt(row["ts"]) or datetime.now(timezone.utc)
    mode = row["valuation_mode"] if row["valuation_mode"] in {"surface", "frozen"} else "frozen"
    cost = _float(row["cost"])
    iv = _float(row["iv"])
    session = row["surface_session"]
    return RowMark(
        position=Position(
            id=str(row["position_id"]),
            occ_symbol=str(row["occ"]),
            underlying=str(row["underlying"]),
            expiry=date.fromisoformat(str(row["expiry"])),
            strike=float(row["strike"]),
            option_type=row["option_type"],
            qty=float(row["qty"]),
            side=row["side"],
            iv_frozen=iv if iv is not None and iv > 0 else 0.01,
            iv_as_of=opened,
            entry_premium=cost if cost is not None and cost > 0 else None,
        ),
        spot=_float(row["spot"]),
        theo=_float(row["model"]),
        delta=_float(row["delta"]),
        dte=_float(row["dte"]),
        notional=_float(row["notional"]),
        cost=cost if cost is not None and cost > 0 else None,
        upnl=_float(row["upnl"]),
        valuation_mode=mode,
        model_iv=_float(row["model_iv"]),
        iv_stale=bool(row["iv_stale"]),
        iv_fallback=bool(row["iv_fallback"]),
        surface_session_date=date.fromisoformat(str(session)) if session else None,
        close_spot=_float(row["close_spot"]),
        close_premium=_float(row["close_premium"]),
        last_op_at=_parse_dt(row["last_op_at"]),
        spot_source=row["spot_source"],
        error=row["error"],
    )


def _parse_dt(value: object) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def _float(value: object) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def migrate_journal_quotes(account: str, home: Path | None = None) -> int:
    """Copy historical quote snapshots into sqlite, then leave the ledger in place.

    Returns how many quote rows were inserted. A second call does nothing.
    The original snapshot bytes are renamed to ``logs/<account>.archive.jsonl``.
    """
    from optionda.journal import archive_quote_snapshots, is_quote_snapshot, log_path

    root = ensure_home(home)
    if _migrated(account, root):
        return 0
    lock = _acquire_lock(account, root)
    if lock is None:
        return 0
    try:
        if _migrated(account, root):
            return 0
        source = log_path(account, root)
        inserted = 0
        while True:
            added, offset = _import_snapshots(account, root, source, is_quote_snapshot)
            inserted += added
            if not source.exists() or offset >= source.stat().st_size:
                break
        archive_quote_snapshots(account, root)
        _set_meta(account, root, "snapshots_archived", "1")
        return inserted
    finally:
        _release_lock(lock)


def _migrated(account: str, home: Path) -> bool:
    path = home / "quotes" / f"{account}.sqlite"
    if not path.exists():
        return False
    conn = _connect(path)
    try:
        _ensure_schema(conn)
        row = conn.execute(
            "SELECT value FROM meta WHERE key = 'snapshots_archived'"
        ).fetchone()
    finally:
        conn.close()
    return row is not None and row["value"] == "1"


def _set_meta(account: str, home: Path, key: str, value: str) -> None:
    conn = _connect(quote_db_path(account, home))
    try:
        _ensure_schema(conn)
        conn.execute(
            "INSERT INTO meta(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
        conn.commit()
    finally:
        conn.close()


def _import_snapshots(account: str, home: Path, path: Path, is_snapshot) -> tuple[int, int]:
    if not path.exists():
        return 0, 0
    conn = _connect(quote_db_path(account, home))
    inserted = 0
    offset = 0
    try:
        _ensure_schema(conn)
        conn.execute("PRAGMA synchronous=OFF")
        row = conn.execute(
            "SELECT value FROM meta WHERE key = 'import_offset'"
        ).fetchone()
        if row is not None:
            try:
                offset = int(row["value"])
            except ValueError:
                offset = 0
        if offset > path.stat().st_size:
            offset = 0
        names = ", ".join(_COLUMNS)
        placeholders = ", ".join("?" for _ in _COLUMNS)
        pending: list[tuple] = []
        last: list[tuple] = []
        with path.open("rb") as handle:
            handle.seek(offset)
            while True:
                line = handle.readline()
                if not line:
                    break
                offset = handle.tell()
                if not is_snapshot(line):
                    continue
                records = _snapshot_records(line)
                if records:
                    last = records
                pending.extend(records)
                if len(pending) >= 4000:
                    inserted += _flush_snapshots(conn, names, placeholders, pending, offset)
                    pending = []
        inserted += _flush_snapshots(conn, names, placeholders, pending, offset)
        if last:
            _upsert_latest(conn, names, placeholders, last, prune=False)
            conn.commit()
        conn.execute("PRAGMA synchronous=NORMAL")
    finally:
        conn.close()
    return inserted, offset


def _flush_snapshots(
    conn: sqlite3.Connection,
    names: str,
    placeholders: str,
    pending: list[tuple],
    offset: int,
) -> int:
    if pending:
        conn.executemany(
            f"INSERT INTO quotes ({names}) VALUES ({placeholders})",
            pending,
        )
    conn.execute(
        "INSERT INTO meta(key, value) VALUES('import_offset', ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (str(offset),),
    )
    conn.commit()
    return len(pending)


def _snapshot_records(line: bytes) -> list[tuple]:
    try:
        event = json.loads(line)
    except json.JSONDecodeError:
        return []
    if not isinstance(event, dict):
        return []
    rows = event.get("rows")
    if not isinstance(rows, list):
        return []
    when = str(event.get("ts") or "")
    kind = str(event.get("event") or "run")
    records: list[tuple] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        record = _snapshot_record(when, kind, row)
        if record is not None:
            records.append(record)
    return records


def _snapshot_record(ts: str, kind: str, row: dict) -> tuple | None:
    occ = str(row.get("occ") or "").upper()
    if not occ:
        return None
    try:
        parts = parse_occ(occ)
    except OccError:
        return None
    cost = _float(row.get("cost"))
    model = _float(row.get("model"))
    pnl = None
    if model is not None and cost not in (None, 0):
        pnl = (model - cost) / cost * 100.0
    iv = _float(row.get("iv"))
    if iv is None or iv <= 0:
        iv = _float(row.get("model_iv")) or _float(row.get("surface_iv")) or 0.01
    model_iv = _float(row.get("model_iv")) or _float(row.get("surface_iv")) or iv
    side = row.get("side") if row.get("side") in {"long", "short"} else "long"
    session = row.get("surface_session_date") or None
    return (
        ts,
        str(row.get("id") or occ),
        parts.occ_symbol,
        parts.underlying,
        parts.expiry.isoformat(),
        parts.strike,
        parts.option_type,
        _float(row.get("qty")) or 0.0,
        side,
        iv,
        cost,
        _float(row.get("spot")),
        model,
        _float(row.get("upnl")),
        pnl,
        _float(row.get("notional")),
        _float(row.get("delta")),
        _float(row.get("dte")),
        _float(row.get("close_spot")),
        _float(row.get("close_premium")),
        row.get("valuation_mode") if row.get("valuation_mode") in {"surface", "frozen"} else "frozen",
        model_iv,
        1 if row.get("iv_stale") else 0,
        1 if row.get("iv_fallback") else 0,
        str(session) if session else None,
        None,
        row.get("spot_source"),
        row.get("error"),
        kind,
        _float(row.get("live")),
    )


def _acquire_lock(account: str, home: Path):
    folder = home / "quotes"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{account}.migrate.lock"
    if path.exists() and not _lock_owner_alive(path):
        path.unlink(missing_ok=True)
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        return None
    os.write(fd, str(os.getpid()).encode())
    return fd, path


def _lock_owner_alive(path: Path) -> bool:
    try:
        pid = int(path.read_text(encoding="utf-8").strip() or "0")
    except (OSError, ValueError):
        return False
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _release_lock(lock) -> None:
    fd, path = lock
    os.close(fd)
    path.unlink(missing_ok=True)
