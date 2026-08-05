from __future__ import annotations

from datetime import datetime, timezone

from rich.console import Group
from rich.table import Table
from rich.text import Text

from optionda.models import RowMark


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
    style = "green" if delta > 0 else "red"
    sign = "+" if delta > 0 else ""
    return Text(f"{sign}{delta:,.2f}", style=style)


def render_snapshot(
    *,
    account: str,
    feed: str,
    refresh_sec: int,
    rows: list[RowMark],
    prev_notionals: dict[str, float] | None = None,
    continuous: bool = False,
) -> Group:
    prev = prev_notionals or {}
    now = datetime.now(timezone.utc).strftime("%H:%M:%S UTC")
    title = Text.assemble(
        ("optionda", "bold"),
        (f" | {account} | MODEL", ""),
        (f" | feed={feed}", "dim"),
        (f" | refresh={refresh_sec}s", "dim"),
        (f" | {now}", "dim"),
    )

    table = Table(
        show_header=True,
        header_style="bold",
        box=None,
        pad_edge=False,
        expand=True,
    )
    table.add_column("OCC", style="")
    table.add_column("Side", justify="center")
    table.add_column("Qty", justify="right")
    table.add_column("Spot", justify="right")
    table.add_column("IV*", justify="right")
    table.add_column("Theo", justify="right")
    table.add_column("Chg", justify="right")
    table.add_column("Delta", justify="right")
    table.add_column("DTE", justify="right")

    total = 0.0
    total_prev = 0.0
    iv_times: list[str] = []

    for row in rows:
        pos = row.position
        sign = 1.0 if pos.side == "long" else -1.0
        notional = row.notional if row.notional is not None else None
        if notional is not None:
            total += notional
        prev_n = prev.get(pos.id)
        if prev_n is not None:
            total_prev += prev_n

        if pos.iv_as_of:
            iv_times.append(pos.iv_as_of.isoformat(timespec="minutes"))

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
            _fmt_money(row.spot),
            _fmt_iv(pos.iv_frozen),
            _fmt_money(row.theo),
            _chg_text(notional, prev_n),
            f"{row.delta:.3f}" if row.delta is not None else "—",
            f"{row.dte:.1f}" if row.dte is not None else "—",
        )
        # silence unused
        _ = sign

    footer_bits = [
        f"Σ theo {_fmt_money(total)}",
    ]
    if prev:
        footer_bits.append(f"Δ {_fmt_money(total - total_prev)}")
    if iv_times:
        footer_bits.append(f"IV frozen {min(iv_times)} … {max(iv_times)}")
    footer_bits.append("delayed/indicative | not a quote")
    if continuous:
        footer_bits.append("Ctrl+C to quit")

    footer = Text(" | ".join(footer_bits), style="dim")
    return Group(title, table, footer)
