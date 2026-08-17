"""Internal entry used by the detached GUI process."""

from __future__ import annotations

import argparse
from pathlib import Path

from optionda.gui.launch import run_foreground


def main() -> None:
    parser = argparse.ArgumentParser(prog="optionda.gui")
    parser.add_argument("--account", default="")
    parser.add_argument("--home", required=True)
    parser.add_argument("--period", default="all")
    parser.add_argument("--view", default="term", choices=("term", "stats", "desk"))
    args = parser.parse_args()
    run_foreground(
        args.account,
        Path(args.home),
        period=args.period,
        initial_view=args.view,
    )


if __name__ == "__main__":
    main()
