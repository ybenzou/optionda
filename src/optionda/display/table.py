from __future__ import annotations

from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

from rich import box
from rich.console import Group
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from optionda.models import RowMark

_SPINNERS = ("⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏")
_ASCII_SPIN = ("|", "/", "-", "\\")
_ET = ZoneInfo("America/New_York")

# Desk chrome — cool slate/cyan, avoid neon purple “AI dashboard” look.
_BORDER_IDLE = "bright_black"
_BORDER_LIVE = "cyan"
_BORDER_HOT = "green"
_BORDER_BUSY = "yellow"
_HEADER = "bold bright_white"
_META = "dim cyan"
_OCC = "bold bright_white"
_NUM = "bright_white"
_MUTED = "bright_black"


def spinner_frame(tick: int) -> str:
    return _SPINNERS[tick % len(_SPINNERS)]


def ascii_spinner(tick: int) -> str:
    return _ASCII_SPIN[tick % len(_ASCII_SPIN)]


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


def _date_label(value: date | None) -> str | None:
    if value is None:
        return None
    return f"{value.month}/{value.day}"


def _iv_asof_label(as_of: datetime | None) -> str | None:
    """US session calendar date for the IV used in Model$."""
    if as_of is None:
        return None
    if as_of.tzinfo is None:
        as_of = as_of.replace(tzinfo=timezone.utc)
    local = as_of.astimezone(_ET)
    return f"{local.month}/{local.day}"


def _model_iv_cell(
    iv: float | None,
    as_of: datetime | None,
    *,
    stale: bool,
    session: date | None = None,
    fallback: bool = False,
) -> Text:
    if iv is None:
        return Text("—", style=_MUTED)
    cell = Text(_fmt_iv(iv), style="cyan")
    label = _date_label(session) or _iv_asof_label(as_of)
    if label is None:
        return cell
    style = "bold yellow" if stale or fallback else _MUTED
    suffix = f"  {label}"
    if fallback:
        suffix += " fb"
    cell.append(suffix, style=style)
    return cell


def _session_status_line(rows: list[RowMark]) -> Text | None:
    if not rows:
        return None
    iv_dates = {
        row.surface_session_date
        for row in rows
        if row.surface_session_date is not None
    }
    close_dates = {
        row.reference_session_date
        for row in rows
        if row.reference_session_date is not None
    }
    pending = any(row.iv_stale for row in rows)
    fallback = any(row.iv_fallback for row in rows)
    parts: list[str] = []
    if pending or fallback:
        used = next(iter(sorted(iv_dates)), None)
        if used is not None:
            parts.append(f"IV pending, using {_date_label(used)}")
        elif fallback:
            parts.append("IV fallback")
        else:
            parts.append("IV pending")
    elif iv_dates:
        label = "/".join(_date_label(item) or "" for item in sorted(iv_dates))
        parts.append(f"IV {label}")
    if close_dates:
        label = "/".join(_date_label(item) or "" for item in sorted(close_dates))
        parts.append(f"close {label}")
    if not parts:
        return None
    return Text("  ·  ".join(parts), style="bold yellow" if pending or fallback else _MUTED)


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


def _money_flash(
    value: float | None,
    previous: float | None,
    *,
    phase: FlashPhase = "idle",
) -> Text:
    if value is None:
        return Text("—", style=_MUTED)
    label = _fmt_money(value)
    direction = _move_direction(value, previous)
    if previous is None:
        return Text(label, style=_NUM)
    if direction == 0:
        return Text(label, style=_MUTED)
    if phase == "idle":
        return Text(label, style=_dir_style(direction, phase="idle"))
    return Text(label, style=_dir_style(direction, phase=phase))


def _pnl_text(value: float | None) -> Text:
    if value is None:
        return Text("—", style=_MUTED)
    if abs(value) < 1e-9:
        return Text("0.00", style=_MUTED)
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
        return Text("—", style=_MUTED)
    label = f"{value:+,.2f}" if abs(value) >= 1e-9 else "0.00"
    direction = _move_direction(value, previous)
    if phase != "idle" and direction != 0:
        return Text(label, style=_dir_style(direction, phase=phase))
    return _pnl_text(value)


_STATUS_WIDTH = 36


def _fit_width(text: str, width: int = _STATUS_WIDTH) -> str:
    """Pad or ellipsize so the header clock does not jump when labels change."""
    visible = text.replace("\t", " ")
    if width <= 0:
        return ""
    if len(visible) > width:
        if width == 1:
            return "…"
        return visible[: width - 1] + "…"
    return visible.ljust(width)


