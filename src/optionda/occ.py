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


_CP_MAP = {
    "C": "call",
    "CALL": "call",
    "P": "put",
    "PUT": "put",
}

# ROOT + YYMMDD as one token, then strike, then C/P
_COMPACT_RE = re.compile(
    r"^(?P<root>[A-Z]{1,6})"
    r"(?P<yymmdd>\d{6})"
    r"$"
)


def parse_position_line(line: str) -> OccParts:
    """Parse OCC or human lines like 'INTC 261016 140 C' / 'INTC261016 140 CALL'."""
    raw = line.strip()
    if not raw or raw.startswith("#"):
        raise OccError("empty line")
    # Full OCC first
    compact = raw.upper().replace(" ", "")
    try:
        return parse_occ(compact)
    except OccError:
        pass

    tokens = raw.upper().replace(",", " ").split()
    if len(tokens) == 4:
        root, yymmdd, strike_s, cp = tokens
        if not re.fullmatch(r"\d{6}", yymmdd):
            raise OccError(f"invalid expiry token in: {line}")
    elif len(tokens) == 3:
        head, strike_s, cp = tokens
        m = _COMPACT_RE.match(head)
        if not m:
            raise OccError(f"invalid line (want ROOT YYMMDD STRIKE C|P): {line}")
        root = m.group("root")
        yymmdd = m.group("yymmdd")
    else:
        raise OccError(f"invalid line (want ROOT YYMMDD STRIKE C|P): {line}")

    cp_n = _CP_MAP.get(cp)
    if cp_n is None:
        raise OccError(f"invalid call/put token in: {line}")
    try:
        strike = float(strike_s)
    except ValueError as exc:
        raise OccError(f"invalid strike in: {line}") from exc
    yy, mm, dd = int(yymmdd[:2]), int(yymmdd[2:4]), int(yymmdd[4:6])
    try:
        expiry = date(2000 + yy, mm, dd)
    except ValueError as exc:
        raise OccError(f"invalid expiry in: {line}") from exc
    otype: OptionType = cp_n  # type: ignore[assignment]
    symbol = format_occ(root, expiry, otype, strike)
    return parse_occ(symbol)


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
