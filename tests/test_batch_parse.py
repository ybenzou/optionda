from datetime import date

import pytest

from optionda.occ import OccError, parse_position_line


def test_parse_human_spaced() -> None:
    p = parse_position_line("INTC 261016 140 C")
    assert p.occ_symbol == "INTC261016C00140000"
    assert p.expiry == date(2026, 10, 16)
    assert p.strike == 140
    assert p.option_type == "call"


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