def format_poll_status(
    label: str | None,
    *,
    busy: bool,
    done: int | None = None,
    total: int | None = None,
    eta_sec: int | None = None,
) -> str:
    """Fixed-width header status: ``1/2 fetch   0/1  spots`` or ``5s``."""
    if not busy:
        if label:
            return _fit_width(label)
        if eta_sec is not None:
            return _fit_width(f"{eta_sec}s")
        return _fit_width("")
    raw = (label or "updating…").strip()
    phase, _, detail = raw.partition("  ")
    if not phase:
        phase = raw
    if detail.startswith("spots"):
        detail = "spots"
    elif detail in {"done", "ready", "spots ready"}:
        detail = "ready"
    if done is not None and total is not None:
        counts = f"{done:>2}/{max(total, 1):<2}"
    else:
        counts = "     "
    body = f"{phase:<10} {counts} {detail}".rstrip()
    return _fit_width(body)


def _inline_bar(
    fraction: float,
    *,
    width: int = 12,
    busy: bool = False,
) -> Text:
    """Compact header progress bar (wait cycle or in-place fetch)."""
    frac = max(0.0, min(1.0, fraction))
    filled = int(round(width * frac))
    filled = min(width, max(0, filled))
    fill_style = "bold yellow" if busy else ("bold green" if frac >= 0.999 else "cyan")
    bar = Text()
    if filled:
        bar.append("#" * filled, style=fill_style)
    if filled < width:
        bar.append("-" * (width - filled), style="bright_black")
    return bar


def _side_cell(side: str) -> Text:
    if side == "long":
        return Text("long", style="bold cyan")
    if side == "short":
        return Text("short", style="bold yellow")
    return Text(side, style=_MUTED)


def _spot_chg_pct(spot: float | None, close_spot: float | None) -> Text | None:
    """Colored % move vs frozen close / surface anchor spot."""
    if spot is None or close_spot is None or close_spot <= 0:
        return None
    pct = (spot / close_spot - 1.0) * 100.0
    if abs(pct) < 0.05:
        return Text(" (0.0%)", style=_MUTED)
    if pct > 0:
        return Text(f" (+{pct:.1f}%)", style="bold green")
    return Text(f" ({pct:.1f}%)", style="bold red")


def _spot_cell(
    spot: float | None,
    close_spot: float | None,
    previous: float | None,
    *,
    phase: FlashPhase,
) -> Text:
    cell = Text()
    cell.append_text(_money_flash(spot, previous, phase=phase))
    chg = _spot_chg_pct(spot, close_spot)
    if chg is not None:
        cell.append_text(chg)
    return cell


def _premium_chg(theo: float | None, close_premium: float | None) -> Text | None:
    """Colored dollar move vs last-session option close."""
    if theo is None or close_premium is None:
        return None
    chg = theo - close_premium
    if abs(chg) < 0.005:
        return Text(" (0.00)", style=_MUTED)
    if chg > 0:
        return Text(f" (+{chg:.2f})", style="bold green")
    return Text(f" ({chg:.2f})", style="bold red")


def _model_cell(
    theo: float | None,
    close_premium: float | None,
    previous: float | None,
    *,
    phase: FlashPhase,
) -> Text:
    cell = Text()
    cell.append_text(_money_flash(theo, previous, phase=phase))
    chg = _premium_chg(theo, close_premium)
    if chg is not None:
        cell.append_text(chg)
    return cell


def _border_style(
    *,
    continuous: bool,
    phase: FlashPhase,
    poll_busy: bool,
) -> str:
    if poll_busy:
        return _BORDER_BUSY
    if phase == "hot":
        return _BORDER_HOT
    if continuous:
        return _BORDER_LIVE
    return _BORDER_IDLE


