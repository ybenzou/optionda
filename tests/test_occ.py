from datetime import date

import pytest

from optionda.occ import OccError, format_occ, parse_occ


def test_parse_occ_call() -> None:
    parts = parse_occ("AAPL250117C00200000")
    assert parts.underlying == "AAPL"
    assert parts.expiry == date(2025, 1, 17)
    assert parts.option_type == "call"
    assert parts.strike == 200.0
    assert parts.occ_symbol == "AAPL250117C00200000"


def test_parse_occ_put_fractional_strike() -> None:
    parts = parse_occ("TSLA240719P00222500")
    assert parts.option_type == "put"
    assert parts.strike == 222.5


def test_format_roundtrip() -> None:
    symbol = format_occ("MSFT", date(2026, 6, 19), "call", 400)
    assert symbol == "MSFT260619C00400000"
    assert parse_occ(symbol).strike == 400


def test_invalid_occ() -> None:
    with pytest.raises(OccError):
        parse_occ("NOTANOPTION")
