from __future__ import annotations

import json
import os
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from optionda.models import Account, Position, RowMark
from optionda.paths import ensure_home


def books_dir(home: Path | None = None) -> Path:
    root = ensure_home(home)
    path = root / "books"
    path.mkdir(parents=True, exist_ok=True)
    return path


def logs_dir(home: Path | None = None) -> Path:
    root = ensure_home(home)
    path = root / "logs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def book_path(account: str, home: Path | None = None) -> Path:
    """Current-state human book (rewritten). Not the event log."""
    return books_dir(home) / f"{account}.txt"


def log_path(account: str, home: Path | None = None) -> Path:
    """Legacy event-log path. The ledger itself lives in SQLite."""
    return logs_dir(home) / f"{account}.jsonl"


def last_ledger_event(account: str, home: Path | None = None) -> dict | None:
    root = ensure_home(home)
    path = root / "ledger" / f"{account}.sqlite"
    if not path.exists():
        return None
    conn = sqlite3.connect(path)
    try:
        row = conn.execute(
            "SELECT payload FROM events ORDER BY seq DESC LIMIT 1"
        ).fetchone()
    except sqlite3.OperationalError:
        return None
    finally:
        conn.close()
    if row is None or row[0] is None:
        return None
    try:
        event = json.loads(row[0])
    except json.JSONDecodeError:
        return None
    return event if isinstance(event, dict) else None


def ledger_seq(account: str, home: Path | None = None) -> int:
    """Highest event seq, or 0 when the book has no sqlite ledger yet."""
    root = ensure_home(home)
    path = root / "ledger" / f"{account}.sqlite"
    if not path.exists():
        return 0
    conn = sqlite3.connect(path)
    try:
        row = conn.execute("SELECT MAX(seq) FROM events").fetchone()
    except sqlite3.OperationalError:
        return 0
    finally:
        conn.close()
    if row is None or row[0] is None:
        return 0
    return int(row[0])


def ledger_db_path(account: str, home: Path | None = None) -> Path:
    folder = ensure_home(home) / "ledger"
    folder.mkdir(parents=True, exist_ok=True)
    return folder / f"{account}.sqlite"


# Quote polls. The trade ledger is everything else.
_QUOTE_EVENTS = frozenset({"run", "export", "verify", "snapshot", "mail"})
_EVENT_RE = re.compile(br'"event"\s*:\s*"([A-Za-z0-9_]+)"')


def event_kind(line: bytes) -> str:
    found = _EVENT_RE.search(line[:240])
    if found is None:
        return ""
    return found.group(1).decode("ascii", errors="ignore")


def is_quote_snapshot(line: bytes) -> bool:
    return event_kind(line) in _QUOTE_EVENTS


def _ledger_location(path: Path) -> tuple[str, Path] | None:
    if path.parent.name != "logs" or path.suffix != ".jsonl":
        return None
    return path.stem, path.parent.parent