def _meta_line(
    *,
    feed: str,
    refresh_sec: int,
    continuous: bool,
    phase: FlashPhase,
    eta_sec: int | None,
    poll_fraction: float | None,
    poll_label: str | None,
    poll_busy: bool,
    poll_done: int | None = None,
    poll_total: int | None = None,
    bar_width: int = 12,
    header_bar: bool = True,
    spin: str | None = None,
    session: Text | None = None,
) -> Text:
    _ = bar_width
    clock_style = (
        "bold cyan" if phase == "hot" else "cyan" if phase == "warm" else _META
    )
    line = Text()
    line.append(feed, style="bold cyan")
    line.append("  ·  ", style=_MUTED)
    if continuous:
        line.append(f"{refresh_sec}s", style=_MUTED)
        if poll_busy:
            line.append("  ", style=_MUTED)
            line.append(spin or ascii_spinner(0), style="bold yellow")
        elif header_bar and poll_fraction is not None:
            line.append("  ", style=_MUTED)
            line.append_text(_inline_bar(poll_fraction, width=12, busy=False))
        status = format_poll_status(
            poll_label,
            busy=poll_busy,
            done=poll_done,
            total=poll_total,
            eta_sec=eta_sec,
        )
        line.append("  ", style=_MUTED)
        line.append(status, style="bold yellow" if poll_busy else _MUTED)
    else:
        line.append(f"refresh {refresh_sec}s", style=_MUTED)
    if session is not None and session.plain.strip():
        line.append("  ·  ", style=_MUTED)
        line.append_text(session)
    line.append("  ·  ", style=_MUTED)
    line.append(format_clock(), style=clock_style)
    return line


def _footer_money(
    value: float,
    previous: float | None,
    *,
    phase: FlashPhase,
) -> Text:
    direction = _move_direction(value, previous)
    sum_phase: FlashPhase = phase if phase != "idle" else "warm"
    if previous is None or direction == 0:
        return Text(_fmt_money(value), style="bold bright_white")
    return Text(_fmt_money(value), style=_dir_style(direction, phase=sum_phase))


def _footer_upnl(
    value: float,
    previous: float | None,
    *,
    phase: FlashPhase,
) -> Text:
    label = f"{value:+,.2f}" if abs(value) >= 1e-9 else "0.00"
    if phase != "idle" and previous is not None:
        direction = _move_direction(value, previous)
        if direction != 0:
            return Text(label, style=_dir_style(direction, phase=phase))
    return _pnl_text(value)


def sort_desk_rows(rows: list[RowMark]) -> list[RowMark]:
    """Largest live position value first; unmarked / error rows stay last."""

    def key(row: RowMark) -> tuple[int, float, str]:
        if row.notional is None:
            return (1, 0.0, row.position.occ_symbol)
        return (0, -abs(row.notional), row.position.occ_symbol)

    return sorted(rows, key=key)


