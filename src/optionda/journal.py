from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from optionda.models import Account, RowMark
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
    return books_dir(home) / f"{account}.txt"


def log_path(account: str, home: Path | None = None) -> Path:
    return logs_dir(home) / f"{account}.log"


def _human_line(pos) -> str:
    cp = "C" if pos.option_type == "call" else "P"
    yymmdd = (
        f"{pos.expiry.year % 100:02d}{pos.expiry.month:02d}{pos.expiry.day:02d}"
    )
    strike = f"{pos.strike:g}"
    return (
        f"{pos.underlying} {yymmdd} {strike} {cp}  "
        f"# qty={pos.qty:g} side={pos.side} iv={pos.iv_frozen:.4f} "
        f"occ={pos.occ_symbol}"
    )


def sync_book(account: Account, home: Path | None = None) -> Path:
    """Rewrite the human-readable book file for an account (create/update)."""
    path = book_path(account.name, home)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    lines = [
        f"# optionda book: {account.name}",
        f"# updated: {now}",
        "# format: UNDERLYING YYMMDD STRIKE C|P",
        "",
    ]
    for pos in account.positions:
        lines.append(_human_line(pos))
    if not account.positions:
        lines.append("# (no positions)")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def append_export_log(
    account: Account,
    rows: list[RowMark],
    *,
    feed: str,
    home: Path | None = None,
) -> Path:
    """Append a timestamped MODEL snapshot to the account log (never overwrite)."""
    path = log_path(account.name, home)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    total = 0.0
    body: list[str] = [
        "=" * 72,
        f"export  account={account.name}  feed={feed}  at={now}",
        "-" * 72,
        f"{'OCC':<22} {'Side':<6} {'Qty':>6} {'Spot':>10} {'IV*':>8} "
        f"{'Model$':>10} {'Delta':>8} {'DTE':>8}",
    ]
    for row in rows:
        pos = row.position
        if row.notional is not None:
            total += row.notional
        if row.error:
            body.append(
                f"{pos.occ_symbol:<22} {pos.side:<6} {pos.qty:>6g} "
                f"{'—':>10} {pos.iv_frozen * 100:>7.1f}%  ERROR {row.error}"
            )
            continue
        spot = f"{row.spot:,.2f}" if row.spot is not None else "—"
        theo = f"{row.theo:,.2f}" if row.theo is not None else "—"
        delta = f"{row.delta:.3f}" if row.delta is not None else "—"
        dte = f"{row.dte:.1f}" if row.dte is not None else "—"
        body.append(
            f"{pos.occ_symbol:<22} {pos.side:<6} {pos.qty:>6g} "
            f"{spot:>10} {pos.iv_frozen * 100:>7.1f}% {theo:>10} {delta:>8} {dte:>8}"
        )
    if not rows:
        body.append("(no positions)")
    body.append("-" * 72)
    body.append(f"Σ Model$ {total:,.2f}")
    body.append("")
    with path.open("a", encoding="utf-8") as fh:
        fh.write("\n".join(body) + "\n")
    return path
