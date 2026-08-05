from __future__ import annotations

from pathlib import Path

from optionda.batch import read_batch_lines
from optionda.occ import OccError, parse_position_line


def split_semi_separated(text: str) -> list[str]:
    """Split 'A; B; C' into position lines (keeps spaces inside each part)."""
    parts = [p.strip() for p in text.replace("\n", ";").split(";")]
    return [p for p in parts if p and not p.startswith("#")]


def read_interactive_lines(
    *,
    prompt_print=print,
    line_input=input,
) -> list[str]:
    """Read pasted lines until a blank line or EOF (Ctrl+Z Enter on Windows)."""
    prompt_print("Paste positions (one per line).")
    prompt_print("Finish with an empty line, or Ctrl+Z then Enter (Windows).")
    prompt_print("")
    lines: list[str] = []
    while True:
        try:
            line = line_input()
        except EOFError:
            break
        if not line.strip():
            break
        if line.strip().startswith("#"):
            continue
        lines.append(line.strip())
    return lines


def resolve_add_lines(items: list[str]) -> list[str]:
    """Normalize CLI tokens into one or more position lines.

    Supports:
      - single/multiple OCC symbols
      - one human line split across argv: INTC 261016 140 C
      - semicolon-separated in one argv: "INTC 261016 140 C; TSLA 261218 500 C"
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
        if ";" in token:
            parts = split_semi_separated(token)
            if not parts:
                raise ValueError("no positions to add")
            return parts
        try:
            return [parse_position_line(token).occ_symbol]
        except OccError as exc:
            raise ValueError(str(exc)) from exc

    # Multiple argv tokens: either many OCCs, or one spaced human line
    joined = " ".join(items)
    if ";" in joined:
        parts = split_semi_separated(joined)
        if parts:
            return parts

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
