from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path

from optionda.paths import default_home, ensure_home


@dataclass(frozen=True)
class AlpacaCredentials:
    key_id: str
    secret: str


def _credentials_path(home: Path) -> Path:
    return home / "credentials.toml"


def _dump_credentials(alpaca: AlpacaCredentials | None) -> str:
    if alpaca is None:
        return ""
    key = alpaca.key_id.replace("\\", "\\\\").replace('"', '\\"')
    secret = alpaca.secret.replace("\\", "\\\\").replace('"', '\\"')
    return f'[alpaca]\nkey_id = "{key}"\nsecret = "{secret}"\n'


def _chmod_private(path: Path) -> None:
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def load_alpaca(home: Path | None = None) -> AlpacaCredentials | None:
    root = ensure_home(home or default_home())
    path = _credentials_path(root)
    if not path.exists():
        return None
    with path.open("rb") as fh:
        raw = tomllib.load(fh)
    section = raw.get("alpaca") or {}
    key_id = (section.get("key_id") or "").strip()
    secret = (section.get("secret") or "").strip()
    if not key_id or not secret:
        return None
    return AlpacaCredentials(key_id=key_id, secret=secret)


def save_alpaca(key_id: str, secret: str, home: Path | None = None) -> Path:
    root = ensure_home(home or default_home())
    path = _credentials_path(root)
    creds = AlpacaCredentials(key_id=key_id.strip(), secret=secret.strip())
    if not creds.key_id or not creds.secret:
        raise ValueError("key_id and secret are required")
    path.write_text(_dump_credentials(creds), encoding="utf-8")
    _chmod_private(path)
    return path


def clear_alpaca(home: Path | None = None) -> bool:
    root = ensure_home(home or default_home())
    path = _credentials_path(root)
    if not path.exists():
        return False
    with path.open("rb") as fh:
        raw = tomllib.load(fh)
    if "alpaca" not in raw:
        return False
    # Drop alpaca section; keep file empty if nothing left
    path.write_text("", encoding="utf-8")
    _chmod_private(path)
    return True


def has_alpaca(home: Path | None = None) -> bool:
    return load_alpaca(home) is not None
