"""Parse and run optionda commands inside the native window."""

from __future__ import annotations

import os
import shlex
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import dataclass
from io import StringIO
from pathlib import Path
from typing import Literal

import typer
from rich.console import Console

from optionda.paths import ensure_home
from optionda.store import AccountStore

Action = Literal["none", "clear", "exit", "stats", "term", "run", "export", "stop"]
PERIODS = {"1m", "3m", "6m", "all"}

WINDOW = """
window
  stats                  analysis view
  run                    live MODEL desk (stop to end)
  export                 one snapshot with progress
  stop                   stop a live run
  term                   back to this prompt
  clear                  clear the transcript
  exit                   close the window
  help                   this list + every CLI command
""".strip()


@dataclass(frozen=True)
class CommandResult:
    code: int
    text: str
    action: Action = "none"
    period: str | None = None


def parse_line(line: str) -> list[str]:
    text = (line or "").strip()
    if not text:
        return []
    try:
        args = shlex.split(text, posix=True)
    except ValueError:
        args = text.split()
    if args and args[0].lower() in {"optionda", "oda"}:
        args = args[1:]
    return args


def _stats_period(args: list[str]) -> str:
    return "all"


def active_account(home: Path | None = None) -> str:
    return AccountStore(ensure_home(home)).active_name() or ""


def sync_active_env(home: Path | None = None) -> str:
    name = active_account(home)
    if name:
        os.environ["OPTIONDA_ACTIVE"] = name
    else:
        os.environ.pop("OPTIONDA_ACTIVE", None)
    return name


def _with_console(work) -> CommandResult:
    import optionda.cli as cli

    buf = StringIO()
    captured = Console(
        file=buf,
        force_terminal=False,
        color_system=None,
        width=100,
        highlight=False,
        legacy_windows=False,
    )
    stdout = StringIO()
    stderr = StringIO()
    old = cli.console
    cli.console = captured
    code = 0
    try:
        with redirect_stdout(stdout), redirect_stderr(stderr):
            try:
                work()
            except typer.Exit as exc:
                raw = exc.exit_code
                code = 0 if raw is None else int(raw)
            except SystemExit as exc:
                raw = exc.code
                if raw is None:
                    code = 0
                elif isinstance(raw, int):
                    code = raw
                else:
                    code = 1
    finally:
        cli.console = old
    text = "".join((buf.getvalue(), stdout.getvalue(), stderr.getvalue())).rstrip()
    return CommandResult(code, text)


def invoke_cli(args: list[str]) -> CommandResult:
    import optionda.cli as cli

    return _with_console(lambda: cli.app(args, standalone_mode=False))


def dispatch(line: str, *, home: Path | None = None) -> CommandResult:
    if home is not None:
        os.environ["OPTIONDA_HOME"] = str(home)
    args = parse_line(line)
    if not args:
        return CommandResult(0, "")
    cmd = args[0].lower()
    if cmd in {"exit", "quit"}:
        return CommandResult(0, "", action="exit")
    if cmd in {"clear", "cls"}:
        return CommandResult(0, "", action="clear")
    if cmd in {"term", "terminal"}:
        return CommandResult(0, "", action="term")
    if cmd == "stop":
        return CommandResult(0, "", action="stop")
    if cmd == "run":
        return CommandResult(0, "", action="run")
    if cmd == "export":
        return CommandResult(0, "", action="export")
    if cmd in {"help", "?"}:
        cli_help = invoke_cli(["--help"])
        text = "\n\n".join(part for part in (WINDOW, cli_help.text) if part)
        return CommandResult(0, text)
    if cmd in {"stats", "desk"}:
        return CommandResult(0, "", action="stats", period=_stats_period(args))
    return invoke_cli(args)
