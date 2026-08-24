"""Idle slash mark for the first term page — long-call payoff + optionda."""

from __future__ import annotations

import textwrap

# IV smile / long-straddle wings — two peaks, not a brim.
MARK = textwrap.dedent(
    """\
      ///////                                          ///////
       ////////                                      ////////
        /////////                                  /////////
         //////////                              //////////
          ///////////                          ///////////
           ////////////                      ////////////
            /////////////                  /////////////
             //////////////              //////////////
              ////////////////////////////////////////
    """
).rstrip("\n")

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
