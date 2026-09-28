"""Stable series colors.

The preferred slot is a function of the OCC, so a contract does not change
color when the desk reorders. Within one book, a taken slot is skipped so
two open contracts are not painted the same.
"""

from __future__ import annotations

import zlib
from collections.abc import Iterable

# Saturated, spaced hues that stay readable on the black desk.
# The pale cost line (#f9f1a5) is intentionally not in this list.
SERIES_COLORS = (
    "#ff5d5d",
    "#ff8a3d",
    "#f0c42a",
    "#a6e22e",
    "#2fcb5a",
    "#1ed6a8",
    "#2ec8e6",
    "#4c97ff",
    "#9a8cff",
    "#e06bff",
    "#ff4ec8",
    "#ff7a9a",
)


def color_slot(occ: str) -> int:
    digest = zlib.crc32(occ.encode("ascii", "replace"))
    return digest % len(SERIES_COLORS)


def contract_color(occ: str) -> str:
    return SERIES_COLORS[color_slot(occ)]


def assign_colors(occs: Iterable[str]) -> dict[str, str]:
    """One distinct color per OCC. Desk order does not matter."""
    unique = sorted(set(occs))
    used: set[int] = set()
    assigned: dict[str, str] = {}
    span = len(SERIES_COLORS)
    for occ in unique:
        slot = color_slot(occ)
        for _ in range(span):
            if slot not in used:
                break
            slot = (slot + 1) % span
        used.add(slot)
        assigned[occ] = SERIES_COLORS[slot]
    return assigned
