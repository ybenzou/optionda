from optionda.display.table import (
    _dir_style,
    _inline_bar,
    _money_flash,
    _move_direction,
    _pnl_flash,
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
