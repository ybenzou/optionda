"""Render Rich desk snapshots as HTML for the native window."""

from __future__ import annotations

from io import StringIO

from rich.console import Console
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


def wrap_desk_html(body: str) -> str:
    return f'<pre style="{DESK_PRE_STYLE}">{body}</pre>'


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
    return wrap_desk_html(body)
