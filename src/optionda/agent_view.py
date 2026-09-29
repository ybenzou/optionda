from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from optionda.display.table import (
    _last_op_label,
    partition_desk_rows,
    today_model_pnl,
)
from optionda.models import RowMark
from optionda.paths import ensure_home


def latest_path(home: Path | None = None) -> Path:
    return ensure_home(home) / "agent" / "latest.json"


def _spot_chg_pct(spot: float | None, close_spot: float | None) -> float | None:
    if spot is None or close_spot is None or close_spot <= 0:
        return None
    return (spot / close_spot - 1.0) * 100.0


def _theo_chg(row: RowMark) -> float | None:
    if row.theo_chg is not None:
        return row.theo_chg
    if row.theo is None or row.close_premium is None:
        return None
    return row.theo - row.close_premium


def _row_payload(row: RowMark, section: str) -> dict[str, Any]:
    pos = row.position
    model_iv = (
        row.model_iv
        if row.model_iv is not None
        else row.surface_iv if row.surface_iv is not None else pos.iv_frozen
    )
    return {
        "occ": pos.occ_symbol,
        "side": pos.side,
        "qty": pos.qty,
        "spot": row.spot,
        "close_spot": row.close_spot,
        "spot_chg_pct": _spot_chg_pct(row.spot, row.close_spot),
        "model_iv": model_iv,
        "cost": row.cost if row.cost is not None else pos.entry_premium,
        "model": row.theo,
        "close_premium": row.close_premium,
        "theo_chg": _theo_chg(row),
        "upnl": row.upnl,
        "today": today_model_pnl(row),
        "dte": row.dte,
        "last": _last_op_label(row.last_op_at),
        "section": section,
    }


