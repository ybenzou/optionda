from __future__ import annotations

from pathlib import Path


def default_home() -> Path:
    return Path.home() / ".optionda"


def ensure_home(root: Path | None = None) -> Path:
    home = root or default_home()
    (home / "accounts").mkdir(parents=True, exist_ok=True)
    return home
