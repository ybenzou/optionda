from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from rich import box
from rich.console import Group
from rich.panel import Panel
from rich.rule import Rule
from rich.table import Table
from rich.text import Text

from optionda.models import RowMark

_SPINNERS = ("⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏")
_ET = ZoneInfo("America/New_York")


def spinner_frame(tick: int) -> str:
    return _SPINNERS[tick % len(_SPINNERS)]


def _tz_abbr(dt: datetime) -> str:
    """Compact zone label; avoid verbose Windows locale names like 中国标准时间."""
    name = (dt.tzname() or "").strip()
    if name in {
        "HKT",
        "JST",
        "KST",
        "IST",
        "BST",
        "GMT",
        "UTC",
        "EST",
        "EDT",
        "CST",
        "CDT",
        "MST",
        "MDT",
        "PST",
        "PDT",
        "CET",
        "CEST",
        "AEST",
        "AEDT",
    }:
        return name
    offset = dt.utcoffset()
    if offset is None:
        return "local"
    total_min = int(offset.total_seconds() // 60)
    sign = "+" if total_min >= 0 else "-"
    hours, mins = divmod(abs(total_min), 60)
    if mins == 0:
        return f"UTC{sign}{hours}"
    return f"UTC{sign}{hours:02d}:{mins:02d}"


def format_clock(now: datetime | None = None) -> str:
    """Local wall clock + US Eastern (US equity/options session)."""
    instant = now or datetime.now(timezone.utc)
    if instant.tzinfo is None:
        instant = instant.replace(tzinfo=timezone.utc)
    local = instant.astimezone()
    et = instant.astimezone(_ET)
    return (
        f"{local.strftime('%H:%M:%S')} {_tz_abbr(local)}"
        f"  ·  {et.strftime('%H:%M:%S')} ET"
    )


def _fmt_money(value: float | None) -> str:
    if value is None:
        return "—"
    return f"{value:,.2f}"


def _fmt_iv(value: float) -> str:
    return f"{value * 100:.1f}%"


FlashPhase = str  # "hot" | "warm" | "idle"


def _dir_style(direction: int, *, phase: FlashPhase) -> str:
    """Style for a move: up=+1, down=-1. Drop always red even if PnL still positive."""
    if direction > 0:
        if phase == "hot":
            return "bold white on dark_green"
        if phase == "warm":
            return "bold green"
        return "green"
    if direction < 0:
        if phase == "hot":
            return "bold white on dark_red"
        if phase == "warm":
            return "bold red"
        return "red"
    return "dim"


def _move_direction(current: float | None, previous: float | None) -> int:
    if current is None or previous is None:
        return 0
    if current > previous + 1e-9:
        return 1
    if current < previous - 1e-9:
        return -1
    return 0


def _chg_text(
    current: float | None,
    previous: float | None,
    *,
    phase: FlashPhase = "idle",
) -> Text:
    if current is None or previous is None:
        return Text("—", style="dim")
    delta = current - previous
    if abs(delta) < 1e-9:
        return Text("0.00", style="dim")
    direction = 1 if delta > 0 else -1
    sign = "+" if delta > 0 else ""
    style = _dir_style(direction, phase=phase if phase != "idle" else "warm")
    return Text(f"{sign}{delta:,.2f}", style=style)


def _money_flash(
    value: float | None,
    previous: float | None,
    *,
    phase: FlashPhase = "idle",
) -> Text:
    if value is None:
        return Text("—", style="dim")
    label = _fmt_money(value)
    direction = _move_direction(value, previous)
    if previous is None:
        return Text(label)
    if direction == 0:
        return Text(label, style="dim")
    if phase == "idle":
        # Keep a soft tint after settle so the last move stays readable.
        return Text(label, style=_dir_style(direction, phase="idle"))
    return Text(label, style=_dir_style(direction, phase=phase))


def _pnl_text(value: float | None) -> Text:
    if value is None:
        return Text("—", style="dim")
    if abs(value) < 1e-9:
        return Text("0.00", style="dim")
    style = "bold green" if value > 0 else "bold red"
    sign = "+" if value > 0 else ""
    return Text(f"{sign}{value:,.2f}", style=style)


def _pnl_flash(
    value: float | None,
    previous: float | None,
    *,
    phase: FlashPhase = "idle",
) -> Text:
    """Color by move vs prior tick when flashing; otherwise by absolute PnL sign."""
    if value is None:
        return Text("—", style="dim")
    label = f"{value:+,.2f}" if abs(value) >= 1e-9 else "0.00"
    direction = _move_direction(value, previous)
    if phase != "idle" and direction != 0:
        return Text(label, style=_dir_style(direction, phase=phase))
    return _pnl_text(value)


def _iv_source_label(row: RowMark) -> str:
    if row.valuation_mode == "surface":
        return "surface"
    return "frozen"


def _inline_bar(
    fraction: float,
    *,
    width: int = 14,
    busy: bool = False,
) -> Text:
    """Compact header progress bar (wait cycle or in-place fetch)."""
    frac = max(0.0, min(1.0, fraction))
    filled = int(round(width * frac))
    filled = min(width, max(0, filled))
    bar = "█" * filled + "░" * (width - filled)
    if busy:
        return Text(bar, style="bold yellow")
    if frac >= 0.999:
        return Text(bar, style="bold green")
    return Text(bar, style="cyan")


def render_snapshot(
    *,
    account: str,
    feed: str,
    refresh_sec: int,
    rows: list[RowMark],
    prev_spots: dict[str, float] | None = None,
    prev_theos: dict[str, float] | None = None,
    prev_notionals: dict[str, float] | None = None,
    prev_lives: dict[str, float] | None = None,
    prev_upnls: dict[str, float] | None = None,
    continuous: bool = False,
    spin: str | None = None,
    eta_sec: int | None = None,
    flash_phase: FlashPhase = "idle",
    poll_fraction: float | None = None,
    poll_label: str | None = None,
    poll_busy: bool = False,
) -> Group:
    prev_s = prev_spots or {}
    prev_t = prev_theos or {}
    prev_n = prev_notionals or {}
    prev_l = prev_lives or {}
    prev_u = prev_upnls or {}
    phase: FlashPhase = flash_phase if continuous else "idle"

    title = Text.assemble(
        (f"[{account}]", "bold cyan"),
        ("  optionda", "bold"),
        ("  ·  ", "dim"),
        ("MODEL", "bold yellow"),
    )
    clock_style = "bold cyan" if phase == "hot" else "cyan" if phase == "warm" else "dim"

    subtitle = Text(f"feed={feed}", style="dim")
    subtitle.append("  │  ", style="dim")
    if continuous:
        frac = 0.0 if poll_fraction is None else poll_fraction
        if poll_busy:
            subtitle.append("refresh ", style="dim")
            subtitle.append_text(_inline_bar(frac, busy=True))
            subtitle.append(
                f"  {poll_label or 'updating…'}",
                style="bold yellow",
            )
        else:
            subtitle.append(f"refresh={refresh_sec}s ", style="dim")
            subtitle.append_text(_inline_bar(frac, busy=False))
            if poll_label:
                subtitle.append(f"  {poll_label}", style="dim")
            elif eta_sec is not None:
                subtitle.append(f"  {eta_sec}s", style="dim")
    else:
        subtitle.append(f"refresh={refresh_sec}s", style="dim")
    subtitle.append("  │  ", style="dim")
    subtitle.append(format_clock(), style=clock_style)

    if phase == "hot":
        header_border = "green"
    elif poll_busy:
        header_border = "yellow"
    else:
        header_border = "cyan"
    header = Panel(
        Group(title, subtitle),
        box=box.SQUARE,
        border_style=header_border,
        padding=(0, 1),
    )

    legend = Text(
        "Live$ = option mid/last   ·   "
        "Model$ = American model (spot, IV dynamics)   ·   "
        "Cost = avg entry   ·   "
        "uPnL$ vs Model (not Live)",
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
    table.add_column("IVsrc", justify="center", min_width=5, no_wrap=True)
    table.add_column("Cost", justify="right")
    table.add_column("Live$", justify="right")
    table.add_column("Model$", justify="right")
    table.add_column("uPnL$", justify="right")
    table.add_column("Chg$", justify="right")
    table.add_column("Delta", justify="right")
    table.add_column("DTE", justify="right")

    total = 0.0
    total_prev = 0.0
    total_upnl = 0.0
    has_upnl = False
    iv_times: list[str] = []
    iv_sources: set[str] = set()

    empty = ("—",) * 13
    if not rows:
        table.add_row(*empty[:5], Text("(no positions)", style="dim"), *empty[6:])

    for row in rows:
        pos = row.position
        notional = row.notional
        if notional is not None:
            total += notional
        prev_notional = prev_n.get(pos.id)
        if prev_notional is not None:
            total_prev += prev_notional
        if row.upnl is not None:
            total_upnl += row.upnl
            has_upnl = True
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
                _iv_source_label(row),
                _fmt_money(pos.entry_premium),
                _fmt_money(row.live),
                Text(row.error, style="red"),
                "—",
                "—",
                "—",
                "—",
            )
            continue

        table.add_row(
            pos.occ_symbol,
            pos.side,
            f"{pos.qty:g}",
            _money_flash(row.spot, prev_s.get(pos.id), phase=phase),
            _fmt_iv(pos.iv_frozen),
            _iv_source_label(row),
            _fmt_money(row.cost if row.cost is not None else pos.entry_premium),
            _money_flash(row.live, prev_l.get(pos.id), phase=phase),
            _money_flash(row.theo, prev_t.get(pos.id), phase=phase),
            _pnl_flash(row.upnl, prev_u.get(pos.id), phase=phase),
            _chg_text(notional, prev_notional, phase=phase),
            f"{row.delta:.3f}" if row.delta is not None else "—",
            f"{row.dte:.1f}" if row.dte is not None else "—",
        )

    total_delta = total - total_prev if prev_n else None
    sum_phase: FlashPhase = phase if phase != "idle" else "warm"
    if total_delta is None:
        model_part = Text.assemble(("Σ Model$  ", "bold"), (_fmt_money(total), "bold"))
    elif abs(total_delta) < 1e-9:
        model_part = Text.assemble(
            ("Σ Model$  ", "bold"),
            (_fmt_money(total), "bold"),
            ("  (unchanged)", "dim"),
        )
    elif total_delta > 0:
        model_part = Text.assemble(
            ("Σ Model$  ", "bold"),
            (_fmt_money(total), _dir_style(1, phase=sum_phase)),
            (f"  ({total_delta:+,.2f})", _dir_style(1, phase=sum_phase)),
        )
    else:
        model_part = Text.assemble(
            ("Σ Model$  ", "bold"),
            (_fmt_money(total), _dir_style(-1, phase=sum_phase)),
            (f"  ({total_delta:+,.2f})", _dir_style(-1, phase=sum_phase)),
        )

    if has_upnl:
        # Absolute PnL color when idle; tick-direction when flashing.
        prev_total_upnl = sum(prev_u.values()) if prev_u else None
        if phase != "idle" and prev_total_upnl is not None:
            upnl_style = _dir_style(
                _move_direction(total_upnl, prev_total_upnl), phase=phase
            )
            if upnl_style == "dim":
                upnl_style = (
                    "bold green"
                    if total_upnl > 1e-9
                    else "bold red"
                    if total_upnl < -1e-9
                    else "bold"
                )
        else:
            upnl_style = (
                "bold green"
                if total_upnl > 1e-9
                else "bold red"
                if total_upnl < -1e-9
                else "bold"
            )
        upnl_label = f"{total_upnl:+,.2f}" if abs(total_upnl) >= 1e-9 else "0.00"
        total_text = Text.assemble(
            model_part,
            ("    Σ uPnL$  ", "bold"),
            (upnl_label, upnl_style),
        )
    else:
        total_text = model_part

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
        if phase == "hot":
            status = Text(f"{frame}  ● refreshed", style="bold green")
        elif phase == "warm":
            status = Text(f"{frame}  ● refreshed", style="cyan")
        elif poll_busy:
            status = Text(
                f"{frame}  updating marks…  {poll_label or ''}".rstrip(),
                style="yellow",
            )
        else:
            status = Text(f"{frame}  live", style="cyan")
        footer_inner.append(status)

    footer = Panel(
        Group(*footer_inner),
        box=box.SQUARE,
        border_style="dim",
        padding=(0, 1),
        title="summary",
        title_align="left",
    )

    parts: list = [
        header,
        Rule(style="dim"),
        legend,
        Rule("positions", style="dim", align="left"),
        table,
        Rule(style="dim"),
        footer,
    ]
    return Group(*parts)
