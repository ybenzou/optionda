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

# @ 3.482   or   @ 3.482 x10
_COST_QTY_RE = re.compile(
    r"@\s*(?P<cost>[0-9]+(?:\.[0-9]+)?)"
    r"(?:\s*(?:x|×|\*)\s*(?P<qty>[0-9]+(?:\.[0-9]+)?))?\s*$",
    re.IGNORECASE,
)
# trailing x10 / *10 (when cost is separate or absent)
_QTY_RE = re.compile(
    r"(?:\s|^)(?:x|×|\*)\s*(?P<qty>[0-9]+(?:\.[0-9]+)?)\s*$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class OccParts:
    underlying: str
    expiry: date
    option_type: OptionType
    strike: float
    occ_symbol: str


@dataclass(frozen=True)
class ParsedLeg:
    parts: OccParts
    entry: float | None = None
    qty: float | None = None


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

_COMPACT_RE = re.compile(
    r"^(?P<root>[A-Z]{1,6})"
    r"(?P<yymmdd>\d{6})"
    r"$"
)


def split_cost_qty(line: str) -> tuple[str, float | None, float | None]:
    """Split contract / cost / qty suffixes.

    Examples:
      INTC 261016 140 C x10 @ 3.482  → (contract, 3.482, 10)
      INTC 261016 140 C @ 3.482 x10  → (contract, 3.482, 10)
      INTC 261016 140 C @ 3.482      → (contract, 3.482, None)
    """
    raw = line.strip()
    entry: float | None = None
    qty: float | None = None

    cost_m = _COST_QTY_RE.search(raw)
    if cost_m:
        entry = float(cost_m.group("cost"))
        if cost_m.group("qty"):
            qty = float(cost_m.group("qty"))
        raw = raw[: cost_m.start()].strip()

    qty_m = _QTY_RE.search(raw)
    if qty_m:
        if qty is None:
            qty = float(qty_m.group("qty"))
        raw = raw[: qty_m.start()].strip()

    return raw, entry, qty


def split_cost_suffix(line: str) -> tuple[str, float | None]:
    """Back-compat: contract text + optional @ cost."""
    head, entry, _qty = split_cost_qty(line)
    return head, entry


def parse_position_line(line: str) -> OccParts:
    """Parse contract text only (no @cost / qty). Prefer parse_leg_line for adds."""
    return parse_leg_line(line).parts


def parse_leg_line(line: str) -> ParsedLeg:
    """Parse OCC/human line, optional 'xQTY' and trailing '@ cost'."""
    raw = line.strip()
    if not raw or raw.startswith("#"):
        raise OccError("empty line")
    # Allow book-file style: "INTC 261016 140 C @ 5.2  # qty=1 …"
    if "#" in raw:
        raw = raw.split("#", 1)[0].strip()
    if not raw:
        raise OccError("empty line")
    head, entry, qty = split_cost_qty(raw)
    if qty is not None and qty <= 0:
        raise OccError(f"qty must be > 0 in: {line}")

    # Full OCC (possibly with spaces removed)
    compact = head.upper().replace(" ", "")
    try:
        return ParsedLeg(parts=parse_occ(compact), entry=entry, qty=qty)
    except OccError:
        pass

    tokens = head.upper().replace(",", " ").split()
    if len(tokens) == 4:
        root, yymmdd, strike_s, cp = tokens
        if not re.fullmatch(r"\d{6}", yymmdd):
            raise OccError(f"invalid expiry token in: {line}")
    elif len(tokens) == 3:
        head_tok, strike_s, cp = tokens
        m = _COMPACT_RE.match(head_tok)
        if not m:
            raise OccError(
                f"invalid line (want ROOT YYMMDD STRIKE C|P [xQTY] [@ cost]): {line}"
            )
        root = m.group("root")
        yymmdd = m.group("yymmdd")
    else:
        raise OccError(
            f"invalid line (want ROOT YYMMDD STRIKE C|P [xQTY] [@ cost]): {line}"
        )

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
    return ParsedLeg(parts=parse_occ(symbol), entry=entry, qty=qty)


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


def require_entry(line_entry: float | None, cli_entry: float | None) -> float:
    value = line_entry if line_entry is not None else cli_entry
    if value is None or value <= 0:
        raise OccError(
            "cost required — use '@ 5.20' on the line or pass --entry 5.20"
        )
    return float(value)


def resolve_qty(line_qty: float | None, cli_qty: float) -> float:
    """Per-line xQTY wins; otherwise CLI --qty (default 1)."""
    if line_qty is not None:
        if line_qty <= 0:
            raise OccError("qty must be > 0")
        return float(line_qty)
    if cli_qty <= 0:
        raise OccError("--qty must be > 0")
    return float(cli_qty)
