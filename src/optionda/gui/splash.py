"""Idle slash mark for the first term page — rising path + optionda."""

from __future__ import annotations

# Oscillating equity curve, same width as the wordmark lockup.
MARK = "\n".join(
    line.ljust(71)
    for line in (
        "                                                                     //",
        "                                                                 ////",
        "                                                             ////",
        "                                                         ////",
        "                                     /\\\\\\            ////",
        "                                   //    \\\\\\\\    ////",
        "                                ///          \\\\//",
        "                             ///",
        "            //\\\\\\          //",
        "        ////     \\\\\\\\   ///",
        "   /////             \\\\//",
        "///",
    )
)

def _join_letters(*glyphs: str, gap: int = 2) -> str:
    rows = [glyph.splitlines() for glyph in glyphs]
    height = max(len(item) for item in rows)
    blocks: list[list[str]] = []
    for item in rows:
        width = max(len(line) for line in item)
        block = [line.ljust(width) for line in item]
        block.extend([" " * width] * (height - len(block)))
        blocks.append(block)
    return "\n".join((" " * gap).join(col[index] for col in blocks) for index in range(height))


# D: flat left stem. O: inset top/bottom, open both sides.
WORD = _join_letters(
    "\n".join(
        (
            "  ////  ",
            " //  // ",
            "//    //",
            "//    //",
            " //  // ",
            "  ////  ",
        )
    ),
    "\n".join(
        (
            "///// ",
            "//  //",
            "///// ",
            "//    ",
            "//    ",
            "//    ",
        )
    ),
    "\n".join(
        (
            "//////",
            "  //  ",
            "  //  ",
            "  //  ",
            "  //  ",
            "  //  ",
        )
    ),
    "\n".join(
        (
            "////",
            " // ",
            " // ",
            " // ",
            " // ",
            "////",
        )
    ),
    "\n".join(
        (
            "  ////  ",
            " //  // ",
            "//    //",
            "//    //",
            " //  // ",
            "  ////  ",
        )
    ),
    "\n".join(
        (
            "//    //",
            "///   //",
            "// // //",
            "//  ////",
            "//   ///",
            "//    //",
        )
    ),
    "\n".join(
        (
            "//////",
            "//    //",
            "//     //",
            "//     //",
            "//    //",
            "//////",
        )
    ),
    "\n".join(
        (
            "  ////  ",
            " //  // ",
            "//    //",
            "////////",
            "//    //",
            "//    //",
        )
    ),
)


def splash_plain() -> str:
    return f"{MARK}\n\n{WORD}"


def mark_html(text: str | None = None) -> str:
    """Color rising strokes green and falling strokes red."""
    from optionda.gui.theme import GREEN, RED

    source = MARK if text is None else text
    lines: list[str] = []
    for line in source.splitlines():
        parts: list[str] = []
        run = ""
        color = ""
        for char in line:
            nxt = GREEN if char == "/" else RED if char == "\\" else ""
            if nxt and nxt == color:
                run += char
                continue
            if run:
                parts.append(f'<span style="color:{color}">{run}</span>')
                run = ""
            if nxt:
                color = nxt
                run = char
                continue
            color = ""
            parts.append("&nbsp;" if char == " " else char)
        if run:
            parts.append(f'<span style="color:{color}">{run}</span>')
        lines.append("".join(parts))
    return '<pre style="margin:0">' + "<br>".join(lines) + "</pre>"
