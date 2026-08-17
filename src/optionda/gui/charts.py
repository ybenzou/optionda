"""Cumulative realized series for pyqtgraph. Pure data helpers stay Qt-free."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone

from optionda.analytics import StatsReport


def day_ts(day: date) -> float:
    return datetime.combine(day, time.min, tzinfo=timezone.utc).timestamp()


def mark_xy(report: StatsReport) -> tuple[list[float], list[float]]:
    if not report.mark_curve:
        return step_xy(report)
    xs = [day_ts(day) for day, _value in report.mark_curve]
    ys = [value for _day, value in report.mark_curve]
    return xs, ys


def position_mark_xy(
    report: StatsReport,
    position_id: str,
) -> tuple[list[float], list[float]]:
    series = report.position_curves.get(position_id) or []
    if not series:
        return position_step_xy(report, position_id)
    return (
        [day_ts(day) for day, _value in series],
        [value for _day, value in series],
    )


def _visible_start(report: StatsReport, first_sell: date) -> date:
    """Start just before the first sell — do not pad back to a distant period_start."""
    span = max((report.as_of - first_sell).days, 1)
    pad = min(14, max(1, span // 12))
    lead = first_sell - timedelta(days=pad)
    if report.period_start is not None and report.period_start > lead:
        return report.period_start
    return lead


def step_xy(report: StatsReport) -> tuple[list[float], list[float]]:
    """Date-axis step path: start at 0 near the first sell, jump only on sell days."""
    if not report.cumulative:
        start = report.period_start or report.as_of
        ts = day_ts(start)
        end = day_ts(report.as_of)
        if end <= ts:
            return [ts], [0.0]
        return [ts, end], [0.0, 0.0]

    start = _visible_start(report, report.cumulative[0][0])
    xs = [day_ts(start)]
    ys = [0.0]
    last = 0.0
    for day, value in report.cumulative:
        ts = day_ts(day)
        if ts > xs[-1]:
            xs.append(ts)
            ys.append(last)
        xs.append(ts)
        ys.append(value)
        last = value
    end = day_ts(report.as_of)
    if end > xs[-1]:
        xs.append(end)
        ys.append(last)
    return xs, ys


def sell_points(report: StatsReport) -> tuple[list[float], list[float]]:
    return (
        [day_ts(day) for day, _value in report.cumulative],
        [value for _day, value in report.cumulative],
    )


def position_sells(report: StatsReport, position_id: str):
    found = []
    for daily in report.calendar:
        for sell in daily.sells:
            if sell.position_id == position_id:
                found.append(sell)
    found.sort(key=lambda item: item.ts)
    return found


def _opened_day(report: StatsReport, position_id: str) -> date | None:
    for lot in (*report.open_lots, *report.closed_lots):
        if lot.position_id != position_id or lot.opened_at is None:
            continue
        opened = lot.opened_at
        return opened.date() if isinstance(opened, datetime) else opened
    return None


def _flat_window(report: StatsReport, start: date) -> tuple[list[float], list[float]]:
    begin = start
    if report.period_start is not None and report.period_start > begin:
        begin = report.period_start
    if begin > report.as_of:
        begin = report.as_of
    ts = day_ts(begin)
    end = day_ts(report.as_of)
    if end <= ts:
        end = ts + 86400
    return [ts, end], [0.0, 0.0]


def position_step_xy(
    report: StatsReport,
    position_id: str,
) -> tuple[list[float], list[float]]:
    """Realized step path for one position; flat zero if it has no sells yet."""
    sells = position_sells(report, position_id)
    if not sells:
        start = _opened_day(report, position_id) or report.period_start
        if start is None:
            start = report.as_of - timedelta(days=14)
        return _flat_window(report, start)
    start = _visible_start(report, sells[0].et_date)
    xs = [day_ts(start)]
    ys = [0.0]
    last = 0.0
    running = 0.0
    for sell in sells:
        running += sell.realized
        ts = day_ts(sell.et_date)
        if ts > xs[-1]:
            xs.append(ts)
            ys.append(last)
        xs.append(ts)
        ys.append(running)
        last = running
    end = day_ts(report.as_of)
    if end > xs[-1]:
        xs.append(end)
        ys.append(last)
    return xs, ys


def position_sell_points(
    report: StatsReport,
    position_id: str,
) -> tuple[list[float], list[float]]:
    running = 0.0
    xs: list[float] = []
    ys: list[float] = []
    for sell in position_sells(report, position_id):
        running += sell.realized
        xs.append(day_ts(sell.et_date))
        ys.append(running)
    return xs, ys