def render_snapshot(
    *,
    account: str,
    feed: str,
    refresh_sec: int,
    rows: list[RowMark],
    prev_spots: dict[str, float] | None = None,
    prev_theos: dict[str, float] | None = None,
    prev_notionals: dict[str, float] | None = None,
    prev_upnls: dict[str, float] | None = None,
    realized: float | None = None,
    continuous: bool = False,
    spin: str | None = None,
    eta_sec: int | None = None,
    flash_phase: FlashPhase = "idle",
    poll_fraction: float | None = None,
    poll_label: str | None = None,
    poll_busy: bool = False,
    poll_done: int | None = None,
    poll_total: int | None = None,
    min_lines: int = 0,
    bar_width: int = 12,
    header_bar: bool = True,
    notes: list[str] | None = None,
    framed: bool = True,
) -> Group:
    """Single framed desk: meta + positions + column-aligned totals."""
    rows = sort_desk_rows(rows)
    prev_s = prev_spots or {}
    prev_t = prev_theos or {}
    prev_n = prev_notionals or {}
    prev_u = prev_upnls or {}
    phase: FlashPhase = flash_phase if continuous else "idle"

    total = 0.0
    total_prev = 0.0
    total_upnl = 0.0
    has_upnl = False
    for row in rows:
        if row.notional is not None:
            total += row.notional
        prev_notional = prev_n.get(row.position.id)
        if prev_notional is not None:
            total_prev += prev_notional
        if row.upnl is not None:
            total_upnl += row.upnl
            has_upnl = True

    model_footer = _footer_money(
        total,
        total_prev if prev_n else None,
        phase=phase,
    )
    upnl_footer = (
        _footer_upnl(
            total_upnl,
            sum(prev_u.values()) if prev_u else None,
            phase=phase,
        )
        if has_upnl
        else Text("—", style=_MUTED)
    )
    realized_footer = _pnl_text(realized) if realized is not None else Text("")

    table = Table(
        show_header=True,
        header_style=_HEADER,
        box=box.SIMPLE_HEAD,
        border_style=_MUTED,
        pad_edge=False,
        expand=True,
        show_lines=False,
        show_footer=True,
        footer_style="bold",
        padding=(0, 1),
        row_styles=("none", "on grey11"),
    )
    table.add_column(
        "OCC",
        style=_OCC,
        footer=Text("Σ", style="bold cyan"),
        no_wrap=True,
        overflow="fold",
    )
    cost_footer: Text | str = ""
    if realized is not None:
        cost_footer = Text.assemble(("rPnL ", "dim cyan"))
        cost_footer.append_text(realized_footer)
    table.add_column("Side", justify="center", footer="", width=5)
    table.add_column("Qty", justify="right", style=_NUM, footer="", min_width=3)
    table.add_column("Spot", justify="right", footer="", min_width=14)
    table.add_column(
        "Model IV", justify="right", style="cyan", footer="", min_width=12
    )
    table.add_column("Cost", justify="right", style=_NUM, footer=cost_footer, min_width=6)
    table.add_column("Model$", justify="right", footer=model_footer, min_width=14)
    table.add_column("uPnL$", justify="right", footer=upnl_footer, min_width=9)
    table.add_column("Delta", justify="right", style=_MUTED, footer="", min_width=5)
    table.add_column("DTE", justify="right", style=_MUTED, footer="", min_width=4)

    if not rows:
        table.add_row(
            Text("(no positions)", style=_MUTED),
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
        )

    for row in rows:
        pos = row.position
        model_iv = (
            row.model_iv
            if row.model_iv is not None
            else row.surface_iv if row.surface_iv is not None else pos.iv_frozen
        )
        iv_as_of = row.surface_as_of if row.surface_as_of is not None else pos.iv_as_of
        iv_stale = row.iv_stale or row.iv_fallback or row.valuation_mode != "surface"
        if row.error:
            table.add_row(
                Text(pos.occ_symbol, style=_OCC),
                _side_cell(pos.side),
                f"{pos.qty:g}",
                "—",
                _model_iv_cell(
                    model_iv,
                    iv_as_of,
                    stale=iv_stale,
                    session=row.surface_session_date,
                    fallback=row.iv_fallback,
                ),
                _fmt_money(pos.entry_premium),
                Text(row.error, style="red"),
                "—",
                "—",
                "—",
            )
            continue

        table.add_row(
            Text(pos.occ_symbol, style=_OCC),
            _side_cell(pos.side),
            Text(f"{pos.qty:g}", style=_NUM),
            _spot_cell(
                row.spot,
                row.close_spot,
                prev_s.get(pos.id),
                phase=phase,
            ),
            _model_iv_cell(
                model_iv,
                iv_as_of,
                stale=iv_stale,
                session=row.surface_session_date,
                fallback=row.iv_fallback,
            ),
            Text(
                _fmt_money(row.cost if row.cost is not None else pos.entry_premium),
                style=_NUM,
            ),
            _model_cell(
                row.theo,
                row.close_premium,
                prev_t.get(pos.id),
                phase=phase,
            ),
            _pnl_flash(row.upnl, prev_u.get(pos.id), phase=phase),
            Text(
                f"{row.delta:.3f}" if row.delta is not None else "—",
                style=_MUTED,
            ),
            Text(
                f"{row.dte:.1f}" if row.dte is not None else "—",
                style=_MUTED,
            ),
        )

    border = _border_style(continuous=continuous, phase=phase, poll_busy=poll_busy)
    title = Text.assemble(
        (f"[{account}]", "bold cyan"),
        ("  optionda", "bold bright_white"),
    )
    status = _session_status_line(rows)
    header = [_meta_line(
        feed=feed,
        refresh_sec=refresh_sec,
        continuous=continuous,
        phase=phase,
        eta_sec=eta_sec,
        poll_fraction=poll_fraction,
        poll_label=poll_label,
        poll_busy=poll_busy,
        poll_done=poll_done,
        poll_total=poll_total,
        bar_width=bar_width,
        header_bar=header_bar,
        spin=spin,
        session=status,
    )]
    for note in notes or []:
        header.append(Text(note, style=_MUTED))
    header.append(table)
    if not framed:
        return Group(title, *header)
    used = 5 + max(len(rows), 1) + len(notes or [])
    pad = max(0, min_lines - used)
    if pad:
        header.append(Text("\n".join([" "] * pad)))
    desk = Panel(
        Group(*header),
        title=title,
        title_align="left",
        border_style=border,
        box=box.ROUNDED,
        padding=(0, 1),
        expand=True,
    )
    return Group(desk)
