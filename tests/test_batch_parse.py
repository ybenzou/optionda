from datetime import date

import pytest

from optionda.add_resolve import resolve_add_lines
from optionda.occ import (
    OccError,
    parse_leg_line,
    parse_position_line,
    require_entry,
    resolve_qty,
)


def test_parse_human_spaced() -> None:
    p = parse_position_line("INTC 261016 140 C")
    assert p.occ_symbol == "INTC261016C00140000"
    assert p.expiry == date(2026, 10, 16)
    assert p.strike == 140
    assert p.option_type == "call"


def test_parse_with_cost() -> None:
    leg = parse_leg_line("INTC 261016 140 C @ 5.20")
    assert leg.parts.occ_symbol == "INTC261016C00140000"
    assert leg.entry == pytest.approx(5.20)


def test_parse_qty_and_cost() -> None:
    a = parse_leg_line("INTC 261016 140 C x10 @ 3.482")
    assert a.qty == 10
    assert a.entry == pytest.approx(3.482)
    b = parse_leg_line("INTC 261016 140 C @ 3.482 x10")
    assert b.qty == 10
    assert b.entry == pytest.approx(3.482)
    c = parse_leg_line("SKHY261016C00200000 *1 @ 9.5")
    assert c.qty == 1
    assert c.entry == pytest.approx(9.5)


def test_semi_separated_preserves_qty_and_cost() -> None:
    lines = resolve_add_lines(
        [
            "INTC 261016 140 C x10 @ 3.482; "
            "SKHY 261016 200 C x1 @ 9.5; "
            "SPCX 260918 100 P x2 @ 6.7"
        ]
    )
    assert len(lines) == 3
    legs = [parse_leg_line(line) for line in lines]
    assert legs[0].qty == 10 and legs[0].entry == pytest.approx(3.482)
    assert legs[1].qty == 1 and legs[1].entry == pytest.approx(9.5)
    assert legs[2].qty == 2 and legs[2].entry == pytest.approx(6.7)


def test_parse_occ_with_cost() -> None:
    leg = parse_leg_line("AAPL261120C00350000 @ 12")
    assert leg.parts.underlying == "AAPL"
    assert leg.entry == 12.0


def test_require_entry_from_cli_or_line() -> None:
    assert require_entry(5.2, None) == 5.2
    assert require_entry(None, 3.0) == 3.0
    with pytest.raises(OccError, match="cost required"):
        require_entry(None, None)
    assert resolve_qty(10, 1) == 10
    assert resolve_qty(None, 2) == 2


def test_parse_compact_head() -> None:
    p = parse_position_line("INTC261016 140 CALL")
    assert p.occ_symbol == "INTC261016C00140000"


def test_parse_put() -> None:
    p = parse_position_line("SPCX 260918 100 P")
    assert p.occ_symbol == "SPCX260918P00100000"
    assert p.option_type == "put"


def test_parse_full_occ() -> None:
    p = parse_position_line("AAPL261120C00350000")
    assert p.underlying == "AAPL"
    assert p.strike == 350


def test_bad_line() -> None:
    with pytest.raises(OccError):
        parse_position_line("not a contract")