def _ledger_connect(account: str, home: Path | None) -> sqlite3.Connection:
    conn = sqlite3.connect(ledger_db_path(account, home))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def _ensure_ledger(conn: sqlite3.Connection) -> None:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS events (
            seq INTEGER PRIMARY KEY,
            kind TEXT,
            payload TEXT NOT NULL
        )
        """
    )


def _read_ledger_db(account: str, home: Path) -> list[dict[str, Any]] | None:
    path = home / "ledger" / f"{account}.sqlite"
    if not path.exists():
        return None
    conn = _ledger_connect(account, home)
    try:
        _ensure_ledger(conn)
        rows = conn.execute("SELECT payload FROM events ORDER BY seq").fetchall()
    finally:
        conn.close()
    events: list[dict[str, Any]] = []
    for row in rows:
        try:
            event = json.loads(row["payload"])
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict):
            events.append(event)
    return events


def _read_log_file(path: Path, *, ledger_only: bool) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    events: list[dict[str, Any]] = []
    with path.open("rb") as handle:
        for line in handle:
            if not line.strip():
                continue
            if ledger_only and is_quote_snapshot(line):
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(event, dict):
                events.append(event)
    return events


def _ledger_migrated(account: str, home: Path) -> bool:
    path = home / "ledger" / f"{account}.sqlite"
    if not path.exists():
        return False
    conn = _ledger_connect(account, home)
    try:
        _ensure_ledger(conn)
        row = conn.execute(
            "SELECT value FROM meta WHERE key = 'migrated'"
        ).fetchone()
    finally:
        conn.close()
    return row is not None and row["value"] == "1"


def _set_ledger_meta(account: str, home: Path, key: str, value: str) -> None:
    conn = _ledger_connect(account, home)
    try:
        _ensure_ledger(conn)
        conn.execute(
            "INSERT INTO meta(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
        conn.commit()
    finally:
        conn.close()


def last_quote_event(path: Path) -> dict[str, Any] | None:
    """Parse only the newest quote snapshot. Skips the trade ledger."""
    if not path.exists():
        return None
    last: dict[str, Any] | None = None
    with path.open("rb") as handle:
        for line in handle:
            if not is_quote_snapshot(line):
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(event, dict):
                last = event
    return last


def read_ledger_events(path: Path) -> list[dict[str, Any]]:
    """Trade events in write order. Quote snapshots stay out of this parse."""
    return load_log_events(path, ledger_only=True)


def load_log_events(path: Path, *, ledger_only: bool) -> list[dict[str, Any]]:
    """Ledger rows when the sqlite book exists, otherwise the JSONL file."""
    located = _ledger_location(path)
    if located is not None:
        account, home = located
        stored = _read_ledger_db(account, home)
        if stored is not None:
            return stored
    return _read_log_file(path, ledger_only=ledger_only)


def migrate_ledger(account: str, home: Path | None = None) -> int:
    """Copy the trade JSONL into sqlite, in file order, and keep the file as an archive."""
    root = ensure_home(home)
    path = log_path(account, root)
    if _ledger_migrated(account, root):
        return 0
    inserted = 0
    if path.exists():
        conn = _ledger_connect(account, root)
        try:
            _ensure_ledger(conn)
            already = conn.execute("SELECT COUNT(*) AS n FROM events").fetchone()["n"]
            if already:
                inserted = int(already)
            else:
                with path.open("rb") as handle:
                    for raw in handle:
                        if not raw.strip() or is_quote_snapshot(raw):
                            continue
                        text = raw.decode("utf-8").strip()
                        try:
                            event = json.loads(text)
                        except json.JSONDecodeError:
                            continue
                        if not isinstance(event, dict):
                            continue
                        conn.execute(
                            "INSERT INTO events(kind, payload) VALUES(?, ?)",
                            (str(event.get("event") or ""), text),
                        )
                        inserted += 1
                conn.commit()
        finally:
            conn.close()
        archive = path.with_name(f"{account}.ledger.archive.jsonl")
        if archive.exists():
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            archive = path.with_name(f"{account}.ledger.archive-{stamp}.jsonl")
        os.replace(path, archive)
    _set_ledger_meta(account, root, "migrated", "1")
    return inserted


def archive_quote_snapshots(account: str, home: Path | None = None) -> Path | None:
    """Move quote snapshots beside the ledger. The original bytes are kept."""
    src = log_path(account, home)
    folder = src.parent
    tmp = folder / f"{account}.ledger.tmp"
    if tmp.exists() and not src.exists():
        os.replace(tmp, src)
        return None
    if tmp.exists():
        tmp.unlink()
    if not src.exists():
        return None
    has_snapshot = False
    with src.open("rb") as handle:
        for line in handle:
            if is_quote_snapshot(line):
                has_snapshot = True
                break
    if not has_snapshot:
        return None
    archive = folder / f"{account}.archive.jsonl"
    if archive.exists():
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        archive = folder / f"{account}.archive-{stamp}.jsonl"
    with src.open("rb") as incoming, tmp.open("wb") as out:
        for line in incoming:
            if is_quote_snapshot(line):
                continue
            if line and not line.endswith(b"\n"):
                line += b"\n"
            out.write(line)
        out.flush()
        os.fsync(out.fileno())
    try:
        os.replace(src, archive)
    except OSError:
        tmp.unlink(missing_ok=True)
        raise
    os.replace(tmp, src)
    return archive


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _human_line(pos: Position) -> str:
    cp = "C" if pos.option_type == "call" else "P"
    yymmdd = (
        f"{pos.expiry.year % 100:02d}{pos.expiry.month:02d}{pos.expiry.day:02d}"
    )
    strike = f"{pos.strike:g}"
    cost = (
        f" @ {pos.entry_premium:.4g}"
        if pos.entry_premium is not None
        else ""
    )
    return (
        f"{pos.underlying} {yymmdd} {strike} {cp}{cost}  "
        f"# qty={pos.qty:g} side={pos.side} iv={pos.iv_frozen:.4f} "
        f"occ={pos.occ_symbol}"
    )


def sync_book(account: Account, home: Path | None = None) -> Path:
    """Rewrite the human-readable *current* book (snapshot, not history)."""
    path = book_path(account.name, home)
    now = _now()
    lines = [
        f"# optionda book: {account.name}",
        f"# updated: {now}",
        "# format: UNDERLYING YYMMDD STRIKE C|P @ COST",
        "# note: this file is the current book only; history is logs/<account>.jsonl",
        "",
    ]
    for pos in account.positions:
        lines.append(_human_line(pos))
    if not account.positions:
        lines.append("# (no positions)")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def append_event(
    account: str,
    event: dict[str, Any],
    *,
    home: Path | None = None,
    ts: datetime | None = None,
) -> Path:
    """Append one trade event. Write order is the book order."""
    when = _iso_ts(ts) if ts is not None else _now()
    payload = {"ts": when, "account": account, **event}
    text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    conn = _ledger_connect(account, home)
    try:
        _ensure_ledger(conn)
        conn.execute(
            "INSERT INTO events(kind, payload) VALUES(?, ?)",
            (str(event.get("event") or ""), text),
        )
        conn.execute(
            "INSERT INTO meta(key, value) VALUES('migrated', '1') "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value"
        )
        conn.commit()
    finally:
        conn.close()
    return ledger_db_path(account, home)


def replace_log(
    account: str,
    events: list[dict[str, Any]],
    *,
    home: Path | None = None,
) -> Path:
    """Replace one account ledger. Used by unpack, not by live commands."""
    conn = _ledger_connect(account, home)
    try:
        _ensure_ledger(conn)
        conn.execute("DELETE FROM events")
        conn.executemany(
            "INSERT INTO events(kind, payload) VALUES(?, ?)",
            [
                (
                    str(event.get("event") or ""),
                    json.dumps(event, ensure_ascii=False, separators=(",", ":")),
                )
                for event in events
            ],
        )
        conn.execute(
            "INSERT INTO meta(key, value) VALUES('migrated', '1') "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value"
        )
        conn.commit()
    finally:
        conn.close()
    return ledger_db_path(account, home)


def _iso_ts(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat(timespec="seconds")


def _position_brief(pos: Position) -> dict[str, Any]:
    return {
        "id": pos.id,
        "occ": pos.occ_symbol,
        "underlying": pos.underlying,
        "side": pos.side,
        "qty": pos.qty,
        "cost": pos.entry_premium,
        "iv": pos.iv_frozen,
        "iv_source": pos.iv_source,
        "expiry": pos.expiry.isoformat(),
        "strike": pos.strike,
        "option_type": pos.option_type,
        "opened_at": _iso_ts(pos.opened_at),
    }


def _book_snapshot(account: Account) -> list[dict[str, Any]]:
    return [_position_brief(p) for p in account.positions]


def append_add_event(
    account: Account,
    *,
    position_after: Position,
    qty_added: float,
    cost_added: float,
    merged: bool,
    previous_qty: float,
    previous_entry: float | None,
    dte_at_entry: int | None = None,
    batch_id: str | None = None,
    ts: datetime | None = None,
    home: Path | None = None,
) -> Path:
    """Append add / merge mutation to the event log."""
    payload: dict[str, Any] = {
        "event": "merge" if merged else "add",
        "id": position_after.id,
        "occ": position_after.occ_symbol,
        "side": position_after.side,
        "qty_added": qty_added,
        "cost_added": cost_added,
        "qty": position_after.qty,
        "cost": position_after.entry_premium,
        "qty_before": previous_qty if merged else 0.0,
        "cost_before": previous_entry if merged else None,
        "iv": position_after.iv_frozen,
        "iv_source": position_after.iv_source,
        "dte_at_entry": dte_at_entry,
        "book": _book_snapshot(account),
    }
    if batch_id:
        payload["batch_id"] = batch_id
    return append_event(account.name, payload, home=home, ts=ts)


def append_delete_event(
    account: Account,
    removed: list[Position],
    *,
    home: Path | None = None,
) -> Path:
    return append_event(
        account.name,
        {
            "event": "delete",
            "removed": [_position_brief(p) for p in removed],
            "book": _book_snapshot(account),
        },
        home=home,
    )


def append_sell_event(
    account: Account,
    *,
    position_id: str,
    occ_symbol: str,
    side: str,
    qty_sold: float,
    exit_premium: float,
    avg_cost: float,
    realized: float,
    qty_remaining: float,
    closed: bool,
    multiplier: int = 100,
    dte_at_exit: int | None = None,
    hold_days: float | None = None,
    batch_id: str | None = None,
    ts: datetime | None = None,
    home: Path | None = None,
) -> Path:
    """Append a realized close / partial-close trade."""
    payload: dict[str, Any] = {
        "event": "sell",
        "id": position_id,
        "occ": occ_symbol,
        "side": side,
        "qty_sold": qty_sold,
        "exit": exit_premium,
        "avg_cost": avg_cost,
        "multiplier": multiplier,
        "realized": realized,
        "qty_remaining": qty_remaining,
        "closed": closed,
        "dte_at_exit": dte_at_exit,
        "hold_days": hold_days,
        "book": _book_snapshot(account),
    }
    if batch_id:
        payload["batch_id"] = batch_id
    return append_event(account.name, payload, home=home, ts=ts)


def append_undo_event(
    account: Account,
    *,
    realized: float,
    by_occ: dict[str, float],
    reverses: list[dict[str, Any]],
    n_events: int,
    undone_batch_id: str | None,
    batch_id: str | None = None,
    home: Path | None = None,
) -> Path:
    payload: dict[str, Any] = {
        "event": "undo",
        "realized": realized,
        "by_occ": by_occ,
        "reverses": reverses,
        "n_events": n_events,
        "undone_batch_id": undone_batch_id,
        "book": _book_snapshot(account),
    }
    if batch_id:
        payload["batch_id"] = batch_id
    return append_event(account.name, payload, home=home)


def append_refresh_iv_event(
    account: Account,
    *,
    home: Path | None = None,
    surfaces: list[dict[str, Any]] | None = None,
) -> Path:
    return append_event(
        account.name,
        {
            "event": "refresh_iv",
            "book": _book_snapshot(account),
            "surfaces": surfaces or [],
        },
        home=home,
    )


def _row_record(row: RowMark) -> dict[str, Any]:
    pos = row.position
    return {
        "occ": pos.occ_symbol,
        "side": pos.side,
        "qty": pos.qty,
        "spot": row.spot,
        "iv": pos.iv_frozen,
        "model_iv": row.model_iv if row.model_iv is not None else row.surface_iv,
        "iv_source": pos.iv_source,
        "valuation_mode": row.valuation_mode,
        "surface_iv": row.surface_iv,
        "surface_session_date": (
            row.surface_session_date.isoformat()
            if row.surface_session_date is not None
            else None
        ),
        "reference_session_date": (
            row.reference_session_date.isoformat()
            if row.reference_session_date is not None
            else None
        ),
        "iv_stale": row.iv_stale,
        "iv_fallback": row.iv_fallback,
        "surface_as_of": (
            row.surface_as_of.isoformat()
            if row.surface_as_of is not None
            else None
        ),
        "surface_source": row.surface_source,
        "model_low": row.model_low,
        "model_high": row.model_high,
        "iv_dynamics": row.iv_dynamics,
        "sticky_strike_iv": row.sticky_strike_iv,
        "sticky_delta_iv": row.sticky_delta_iv,
        "sticky_strike_model": row.sticky_strike_model,
        "sticky_delta_model": row.sticky_delta_model,
        "rate_used": row.rate_used,
        "dividend_used": row.dividend_used,
        "spot_as_of": (
            row.spot_as_of.isoformat() if row.spot_as_of is not None else None
        ),
        "spot_source": row.spot_source,
        "cost": row.cost if row.cost is not None else pos.entry_premium,
        "live": row.live,
        "model": row.theo,
        "close_premium": row.close_premium,
        "theo_chg": row.theo_chg,
        "upnl": row.upnl,
        "notional": row.notional,
        "delta": row.delta,
        "dte": row.dte,
        "error": row.error,
    }


def append_export_log(
    account: Account,
    rows: list[RowMark],
    *,
    feed: str,
    home: Path | None = None,
    source: str = "export",
) -> Path:
    """Store a mark snapshot in the quote database."""
    from optionda.quotes import quote_db_path, save_quotes

    save_quotes(account.name, rows, home=home, source=source)
    return quote_db_path(account.name, home)


def append_verify_log(
    account: Account,
    rows: list[RowMark],
    *,
    feed: str,
    home: Path | None = None,
) -> Path:
    """Append an explicit model-vs-live comparison snapshot."""
    return append_export_log(account, rows, feed=feed, home=home, source="verify")
