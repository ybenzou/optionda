from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from rich.console import Console
from rich.progress import BarColumn, Progress, SpinnerColumn, TextColumn, TimeElapsedColumn

from optionda.engine import freeze_iv_for_position
from optionda.models import Position, Side
from optionda.occ import OccError, parse_position_line
from optionda.store import AccountStore, StoreError


@dataclass
class BatchResult:
    ok: int = 0
    failed: int = 0
    skipped: int = 0
    errors: list[str] | None = None


def read_batch_lines(source: str | Path) -> list[str]:
    if source == "-":
        import sys

        text = sys.stdin.read()
    else:
        text = Path(source).read_text(encoding="utf-8")
    lines: list[str] = []
    for line in text.splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        lines.append(s)
    return lines


def add_batch(
    store: AccountStore,
    lines: list[str],
    *,
    qty: float = 1.0,
    side: Side = "long",
    iv: float | None = None,
    home: Path | None = None,
    console: Console | None = None,
) -> BatchResult:
    out = BatchResult(errors=[])
    con = console or Console()
    # Gate once
    store.require_current()

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
        TextColumn("•"),
        TextColumn("{task.completed}/{task.total}"),
        TimeElapsedColumn(),
        console=con,
    ) as progress:
        task = progress.add_task("adding positions", total=len(lines))
        for line in lines:
            progress.update(task, description=f"add {line[:40]}")
            try:
                parts = parse_position_line(line)
                draft = Position(
                    occ_symbol=parts.occ_symbol,
                    underlying=parts.underlying,
                    expiry=parts.expiry,
                    strike=parts.strike,
                    option_type=parts.option_type,
                    qty=qty,
                    side=side,
                    iv_frozen=iv if iv is not None else 0.01,
                    iv_as_of=datetime.now(timezone.utc),
                )
                draft = freeze_iv_for_position(draft, iv=iv, home=home)
                store.add_position(None, draft)
                out.ok += 1
                progress.console.print(
                    f"  [green]ok[/green] {draft.occ_symbol} "
                    f"IV*={draft.iv_frozen * 100:.1f}% ({draft.iv_source or 'market'})"
                )
            except StoreError as exc:
                msg = str(exc)
                if "already exists" in msg:
                    out.skipped += 1
                    progress.console.print(f"  [yellow]skip[/yellow] {line} ({msg})")
                else:
                    out.failed += 1
                    out.errors.append(f"{line}: {msg}")
                    progress.console.print(f"  [red]fail[/red] {line}: {msg}")
            except (OccError, Exception) as exc:  # noqa: BLE001
                out.failed += 1
                out.errors.append(f"{line}: {exc}")
                progress.console.print(f"  [red]fail[/red] {line}: {exc}")
            progress.advance(task)

    return out
