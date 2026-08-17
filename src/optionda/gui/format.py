"""Presentation labels for the native Stats desk. No Qt imports."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

from optionda.analytics import StatsReport


def signed_money(value: float | None, *, compact: bool = False, digits: int = 0) -> str:
    if value is None:
        return "—"
    if compact and abs(value) >= 1000:
        return f"{value / 1000:+.1f}k"
    if digits == 0 and abs(value) >= 10:
        return f"{value:+,.0f}"
    return f"{value:+,.2f}"


def money(value: float | None) -> str:
    if value is None:
        return "—"
    return f"{value:,.2f}"


def pct(value: float | None) -> str:
    if value is None:
        return "—"
    return f"{round(value * 100)}%"


def hold_label(days: float | None) -> str:
    if days is None:
        return "—"
    if days < 1:
        return f"{days * 24:.0f}h"
    if days < 10:
        return f"{days:.1f}d"
    return f"{days:.0f}d"


def month_title(day: date) -> str:
    return day.strftime("%b %Y")


def occ_short(occ: str) -> str:
    raw = (occ or "?").upper()
    if len(raw) >= 15 and raw[-9] in {"C", "P"}:
        root = raw[:-15]
        yymmdd = raw[-15:-9]
        cp = raw[-9]
        try:
            strike = int(raw[-8:]) / 1000.0
            strike_s = f"{strike:g}"
        except ValueError:
            strike_s = raw[-8:]
        return f"{root} {yymmdd[2:4]}/{yymmdd[4:6]} {strike_s}{cp}"
    return raw


def iso_day(value: datetime | date | None) -> str:
    if value is None:
        return "—"
    if isinstance(value, datetime):
        return value.date().isoformat()
    return value.isoformat()


def pnl_tone(value: float | None) -> str:
    if value is None or value == 0:
        return "neutral"
    return "pos" if value > 0 else "neg"


@dataclass(frozen=True)
class KpiCard:
    title: str
    value: str
    detail: str
    tone: str = "neutral"


def kpi_cards(report: StatsReport) -> list[KpiCard]:
    sell_note = ""
    if report.sell_win.total:
        sell_note = f" · sell-event {report.sell_win.label()}"
    if report.n_closed <= 0:
        win_value = "—"
        win_detail = "no fully closed lots yet"
        hold_value = "—"
        hold_detail = "needs a full close"
        closed_detail = f"{report.n_sells} sell event{'s' if report.n_sells != 1 else ''}{sell_note}"
    else:
        win_value = pct(report.lot_win.rate)
        win_detail = f"{report.lot_win.wins}/{report.lot_win.total} closed lots{sell_note}"
        hold_value = hold_label(report.avg_hold_days)
        hold_detail = "average closed hold"
        closed_detail = f"{report.n_sells} sell event{'s' if report.n_sells != 1 else ''}{sell_note}"
    return [
        KpiCard(
            "Total P&L",
            signed_money(report.total_pnl if report.total_pnl is not None else report.realized),
            "realized + open marks",
            pnl_tone(report.total_pnl if report.total_pnl is not None else report.realized),
        ),
        KpiCard(
            "Realized P&L",
            signed_money(report.realized),
            f"{report.n_sells} sell event{'s' if report.n_sells != 1 else ''}",
            pnl_tone(report.realized),
        ),
        KpiCard("Closed Trades", str(report.n_closed), closed_detail, "neutral"),
        KpiCard("Win Rate", win_value, win_detail, "neutral"),
        KpiCard("Avg Hold", hold_value, hold_detail, "neutral"),
        KpiCard(
            "Open uPnL",
            signed_money(report.open_upnl),
            "point-in-time mark",
            pnl_tone(report.open_upnl),
        ),
    ]


def kpi_line(report: StatsReport) -> str:
    """One-line KPI strip — same facts as the cards, without the tall stack."""
    return "    ".join(f"{card.title} {card.value}" for card in kpi_cards(report))
