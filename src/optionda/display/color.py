"""One color per option code.

The RGB is a function of the OCC alone. The desk list and the chart call
this with different sets of contracts, and a contract must not change color
because another name is present or absent. The pale cost line (#f9f1a5) is
not produced here.
"""

from __future__ import annotations

import colorsys
import zlib
from collections.abc import Iterable


def contract_color(occ: str) -> str:
    """Fixed ``#rrggbb`` for this OCC."""
    key = "".join(occ.split()).upper().encode("ascii", "replace")
    digest = zlib.crc32(key)
    hue = digest % 360
    # The cost guide sits near hue 54. Step clear of that band.
    if 42 <= hue <= 72:
        hue = (hue + 36) % 360
    red, green, blue = colorsys.hls_to_rgb(hue / 360.0, 0.62, 0.78)
    return f"#{round(red * 255):02x}{round(green * 255):02x}{round(blue * 255):02x}"


def assign_colors(occs: Iterable[str]) -> dict[str, str]:
    """Map each OCC to its own color. The other names do not move it."""
    return {occ: contract_color(occ) for occ in occs}
