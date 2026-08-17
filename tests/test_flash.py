from datetime import date, datetime, timezone

from optionda.display.table import (
    _STATUS_WIDTH,
    _dir_style,
    _fit_width,
    _inline_bar,
    _iv_asof_label,
    _model_cell,
    _model_iv_cell,
    _money_flash,
    _move_direction,
    _pnl_flash,
    _premium_chg,
    _spot_cell,
    _spot_chg_pct,
    format_poll_status,
    render_snapshot,
    sort_desk_rows,
)
from optionda.models import Position, RowMark


def test_move_direction():
    assert _move_direction(10.0, 9.0) == 1
    assert _move_direction(9.0, 10.0) == -1
    assert _move_direction(10.0, 10.0) == 0
    assert _move_direction(None, 1.0) == 0


def test_drop_flashes_red_even_when_pnl_still_positive():
    # Absolute uPnL still green (+100), but tick dropped from +120 → flash red.
    text = _pnl_flash(100.0, 120.0, phase="hot")
    assert "on dark_red" in text.style
    assert "+100.00" in text.plain


def test_rise_flashes_green_background():
    text = _money_flash(5.25, 5.00, phase="hot")
    assert "on dark_green" in text.style
    assert "5.25" in text.plain


def test_idle_keeps_soft_direction_tint():
    up = _money_flash(5.25, 5.00, phase="idle")
    down = _money_flash(4.80, 5.00, phase="idle")
    assert up.style == "green"
    assert down.style == "red"


def test_dir_style_phases():
    assert "on dark_green" in _dir_style(1, phase="hot")
    assert _dir_style(-1, phase="warm") == "bold red"
    assert _dir_style(0, phase="hot") == "dim"


def test_poll_status_width_is_stable():
    fetch = format_poll_status(
        "1/2 fetch  spots · AAPL AVGO CSCO GOOG HOOD IBM INTC",
        busy=True,
        done=0,
        total=1,
    )
    mark = format_poll_status(
        "2/2 mark  SKHY261016C00200000",
        busy=True,
        done=3,
        total=11,
    )
    idle = format_poll_status("5s", busy=False, eta_sec=5)
    assert fetch == _fit_width("1/2 fetch   0/1  spots")
    assert "spots ·" not in fetch
    assert "SKHY261016C00200000" in mark
    assert len(fetch) == _STATUS_WIDTH
    assert len(mark) == _STATUS_WIDTH
    assert len(idle) == _STATUS_WIDTH


def test_inline_bar_fills():
    empty = _inline_bar(0.0, width=10)
    full = _inline_bar(1.0, width=10)
    busy = _inline_bar(0.5, width=10, busy=True)
    assert empty.plain == "─" * 10
    assert full.plain == "━" * 10
    assert "yellow" in str(busy.style)


def test_spot_chg_pct_vs_close():
    up = _spot_chg_pct(146.81, 141.65)
    assert up is not None
    assert "+3.6%" in up.plain
    assert "green" in str(up.style)

    down = _spot_chg_pct(140.0, 141.65)
    assert down is not None
    assert "-1.2%" in down.plain
    assert "red" in str(down.style)

    cell = _spot_cell(146.81, 141.65, None, phase="idle")
    assert "146.81" in cell.plain
    assert "+3.6%" in cell.plain


def test_model_cell_shows_dollar_chg_vs_close():
    up = _premium_chg(12.00, 10.80)
    assert up is not None
    assert "+1.20" in up.plain
    assert "green" in str(up.style)

    down = _premium_chg(10.45, 10.80)
    assert down is not None
    assert "-0.35" in down.plain
    assert "red" in str(down.style)

    cell = _model_cell(12.00, 10.80, None, phase="idle")
    assert "12.00" in cell.plain
    assert "+1.20" in cell.plain


def test_model_iv_shows_et_session_date():
    as_of = datetime(2026, 8, 12, 20, 5, tzinfo=timezone.utc)  # 16:05 ET
    label = _iv_asof_label(as_of)
    assert label == "8/12"

    cell = _model_iv_cell(0.779, as_of, stale=False)
    assert "77.9%" in cell.plain
    assert "8/12" in cell.plain

    stale = _model_iv_cell(0.40, as_of, stale=True)
    assert "8/12" in stale.plain
    assert any(span.style == "bold yellow" for span in stale.spans)


def test_continuous_desk_has_no_bottom_live_caption() -> None:
    pos = Position(
        occ_symbol="AAPL261120C00350000",
        underlying="AAPL",
        expiry=date(2026, 11, 20),
        strike=350.0,
        option_type="call",
        qty=1,
        side="long",
        iv_frozen=0.25,
        iv_as_of=datetime(2026, 8, 12, 20, tzinfo=timezone.utc),
        entry_premium=3.5,
    )
    group = render_snapshot(
        account="main",
        feed="alpaca",
        refresh_sec=15,
        rows=[
            RowMark(
                position=pos,
                spot=305.0,
                theo=3.8,
                delta=0.19,
                dte=98.0,
                notional=380.0,
                cost=3.5,
                upnl=30.0,
            )
        ],
        continuous=True,
        spin="⠹",
    )
    panel = group.renderables[0]
    table = panel.renderable.renderables[-1]
    assert table.caption is None


def _row(occ: str, notional: float | None) -> RowMark:
    return RowMark(
        position=Position(
            occ_symbol=occ,
            underlying=occ[:4],
            expiry=date(2026, 12, 18),
            strike=100.0,
            option_type="call",
            qty=1,
            side="long",
            iv_frozen=0.25,
            iv_as_of=datetime(2026, 8, 12, 20, tzinfo=timezone.utc),
            entry_premium=3.5,
        ),
        spot=100.0,
        theo=None if notional is None else notional / 100.0,
        delta=0.2,
        dte=90.0,
        notional=notional,
        cost=3.5,
        upnl=None,
        error="no spot" if notional is None else None,
    )


def test_desk_rows_sort_by_abs_notional() -> None:
    ordered = sort_desk_rows(
        [
            _row("CSCO261218C00130000", 1086.0),
            _row("HOOD261218C00150000", 909.0),
            _row("AVGO261218C00500000", 3350.0),
            _row("BADX261218C00100000", None),
        ]
    )
    assert [row.position.occ_symbol[:4] for row in ordered] == [
        "AVGO",
        "CSCO",
        "HOOD",
        "BADX",
    ]
