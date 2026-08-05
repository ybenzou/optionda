from __future__ import annotations

from pathlib import Path

from optionda.batch import read_batch_lines
from optionda.occ import OccError, parse_occ, parse_position_line


def resolve_add_lines(items: list[str]) -> list[str]:
    """Normalize CLI tokens into one or more position lines.

    Supports:
      - single/multiple OCC symbols
      - one human line split across argv: INTC 261016 140 C
      - file path or '-' (stdin) for multi-line batch
    """
    if not items:
        raise ValueError("no positions provided")

    if len(items) == 1:
        token = items[0]
        if token == "-" or Path(token).is_file():
            lines = read_batch_lines(token)
            if not lines:
                raise ValueError("no positions to add (empty input)")
            return lines
        # single OCC or compact human-ish token
        try:
            return [parse_position_line(token).occ_symbol]
        except OccError as exc:
            raise ValueError(str(exc)) from exc

    # Multiple argv tokens: either many OCCs, or one spaced human line
    joined = " ".join(items)
    try:
        return [parse_position_line(joined).occ_symbol]
    except OccError:
        pass

    lines: list[str] = []
    for token in items:
        try:
            lines.append(parse_position_line(token).occ_symbol)
        except OccError as exc:
            raise ValueError(
                f"could not parse {token!r} (also not a single human line: {joined!r})"
            ) from exc
    return lines


def looks_like_field_add(
    items: list[str],
    underlying: str | None,
    expiry: str | None,
    strike: float | None,
    option_type: str | None,
) -> bool:
    return (
        not items
        and underlying is not None
        and expiry is not None
        and strike is not None
        and option_type is not None
    )
