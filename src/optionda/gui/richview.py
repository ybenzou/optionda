"""Render Rich desk snapshots as HTML for the native window."""

from __future__ import annotations

import re
from io import StringIO

from rich.console import Console
from rich.segment import Segment
from rich.terminal_theme import TerminalTheme

# Campbell / Windows Terminal, matches the desk palette.
CAMPBELL = TerminalTheme(
    (12, 12, 12),
    (204, 204, 204),
    [
        (12, 12, 12),
        (231, 72, 86),
        (22, 198, 12),
        (249, 241, 165),
        (58, 150, 221),
        (180, 0, 158),
        (97, 214, 214),
        (204, 204, 204),
    ],
    [
        (118, 118, 118),
        (231, 72, 86),
        (22, 198, 12),
        (249, 241, 165),
        (58, 150, 221),
        (180, 0, 158),
        (97, 214, 214),
        (242, 242, 242),
    ],
)


DESK_FONT_PT = 12
DESK_PRE_STYLE = (
    "background:#0c0c0c;color:#cccccc;"
    "font-family:Cascadia Mono,Consolas,monospace;"
    f"font-size:{DESK_FONT_PT}pt;line-height:1.2;white-space:pre;"
    "margin:0;padding:0;"
)


def wrap_desk_html(body: str, *, wrap: bool = False) -> str:
    style = DESK_PRE_STYLE
    if wrap:
        style = style.replace("white-space:pre;", "white-space:pre-wrap;")
    return f'<pre style="{style}">{body}</pre>'


# Qt paints an <a> inside a colored span as a blue underline and drops the span color.
# The color has to sit inside the anchor.
_LINK_SPAN = re.compile(
    r'<span (?P<attrs>style="[^"]*")><a href="(?P<href>optionda:[^"]+)">(?P<body>.*?)</a></span>',
    re.DOTALL,
)


def _keep_link_colors(html: str) -> str:
    return _LINK_SPAN.sub(
        lambda match: (
            f'<a href="{match.group("href")}" style="text-decoration:none">'
            f'<span {match.group("attrs")}>{match.group("body")}</span></a>'
        ),
        html,
    )


def _span_ink(style) -> tuple[str, str, bool]:
    """Foreground, background, bold. Same Campbell colors as the HTML desk."""
    if style is None:
        return "#cccccc", "", False
    rule = style.get_html_style(CAMPBELL)
    fg = "#cccccc"
    bg = ""
    for part in rule.split(";"):
        piece = part.strip()
        if piece.startswith("color:"):
            fg = piece.split(":", 1)[1].strip()
        elif piece.startswith("background-color:"):
            bg = piece.split(":", 1)[1].strip()
    return fg, bg, bool(style.bold)


def renderable_lines(renderable, width: int) -> tuple:
    """Desk text as paint runs. The window draws these directly, with no HTML document."""
    console = Console(
        width=max(int(width), 40),
        force_terminal=True,
        color_system="truecolor",
        highlight=False,
        legacy_windows=False,
    )
    lines: list[tuple[str, tuple]] = []
    spans: list[tuple[str, str, str, bool]] = []
    occ = ""

    def _flush() -> None:
        nonlocal occ, spans
        lines.append((occ, tuple(spans)))
        spans = []
        occ = ""

    for segment in Segment.filter_control(console.render(renderable)):
        text = segment.text or ""
        if not text:
            continue
        fg, bg, bold = _span_ink(segment.style)
        link = ""
        if segment.style is not None and segment.style.link:
            link = segment.style.link
        parts = text.split("\n")
        for index, part in enumerate(parts):
            if part:
                spans.append((part, fg, bg, bold))
                if link.startswith("optionda:"):
                    occ = link.removeprefix("optionda:")
            if index < len(parts) - 1:
                _flush()
    if spans or not lines:
        _flush()
    return tuple(lines)


def renderable_html(renderable, width: int) -> str:
    console = Console(
        file=StringIO(),
        record=True,
        width=max(int(width), 40),
        force_terminal=True,
        color_system="truecolor",
        highlight=False,
        legacy_windows=False,
    )
    console.print(renderable)
    body = console.export_html(
        theme=CAMPBELL,
        inline_styles=True,
        code_format="{code}",
    )
    return wrap_desk_html(_keep_link_colors(body))
