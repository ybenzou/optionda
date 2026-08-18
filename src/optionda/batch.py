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

from optionda.engine import freeze_iv_for_position, sync_completed_session
from optionda.journal import sync_book
from optionda.models import Position, Side
from optionda.occ import OccError, as_sell_line, parse_leg_line, parse_occ, require_entry, resolve_qty
from optionda.store import AccountStore, StoreError


@dataclass
class BatchRow:
    status: str  # ok | merge | fail
    label: str
    occ: str = ""
    iv: float | None = None
    source: str = ""
    detail: str = ""


@dataclass
class BatchResult:
    ok: int = 0
    merged: int = 0
    failed: int = 0
    skipped: int = 0
    sold: int = 0
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


def merge_detail(outcome) -> str:
    pos = outcome.position
    bits = [f"qty {outcome.previous_qty:g}→{pos.qty:g}"]
    if (
        outcome.previous_entry is not None
        and pos.entry_premium is not None
    ):
        bits.append(
            f"cost {outcome.previous_entry:g}→{pos.entry_premium:g}"
        )
    elif pos.entry_premium is not None:
        bits.append(f"cost={pos.entry_premium:g}")
    return " ".join(bits)


def ok_detail(pos: Position) -> str:
    cost = (
        f" cost={pos.entry_premium:g}"
        if pos.entry_premium is not None
        else ""
    )
    return f"qty={pos.qty:g}{cost}"


def sell_detail(outcome) -> str:
    left = (
        "closed"
        if outcome.closed
        else f"left {outcome.position.qty:g}"
    )
    return (
        f"sold x{outcome.qty_sold:g} @ {outcome.exit_premium:g}  "
        f"realized ${outcome.realized:,.2f}  {left}"
    )


def sell_from_line(
    store: AccountStore,
    line: str,
    *,
    qty: float = 1.0,
) -> BatchRow:
    rest = as_sell_line(line)
    if rest is None:
        raise OccError(f"not a sell line: {line}")
    leg = parse_leg_line(rest)
    if leg.entry is None or leg.entry <= 0:
        raise OccError("exit premium required — use '@ 7.3'")
    line_qty = resolve_qty(leg.qty, qty)
    outcome = store.sell_position(
        None,
        leg.parts.occ_symbol,
        qty=line_qty,
        exit_premium=leg.entry,
    )
    return BatchRow(
        status="sell",
        label=line,
        occ=outcome.occ_symbol,
        detail=sell_detail(outcome),
    )


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
            note = row.detail
        elif row.status == "merge":
            st = Text("merge", style="bold cyan")
            iv = f"{row.iv * 100:.1f}%" if row.iv is not None else "—"
            note = row.detail
        elif row.status == "sell":
            st = Text("sell", style="bold yellow")
            iv = "—"
            note = row.detail
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
        ("  merge ", "dim"),
        (str(result.merged), "bold cyan" if result.merged else "dim"),
        ("  sell ", "dim"),
        (str(result.sold), "bold yellow" if result.sold else "dim"),
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


def _add_one_line(
    store: AccountStore,
    line: str,
    out: BatchResult,
    *,
    qty: float,
    side: Side,
    iv: float | None,
    entry: float | None,
    home: Path | None,
) -> None:
    try:
        if as_sell_line(line) is not None:
            row = sell_from_line(store, line, qty=qty)
            out.sold += 1
            out.rows.append(row)
            return
        leg = parse_leg_line(line)
        cost = require_entry(leg.entry, entry)
        line_qty = resolve_qty(leg.qty, qty)
        parts = leg.parts
        draft = Position(
            occ_symbol=parts.occ_symbol,
            underlying=parts.underlying,
            expiry=parts.expiry,
            strike=parts.strike,
            option_type=parts.option_type,
            qty=line_qty,
            side=side,
            iv_frozen=iv if iv is not None else 0.01,
            iv_as_of=datetime.now(timezone.utc),
            entry_premium=cost,
        )
        draft = freeze_iv_for_position(draft, iv=iv, home=home)
        outcome = store.add_position(None, draft)
        pos = outcome.position
        if outcome.merged:
            out.merged += 1
            out.rows.append(
                BatchRow(
                    status="merge",
                    label=line,
                    occ=pos.occ_symbol,
                    iv=pos.iv_frozen,
                    source=pos.iv_source or "market",
                    detail=merge_detail(outcome),
                )
            )
            return
        out.ok += 1
        out.rows.append(
            BatchRow(
                status="ok",
                label=line,
                occ=pos.occ_symbol,
                iv=pos.iv_frozen,
                source=pos.iv_source or "market",
                detail=ok_detail(pos),
            )
        )
    except StoreError as exc:
        msg = str(exc)
        out.failed += 1
        out.errors.append(f"{line}: {msg}")
        out.rows.append(BatchRow(status="fail", label=line, occ=line, detail=msg))
    except (OccError, Exception) as exc:  # noqa: BLE001
        out.failed += 1
        out.errors.append(f"{line}: {exc}")
        out.rows.append(BatchRow(status="fail", label=line, occ=line, detail=str(exc)))


def add_batch(
    store: AccountStore,
    lines: list[str],
    *,
    qty: float = 1.0,
    side: Side = "long",
    iv: float | None = None,
    entry: float | None = None,
    home: Path | None = None,
    console: Console | None = None,
    on_progress=None,
) -> BatchResult:
    out = BatchResult()
    store.require_current()
    total = max(len(lines), 1)

    def report(label: str, done: int, steps: int) -> None:
        if on_progress is not None:
            on_progress(label, done, steps)

    def one(index: int, line: str) -> str:
        short = line if len(line) <= 36 else line[:33] + "…"
        label = f"add {index}/{total}  {short}"
        report(label, index - 1, total)
        _add_one_line(
            store,
            line,
            out,
            qty=qty,
            side=side,
            iv=iv,
            entry=entry,
            home=home,
        )
        report(label, index, total)
        return label

    if console is not None:
        with Progress(
            SpinnerColumn(style="cyan"),
            TextColumn("[cyan]{task.description}[/cyan]"),
            BarColumn(bar_width=28, complete_style="cyan", finished_style="green"),
            TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
            TextColumn("•"),
            TextColumn("{task.completed}/{task.total}"),
            TimeElapsedColumn(),
            console=console,
            transient=True,
        ) as progress:
            task = progress.add_task(f"add 0/{total}", total=total)
            for index, line in enumerate(lines, start=1):
                label = one(index, line)
                progress.update(task, description=label, completed=index, total=total)
        return out

    for index, line in enumerate(lines, start=1):
        one(index, line)
    return out


def run_add(
    store: AccountStore,
    lines: list[str],
    *,
    qty: float = 1.0,
    side: Side = "long",
    iv: float | None = None,
    entry: float | None = None,
    home: Path | None = None,
    console: Console | None = None,
    on_progress=None,
) -> BatchResult:
    result = add_batch(
        store,
        lines,
        qty=qty,
        side=side,
        iv=iv,
        entry=entry,
        home=home,
        console=console,
        on_progress=on_progress,
    )
    acc = store.require_current()
    sync_book(acc, home)
    added = [
        parse_occ(row.occ).underlying
        for row in result.rows
        if row.status in {"ok", "merge"} and row.occ
    ]
    if added:
        sync = sync_completed_session(
            acc,
            home=home,
            only=set(added),
            on_progress=on_progress,
        )
        if console is not None:
            from optionda.desk_live import sync_notes

            for line in sync_notes(sync):
                console.print(f"[dim]{line}[/dim]")
    return result
