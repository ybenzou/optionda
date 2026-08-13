from datetime import datetime, timezone

from optionda.display.table import (
    _dir_style,
    _inline_bar,
    _iv_asof_label,
    _model_iv_cell,
    _money_flash,
    _move_direction,
    _pnl_flash,
    _spot_cell,
    _spot_chg_pct,
)


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
