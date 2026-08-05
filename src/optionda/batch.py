from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from rich import box
from rich.console import Console, Group
from rich.panel import Panel
from rich.progress import BarColumn, Progress, SpinnerColumn, TextColumn, TimeElapsedColumn
from rich.table import Table
from rich.text import Text

from optionda.engine import freeze_iv_for_position
from optionda.models import Position, Side
from optionda.occ import OccError, parse_position_line
from optionda.store import AccountStore, StoreError


@dataclass
class BatchRow:
    status: str  # ok | skip | fail
    label: str
    occ: str = ""
    iv: float | None = None
    source: str = ""
    detail: str = ""


@dataclass
class BatchResult:
    ok: int = 0
    failed: int = 0
    skipped: int = 0
    errors: list[str] = field(default_factory=list)
    rows: list[BatchRow] = field(default_factory=list)


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


def short_path(path: Path) -> str:
    try:
        home = Path.home()
        resolved = path.resolve()
        if resolved.is_relative_to(home):
            return "~/" + resolved.relative_to(home).as_posix()
    except (OSError, ValueError):
        pass
    return str(path)


def render_batch_summary(result: BatchResult, *, book: Path | None = None) -> Panel:
    table = Table(
        box=box.SIMPLE_HEAD,
        show_header=True,
        header_style="bold",
        pad_edge=False,
        expand=True,
        border_style="dim",
    )
    table.add_column("Status", width=6)
    table.add_column("OCC", ratio=2)
    table.add_column("IV*", justify="right", width=8)
    table.add_column("Src", width=8)
    table.add_column("Note", style="dim", ratio=1)

    for row in result.rows:
        if row.status == "ok":
            st = Text("ok", style="bold green")
            iv = f"{row.iv * 100:.1f}%" if row.iv is not None else "—"
            note = ""
        elif row.status == "skip":
            st = Text("skip", style="bold yellow")
            iv = "—"
            note = row.detail
        else:
            st = Text("fail", style="bold red")
            iv = "—"
            note = row.detail
        table.add_row(st, row.occ or row.label, iv, row.source or "—", note)

    counts = Text.assemble(
        ("ok ", "dim"),
        (str(result.ok), "bold green"),
        ("  skip ", "dim"),
        (str(result.skipped), "bold yellow" if result.skipped else "dim"),
        ("  fail ", "dim"),
        (str(result.failed), "bold red" if result.failed else "dim"),
    )
    footer_bits: list = [counts]
    if book is not None:
        footer_bits.append(Text(f"book {short_path(book)}", style="dim"))

    return Panel(
        Group(table, *footer_bits),
        title="add",
        title_align="left",
        border_style="cyan",
        box=box.SQUARE,
        padding=(0, 1),
    )


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
    out = BatchResult()
    con = console or Console()
    store.require_current()
    total = len(lines)

    with Progress(
        SpinnerColumn(style="cyan"),
        TextColumn("[cyan]{task.description}[/cyan]"),
        BarColumn(bar_width=28, complete_style="cyan", finished_style="green"),
        TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
        TextColumn("•"),
        TextColumn("{task.completed}/{task.total}"),
        TimeElapsedColumn(),
        console=con,
        transient=True,  # clear bar when done — summary panel follows
    ) as progress:
        task = progress.add_task(f"adding 0/{total}", total=total)
        for index, line in enumerate(lines, start=1):
            short = line if len(line) <= 36 else line[:33] + "…"
            progress.update(task, description=f"adding {index}/{total}  {short}")
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
                out.rows.append(
                    BatchRow(
                        status="ok",
                        label=line,
                        occ=draft.occ_symbol,
                        iv=draft.iv_frozen,
                        source=draft.iv_source or "market",
                    )
                )
            except StoreError as exc:
                msg = str(exc)
                if "already exists" in msg:
                    out.skipped += 1
                    out.rows.append(
                        BatchRow(
                            status="skip",
                            label=line,
                            occ=line.split()[0] if line else line,
                            detail="already exists",
                        )
                    )
                else:
                    out.failed += 1
                    out.errors.append(f"{line}: {msg}")
                    out.rows.append(
                        BatchRow(status="fail", label=line, occ=line, detail=msg)
                    )
            except (OccError, Exception) as exc:  # noqa: BLE001
                out.failed += 1
                out.errors.append(f"{line}: {exc}")
                out.rows.append(
                    BatchRow(status="fail", label=line, occ=line, detail=str(exc))
                )
            progress.advance(task)

    return out
