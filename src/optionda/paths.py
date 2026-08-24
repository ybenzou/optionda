from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

HomeMode = Literal["override", "env", "user"]


@dataclass(frozen=True)
class HomeInfo:
    path: Path
    mode: HomeMode
    """override = OPTIONDA_HOME; env = conda/venv; user = ~/.optionda"""
    env_kind: str | None = None
    """'conda' | 'venv' when mode == 'env'."""
    env_name: str | None = None


def user_home() -> Path:
    return Path.home() / ".optionda"


def _from_prefix(prefix: Path, *, kind_hint: str | None = None) -> tuple[Path, str, str]:
    if kind_hint == "conda" or (prefix / "conda-meta").is_dir():
        name = os.environ.get("CONDA_DEFAULT_ENV") or prefix.name
        return prefix, "conda", name
    return prefix, "venv", prefix.name


def _env_prefix() -> tuple[Path, str, str] | None:
    """Return (prefix, kind, name) for the active / installing environment.

    Prefer VIRTUAL_ENV over CONDA_PREFIX (conda *base* often stays exported).
    Fall back to ``sys.prefix`` when the interpreter itself is isolated, so
    calling ``.venv/Scripts/optionda`` without ``activate`` still isolates.
    """
    venv = os.environ.get("VIRTUAL_ENV")
    if venv:
        return _from_prefix(Path(venv), kind_hint="venv")

    conda = os.environ.get("CONDA_PREFIX")
    conda_path = Path(conda) if conda else None

    # Isolated interpreter (venv / conda env), even if activate vars are missing.
    if sys.prefix != getattr(sys, "base_prefix", sys.prefix):
        prefix = Path(sys.prefix)
        # Ignore stale CONDA_PREFIX that points at a different env (e.g. base).
        return _from_prefix(prefix)

    if conda_path is not None:
        return _from_prefix(conda_path, kind_hint="conda")

    return None


def resolve_home_info(explicit: Path | str | None = None) -> HomeInfo:
    """Pick the data library: OPTIONDA_HOME > conda/venv > ~/.optionda."""
    if explicit is not None:
        return HomeInfo(path=Path(explicit), mode="override")

    override = os.environ.get("OPTIONDA_HOME")
    if override:
        return HomeInfo(path=Path(override), mode="override")

    active = _env_prefix()
    if active is not None:
        prefix, kind, name = active
        return HomeInfo(
            path=prefix / "share" / "optionda",
            mode="env",
            env_kind=kind,
            env_name=name,
        )

    return HomeInfo(path=user_home(), mode="user")


def resolve_home(explicit: Path | str | None = None) -> Path:
    return resolve_home_info(explicit).path


def default_home() -> Path:
    """Resolved data home for the current process (may be env-local)."""
    return resolve_home()


def ensure_home(root: Path | str | None = None) -> Path:
    home = resolve_home(root)
    (home / "accounts").mkdir(parents=True, exist_ok=True)
    (home / "books").mkdir(parents=True, exist_ok=True)
    (home / "logs").mkdir(parents=True, exist_ok=True)
    (home / "surfaces").mkdir(parents=True, exist_ok=True)
    (home / "session_refs").mkdir(parents=True, exist_ok=True)
    (home / "close_mids").mkdir(parents=True, exist_ok=True)
    (home / "closes").mkdir(parents=True, exist_ok=True)
    (home / "marks").mkdir(parents=True, exist_ok=True)
    (home / "agent").mkdir(parents=True, exist_ok=True)
    (home / "mail").mkdir(parents=True, exist_ok=True)
    return home
