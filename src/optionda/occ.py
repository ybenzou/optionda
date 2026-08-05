from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

from optionda.models import OptionType

# OCC: ROOT + YYMMDD + C/P + strike*1000 zero-padded 8
_OCC_RE = re.compile(
    r"^(?P<root>[A-Z]{1,6})"
    r"(?P<yy>\d{2})(?P<mm>\d{2})(?P<dd>\d{2})"
    r"(?P<cp>[CP])"
    r"(?P<strike>\d{8})$"
)


@dataclass(frozen=True)
class OccParts:
    underlying: str
    expiry: date
    option_type: OptionType
    strike: float
    occ_symbol: str


class OccError(ValueError):
    pass


def parse_occ(symbol: str) -> OccParts:
    raw = symbol.strip().upper().replace(" ", "")
    match = _OCC_RE.match(raw)
    if not match:
        raise OccError(f"invalid OCC symbol: {symbol}")
    year = 2000 + int(match.group("yy"))
    month = int(match.group("mm"))
    day = int(match.group("dd"))
    try:
        expiry = date(year, month, day)
    except ValueError as exc:
        raise OccError(f"invalid OCC expiry in {symbol}") from exc
    strike = int(match.group("strike")) / 1000.0
    option_type: OptionType = "call" if match.group("cp") == "C" else "put"
    return OccParts(
        underlying=match.group("root"),
        expiry=expiry,
        option_type=option_type,
        strike=strike,
        occ_symbol=raw,
    )


def format_occ(
    underlying: str,
    expiry: date,
    option_type: OptionType,
    strike: float,
) -> str:
    root = underlying.strip().upper()
    if not re.fullmatch(r"[A-Z]{1,6}", root):
        raise OccError(f"invalid underlying for OCC: {underlying}")
    cp = "C" if option_type == "call" else "P"
    strike_i = int(round(strike * 1000))
    if strike_i < 0 or strike_i > 99_999_999:
        raise OccError(f"strike out of OCC range: {strike}")
    return (
        f"{root}"
        f"{expiry.year % 100:02d}{expiry.month:02d}{expiry.day:02d}"
        f"{cp}"
        f"{strike_i:08d}"
    )
