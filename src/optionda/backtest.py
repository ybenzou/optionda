"""Offline diagnostics for frozen-surface marks recorded in the JSONL journal."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


@dataclass(frozen=True)
class BacktestResult:
    count: int
    mae: float | None
    mean_relative_error: float | None
    interval_coverage: float | None


def evaluate_rows(rows: Iterable[dict[str, Any]]) -> BacktestResult:
    """Compare recorded model prices against a later supplied/recorded mark."""
    errors: list[float] = []
    relatives: list[float] = []
    covered: list[bool] = []
    for row in rows:
        live = _number(row.get("live"))
        model = _number(row.get("model"))
        if live is None or model is None:
            continue
        errors.append(abs(model - live))
        if abs(live) > 1e-8:
            relatives.append(abs(model - live) / abs(live))
        low, high = _number(row.get("model_low")), _number(row.get("model_high"))
        if low is not None and high is not None:
            covered.append(low <= live <= high)
    count = len(errors)
    return BacktestResult(
        count=count,
        mae=sum(errors) / count if count else None,
        mean_relative_error=(sum(relatives) / len(relatives) if relatives else None),
        interval_coverage=(sum(covered) / len(covered) if covered else None),
    )


def recommended_sticky_delta_weight(rows: Iterable[dict[str, Any]]) -> float:
    """Least-squares hybrid weight clamped to [0, 1]."""
    numer = 0.0
    denom = 0.0
    for row in rows:
        target = _number(row.get("live"))
        strike = _number(row.get("sticky_strike_model"))
        delta = _number(row.get("sticky_delta_model"))
        if target is None or strike is None or delta is None:
            continue
        span = delta - strike
        numer += span * (target - strike)
        denom += span * span
    if denom <= 1e-12:
        return 0.5
    return min(max(numer / denom, 0.0), 1.0)


def journal_rows(path: Path) -> list[dict[str, Any]]:
    """Load marked rows from an append-only optionda journal."""
    found = _snapshot_rows(path)
    if found:
        return found
    archives = sorted(path.parent.glob(f"{path.stem}.archive*.jsonl"))
    for archive in archives:
        found = _snapshot_rows(archive)
        if found:
            return found
    return _rows_from_quotes(path)


def _snapshot_rows(path: Path) -> list[dict[str, Any]]:
    verify_rows: list[dict[str, Any]] = []
    mark_rows: list[dict[str, Any]] = []
    if not path.exists():
        return verify_rows
    with path.open("rb") as handle:
        for raw in handle:
            if not raw.strip():
                continue
            try:
                event = json.loads(raw)
            except json.JSONDecodeError:
                continue
            kind = event.get("event")
            if kind not in {"verify", "export", "run", "snapshot", "mail"}:
                continue
            bucket = verify_rows if kind == "verify" else mark_rows
            for row in event.get("rows", []):
                if isinstance(row, dict):
                    bucket.append(row)
    return verify_rows or mark_rows


def _rows_from_quotes(path: Path) -> list[dict[str, Any]]:
    if path.parent.name != "logs":
        return []
    db = path.parent.parent / "quotes" / f"{path.stem}.sqlite"
    if not db.exists():
        return []
    import sqlite3

    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    try:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(quotes)")}
        if "source" not in columns:
            return []
        verify = conn.execute(
            "SELECT * FROM quotes WHERE source = 'verify' ORDER BY rowid"
        ).fetchall()
        chosen = verify or conn.execute(
            "SELECT * FROM quotes WHERE source IN ('export', 'run', 'mail', 'snapshot') ORDER BY rowid"
        ).fetchall()
    finally:
        conn.close()
    rows: list[dict[str, Any]] = []
    for row in chosen:
        rows.append({key: row[key] for key in row.keys()})
    return rows


def _number(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None
