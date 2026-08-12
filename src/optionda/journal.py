from __future__ import annotations

import json
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
    """Append-only event stream (JSONL). Never overwritten."""
    return logs_dir(home) / f"{account}.jsonl"


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
) -> Path:
    """Append one JSON object to the account event log (never overwrite)."""
    path = log_path(account, home)
    payload = {"ts": _now(), "account": account, **event}
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n")
    return path


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
    home: Path | None = None,
) -> Path:
    """Append add / merge mutation to the event log."""
    return append_event(
        account.name,
        {
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
            "book": _book_snapshot(account),
        },
        home=home,
    )


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
    home: Path | None = None,
) -> Path:
    """Append a realized close / partial-close trade."""
    return append_event(
        account.name,
        {
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
            "book": _book_snapshot(account),
        },
        home=home,
    )


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
        "iv_source": pos.iv_source,
        "valuation_mode": row.valuation_mode,
        "surface_iv": row.surface_iv,
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
    """Append a mark snapshot (export / run). Always append-only."""
    total = 0.0
    total_upnl = 0.0
    has_upnl = False
    for row in rows:
        if row.notional is not None:
            total += row.notional
        if row.upnl is not None:
            total_upnl += row.upnl
            has_upnl = True
    return append_event(
        account.name,
        {
            "event": source,  # export | run
            "feed": feed,
            "sum_model": round(total, 6),
            "sum_upnl": round(total_upnl, 6) if has_upnl else None,
            "n": len(rows),
            "rows": [_row_record(row) for row in rows],
        },
        home=home,
    )