def build_agent_view(
    *,
    account: str,
    feed: str,
    rows: list[RowMark],
    realized: float | None = None,
    ts: str | None = None,
    news: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    up_rows, down_rows = partition_desk_rows(rows)
    up = [_row_payload(row, "+") for row in up_rows]
    down = [_row_payload(row, "−") for row in down_rows]
    sum_model = 0.0
    sum_upnl = 0.0
    has_upnl = False
    tpnl = 0.0
    has_today = False
    for row in (*up_rows, *down_rows):
        if row.notional is not None:
            sum_model += row.notional
        if row.upnl is not None:
            sum_upnl += row.upnl
            has_upnl = True
        day = today_model_pnl(row)
        if day is not None:
            tpnl += day
            has_today = True
    stamp = ts or datetime.now(timezone.utc).isoformat()
    return {
        "account": account,
        "ts": stamp,
        "feed": feed,
        "n": len(rows),
        "sum_model": sum_model,
        "sum_upnl": sum_upnl if has_upnl else None,
        "rpnl": realized,
        "tpnl": tpnl if has_today else None,
        "up": up,
        "down": down,
        "news": list(news or []),
    }


def write_latest(view: dict[str, Any], home: Path | None = None) -> Path:
    path = latest_path(home)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(view, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)
    return path


def load_latest(home: Path | None = None) -> dict[str, Any] | None:
    path = latest_path(home)
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _money(value: float | None) -> str:
    if value is None:
        return "—"
    return f"{value:,.2f}"


def _pnl(value: float | None) -> str:
    if value is None:
        return "—"
    if abs(value) < 1e-9:
        return "0.00"
    sign = "+" if value > 0 else ""
    return f"{sign}{value:,.2f}"


def _iv(value: float | None) -> str:
    if value is None:
        return "—"
    return f"{value * 100:.1f}%"


def _spot_suffix(pct: float | None) -> str:
    if pct is None:
        return ""
    if abs(pct) < 0.05:
        return " (0.0%)"
    return f" ({pct:+.1f}%)"


def _model_suffix(chg: float | None) -> str:
    if chg is None:
        return ""
    if abs(chg) < 0.005:
        return " (0.00)"
    return f" ({chg:+.2f})"


def _move_color(value: float | None, *, zero: float, up: str, down: str, flat: str) -> str:
    if value is None or abs(value) < zero:
        return flat
    return up if value > 0 else down


def _group_today(rows: list[dict[str, Any]]) -> float | None:
    total = 0.0
    found = False
    for row in rows:
        day = row.get("today")
        if day is None:
            continue
        total += float(day)
        found = True
    return total if found else None


def inbox_line(view: dict[str, Any]) -> str:
    """First line of the mail. Inbox previews stop before the position table."""
    account = view.get("account") or "optionda"
    up = _pnl(_group_today(view.get("up") or []))
    down = _pnl(_group_today(view.get("down") or []))
    return f"[{account}] optionda  today + {up}  today − {down}"


def format_agent_text(view: dict[str, Any]) -> str:
    lines = [inbox_line(view)]
    for title, key in (
        ("today +", "up"),
        ("today −", "down"),
    ):
        rows = view.get(key) or []
        total = 0.0
        found = False
        for row in rows:
            day = row.get("today")
            if day is None:
                continue
            total += float(day)
            found = True
        label = title
        if found:
            label = f"{title}    {_pnl(total)}"
        lines.append("")
        lines.append(label)
        lines.append(
            f"{'OCC':<20} {'Side':<5} {'Qty':>4} {'Spot':>16} {'Model IV':>9} "
            f"{'Cost':>8} {'Model$':>16} {'uPnL$':>10} {'DTE':>6} {'Last':>6}"
        )
        if not rows:
            lines.append("(empty)")
            continue
        for row in rows:
            dte = row.get("dte")
            dte_s = f"{dte:.1f}" if dte is not None else "—"
            spot = f"{_money(row.get('spot'))}{_spot_suffix(row.get('spot_chg_pct'))}"
            model = f"{_money(row.get('model'))}{_model_suffix(row.get('theo_chg'))}"
            lines.append(
                f"{str(row.get('occ') or ''):<20} "
                f"{str(row.get('side') or ''):<5} "
                f"{row.get('qty') or 0:>4g} "
                f"{spot:>16} "
                f"{_iv(row.get('model_iv')):>9} "
                f"{_money(row.get('cost')):>8} "
                f"{model:>16} "
                f"{_pnl(row.get('upnl')):>10} "
                f"{dte_s:>6} "
                f"{str(row.get('last') or '—'):>6}"
            )
    lines.append("")
    lines.append(f"Σ {_money(view.get('sum_model'))}")
    lines.append(f"rPnL {_pnl(view.get('rpnl'))}    tPnL {_pnl(view.get('tpnl'))}")
    news = view.get("news") or []
    if news:
        lines.append("")
        lines.append("News")
        for item in news:
            stamp = _news_stamp(item.get("ts"))
            symbol = str(item.get("symbol") or "?").strip() or "?"
            rank = str(item.get("rank") or "").strip()
            headline = str(item.get("headline") or "").strip()
            bits = [part for part in (stamp, symbol, rank, headline) if part]
            lines.append("  ".join(bits))
    return "\n".join(lines)


def _news_stamp(value: Any) -> str:
    if isinstance(value, datetime):
        instant = value
    elif isinstance(value, str) and value.strip():
        try:
            instant = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError:
            return ""
    else:
        return ""
    if instant.tzinfo is None:
        instant = instant.replace(tzinfo=timezone.utc)
    return instant.astimezone().strftime("%H:%M")


def _news_html(
    news: list[dict[str, Any]],
    *,
    muted: str,
    cyan: str,
    fg: str,
) -> str:
    if not news:
        return ""

    def cell(
        text: str,
        *,
        color: str,
        nowrap: bool = False,
        weight: str = "normal",
    ) -> str:
        extra = "white-space:nowrap;" if nowrap else ""
        return (
            f'<td style="padding:5px 12px 5px 0;text-align:left;vertical-align:top;'
            f"color:{color};font-family:Consolas,Menlo,monospace;font-size:12px;"
            f'font-weight:{weight};{extra}">{text}</td>'
        )

    bits = [
        f'<p style="margin:18px 0 8px;color:{cyan};font-family:Consolas,Menlo,monospace;'
        f'font-size:13px;font-weight:bold;">News</p>',
        '<table style="border-collapse:collapse;width:100%;">',
    ]
    for item in news:
        stamp = _news_stamp(item.get("ts")) or "—"
        symbol = str(item.get("symbol") or "?").strip() or "?"
        headline = str(item.get("headline") or "").strip()
        url = str(item.get("url") or "").strip()
        rank = str(item.get("rank") or "").strip()
        title = headline
        if rank:
            title = (
                f'<span style="color:{cyan};padding-right:8px;">{rank}</span>{headline}'
            )
        if url:
            title = (
                f'<a href="{url}" style="color:{fg};text-decoration:underline;">'
                f"{title}</a>"
            )
        bits.append(
            "<tr>"
            + cell(stamp, color=muted, nowrap=True)
            + cell(symbol, color=cyan, nowrap=True, weight="bold")
            + cell(title, color=fg)
            + "</tr>"
        )
    bits.append("</table>")
    return "".join(bits)


def render_desk_html(view: dict[str, Any]) -> str:
    bg = "#1a1a1a"
    fg = "#f3f3f3"
    muted = "#9a9a9a"
    cyan = "#3a96dd"
    green = "#16c60c"
    red = "#e74856"

    def cell(text: str, *, align: str = "right", color: str = fg) -> str:
        return (
            f'<td style="padding:3px 8px;text-align:{align};color:{color};'
            f'font-family:Consolas,Menlo,monospace;font-size:12px;'
            f'white-space:nowrap;">{text}</td>'
        )

    def section_html(title: str, rows: list[dict[str, Any]], color: str) -> str:
        total = 0.0
        found = False
        for row in rows:
            day = row.get("today")
            if day is None:
                continue
            total += float(day)
            found = True
        head = title
        if found:
            head = f"{title}&nbsp;&nbsp;{_pnl(total)}"
        bits = [
            f'<p style="margin:14px 0 6px;color:{color};font-family:Consolas,Menlo,monospace;'
            f'font-size:13px;font-weight:bold;">{head}</p>',
            '<table style="border-collapse:collapse;width:100%;">',
            "<tr>"
            + "".join(
                cell(name, align="left" if name == "OCC" else "right", color=muted)
                for name in (
                    "OCC",
                    "Side",
                    "Qty",
                    "Spot",
                    "Model IV",
                    "Cost",
                    "Model$",
                    "uPnL$",
                    "DTE",
                    "Last",
                )
            )
            + "</tr>",
        ]
        if not rows:
            bits.append("<tr>" + cell("(empty)", align="left", color=muted) + "</tr>")
        for row in rows:
            upnl = row.get("upnl")
            pnl_color = green if (upnl or 0) > 0 else (red if (upnl or 0) < 0 else fg)
            spot_pct = row.get("spot_chg_pct")
            spot_color = _move_color(spot_pct, zero=0.05, up=green, down=red, flat=muted)
            theo_chg = row.get("theo_chg")
            model_color = _move_color(theo_chg, zero=0.005, up=green, down=red, flat=muted)
            bits.append(
                "<tr>"
                + cell(str(row.get("occ") or ""), align="left")
                + cell(str(row.get("side") or ""), align="center")
                + cell(f"{row.get('qty') or 0:g}")
                + cell(
                    f"{_money(row.get('spot'))}"
                    f'<span style="color:{spot_color}">{_spot_suffix(spot_pct)}</span>'
                )
                + cell(_iv(row.get("model_iv")), color=cyan)
                + cell(_money(row.get("cost")))
                + cell(
                    f"{_money(row.get('model'))}"
                    f'<span style="color:{model_color}">{_model_suffix(theo_chg)}</span>'
                )
                + cell(_pnl(upnl), color=pnl_color)
                + cell(f"{row.get('dte'):.1f}" if row.get("dte") is not None else "—")
                + cell(str(row.get("last") or "—"))
                + "</tr>"
            )
        bits.append("</table>")
        return "".join(bits)

    account = view.get("account") or "optionda"
    preview = inbox_line(view)
    body = (
        f'<div style="display:none;max-height:0;overflow:hidden;color:{bg};">{preview}</div>'
        f'<div style="background:{bg};color:{fg};padding:16px;'
        f'font-family:Consolas,Menlo,monospace;">'
        f'<p style="margin:0 0 8px;color:{cyan};font-weight:bold;">{preview}</p>'
        f"{section_html('today +', view.get('up') or [], green)}"
        f"{section_html('today −', view.get('down') or [], red)}"
        f'<p style="margin:16px 0 0;color:{cyan};">Σ {_money(view.get("sum_model"))}</p>'
        f'<p style="margin:4px 0 0;">rPnL {_pnl(view.get("rpnl"))}'
        f'&nbsp;&nbsp;&nbsp;tPnL {_pnl(view.get("tpnl"))}</p>'
        f"{_news_html(view.get('news') or [], muted=muted, cyan=cyan, fg=fg)}"
        "</div>"
    )
    return (
        "<!DOCTYPE html><html><head><meta charset='utf-8'></head>"
        f"<body style='margin:0;background:{bg};'>{body}</body></html>"
    )
