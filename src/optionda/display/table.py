from __future__ import annotations

from datetime import datetime, timezone

from rich import box
from rich.console import Group
from rich.panel import Panel
from rich.rule import Rule
from rich.table import Table
from rich.text import Text

from optionda.models import RowMark

_SPINNERS = ("⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏")


def spinner_frame(tick: int) -> str:
    return _SPINNERS[tick % len(_SPINNERS)]


def _fmt_money(value: float | None) -> str:
    if value is None:
        return "—"
    return f"{value:,.2f}"


def _fmt_iv(value: float) -> str:
    return f"{value * 100:.1f}%"


def _chg_text(current: float | None, previous: float | None) -> Text:
    if current is None or previous is None:
        return Text("—", style="dim")
    delta = current - previous
    if abs(delta) < 1e-9:
        return Text("0.00", style="dim")
    style = "bold green" if delta > 0 else "bold red"
    sign = "+" if delta > 0 else ""
    return Text(f"{sign}{delta:,.2f}", style=style)


def _money_flash(value: float | None, previous: float | None) -> Text:
    if value is None:
        return Text("—", style="dim")
    label = _fmt_money(value)
    if previous is None:
        return Text(label)
    if value > previous + 1e-9:
        return Text(label, style="bold green")
    if value < previous - 1e-9:
        return Text(label, style="bold red")
    return Text(label, style="dim")


def render_snapshot(
    *,
    account: str,
    feed: str,
    refresh_sec: int,
    rows: list[RowMark],
    prev_spots: dict[str, float] | None = None,
    prev_theos: dict[str, float] | None = None,
    prev_notionals: dict[str, float] | None = None,
    continuous: bool = False,
    spin: str | None = None,
    eta_sec: int | None = None,
) -> Group:
    prev_s = prev_spots or {}
    prev_t = prev_theos or {}
    prev_n = prev_notionals or {}
    now = datetime.now(timezone.utc).strftime("%H:%M:%S UTC")

    title = Text.assemble(
        (f"({account})", "bold cyan"),
        ("  optionda", "bold"),
        ("  ·  ", "dim"),
        ("MODEL", "bold yellow"),
    )
    subtitle = Text.assemble(
        (f"feed={feed}", "dim"),
        ("  │  ", "dim"),
        (f"refresh={refresh_sec}s", "dim"),
        ("  │  ", "dim"),
        (now, "dim"),
    )
    header = Panel(
        Group(title, subtitle),
        box=box.SQUARE,
        border_style="cyan",
        padding=(0, 1),
    )

    legend = Text(
        "Model$ = BS theoretical premium / share   ·   "
        "IV* = frozen vol   ·   "
        "Chg$ = book $ vs last refresh   ·   "
        "not a live option quote",
        style="dim",
    )

    table = Table(
        show_header=True,
        header_style="bold",
        box=box.SIMPLE_HEAD,
        border_style="dim",
        pad_edge=False,
        expand=True,
        show_lines=False,
        row_styles=("", "dim"),
    )
    table.add_column("OCC", style="bold")
    table.add_column("Side", justify="center")
    table.add_column("Qty", justify="right")
    table.add_column("Spot", justify="right")
    table.add_column("IV*", justify="right")
    table.add_column("Model$", justify="right")
    table.add_column("Chg$", justify="right")
    table.add_column("Delta", justify="right")
    table.add_column("DTE", justify="right")

    total = 0.0
    total_prev = 0.0
    iv_times: list[str] = []
    iv_sources: set[str] = set()

    if not rows:
        table.add_row("—", "—", "—", "—", "—", Text("(no positions)", style="dim"), "—", "—", "—")

    for row in rows:
        pos = row.position
        notional = row.notional
        if notional is not None:
            total += notional
        prev_notional = prev_n.get(pos.id)
        if prev_notional is not None:
            total_prev += prev_notional
        if pos.iv_as_of:
            iv_times.append(pos.iv_as_of.isoformat(timespec="minutes"))
        if getattr(pos, "iv_source", None):
            iv_sources.add(str(pos.iv_source))

        if row.error:
            table.add_row(
                pos.occ_symbol,
                pos.side,
                f"{pos.qty:g}",
                "—",
                _fmt_iv(pos.iv_frozen),
                Text(row.error, style="red"),
                "—",
                "—",
                "—",
            )
            continue

        table.add_row(
            pos.occ_symbol,
            pos.side,
            f"{pos.qty:g}",
            _money_flash(row.spot, prev_s.get(pos.id)),
            _fmt_iv(pos.iv_frozen),
            _money_flash(row.theo, prev_t.get(pos.id)),
            _chg_text(notional, prev_notional),
            f"{row.delta:.3f}" if row.delta is not None else "—",
            f"{row.dte:.1f}" if row.dte is not None else "—",
        )

    total_delta = total - total_prev if prev_n else None
    if total_delta is None:
        total_text = Text.assemble(("Σ Model$  ", "bold"), (_fmt_money(total), "bold"))
    elif abs(total_delta) < 1e-9:
        total_text = Text.assemble(
            ("Σ Model$  ", "bold"),
            (_fmt_money(total), "bold"),
            ("  (unchanged)", "dim"),
        )
    elif total_delta > 0:
        total_text = Text.assemble(
            ("Σ Model$  ", "bold"),
            (_fmt_money(total), "bold green"),
            (f"  ({total_delta:+,.2f})", "bold green"),
        )
    else:
        total_text = Text.assemble(
            ("Σ Model$  ", "bold"),
            (_fmt_money(total), "bold red"),
            (f"  ({total_delta:+,.2f})", "bold red"),
        )

    meta_parts = ["indicative / delayed · not executable"]
    if iv_times:
        meta_parts.append(f"IV frozen {min(iv_times)} … {max(iv_times)}")
    if iv_sources:
        meta_parts.append(f"IV src={','.join(sorted(iv_sources))}")
    if continuous:
        meta_parts.append("Ctrl+C quit")
    meta = Text("  ·  ".join(meta_parts), style="dim")

    footer_inner: list = [total_text, meta]
    if continuous:
        frame = spin or spinner_frame(0)
        if eta_sec is None:
            status = Text(f"{frame}  waiting for next refresh…", style="cyan")
        else:
            status = Text(f"{frame}  next refresh in {eta_sec}s", style="cyan")
        footer_inner.append(status)

    footer = Panel(
        Group(*footer_inner),
        box=box.SQUARE,
        border_style="dim",
        padding=(0, 1),
        title="summary",
        title_align="left",
    )

    return Group(
        header,
        Rule(style="dim"),
        legend,
        Rule("positions", style="dim", align="left"),
        table,
        Rule(style="dim"),
        footer,
    )
