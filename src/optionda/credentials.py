from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path

from optionda.paths import ensure_home


@dataclass(frozen=True)
class AlpacaCredentials:
    key_id: str
    secret: str


@dataclass(frozen=True)
class SmtpCredentials:
    user: str
    password: str
    host: str = "smtp.gmail.com"
    port: int = 587


def _credentials_path(home: Path) -> Path:
    return home / "credentials.toml"


def _escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _load_raw(home: Path) -> dict:
    path = _credentials_path(home)
    if not path.exists():
        return {}
    with path.open("rb") as fh:
        return tomllib.load(fh)


def _dump_all(raw: dict) -> str:
    parts: list[str] = []
    alpaca = raw.get("alpaca") or {}
    key_id = (alpaca.get("key_id") or "").strip()
    secret = (alpaca.get("secret") or "").strip()
    if key_id and secret:
        parts.append(
            f'[alpaca]\nkey_id = "{_escape(key_id)}"\nsecret = "{_escape(secret)}"\n'
        )
    smtp = raw.get("smtp") or {}
    user = (smtp.get("user") or "").strip()
    password = (smtp.get("password") or "").strip()
    if user and password:
        host = (smtp.get("host") or "smtp.gmail.com").strip()
        port = int(smtp.get("port") or 587)
        parts.append(
            "[smtp]\n"
            f'user = "{_escape(user)}"\n'
            f'password = "{_escape(password)}"\n'
            f'host = "{_escape(host)}"\n'
            f"port = {port}\n"
        )
    return "\n".join(parts)


def _write_raw(home: Path, raw: dict) -> Path:
    path = _credentials_path(home)
    path.write_text(_dump_all(raw), encoding="utf-8")
    _chmod_private(path)
    return path


def _chmod_private(path: Path) -> None:
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def load_alpaca(home: Path | None = None) -> AlpacaCredentials | None:
    root = ensure_home(home)
    section = _load_raw(root).get("alpaca") or {}
    key_id = (section.get("key_id") or "").strip()
    secret = (section.get("secret") or "").strip()
    if not key_id or not secret:
        return None
    return AlpacaCredentials(key_id=key_id, secret=secret)


def save_alpaca(key_id: str, secret: str, home: Path | None = None) -> Path:
    root = ensure_home(home)
    raw = _load_raw(root)
    creds = AlpacaCredentials(key_id=key_id.strip(), secret=secret.strip())
    if not creds.key_id or not creds.secret:
        raise ValueError("key_id and secret are required")
    raw["alpaca"] = {"key_id": creds.key_id, "secret": creds.secret}
    return _write_raw(root, raw)


def clear_alpaca(home: Path | None = None) -> bool:
    root = ensure_home(home)
    raw = _load_raw(root)
    if "alpaca" not in raw:
        return False
    raw.pop("alpaca", None)
    _write_raw(root, raw)
    return True


def has_alpaca(home: Path | None = None) -> bool:
    return load_alpaca(home) is not None


def load_smtp(home: Path | None = None) -> SmtpCredentials | None:
    root = ensure_home(home)
    section = _load_raw(root).get("smtp") or {}
    user = (section.get("user") or "").strip()
    password = (section.get("password") or "").strip()
    if not user or not password:
        return None
    host = (section.get("host") or "smtp.gmail.com").strip() or "smtp.gmail.com"
    port = int(section.get("port") or 587)
    return SmtpCredentials(user=user, password=password, host=host, port=port)


def save_smtp(
    user: str,
    password: str,
    home: Path | None = None,
    *,
    host: str = "smtp.gmail.com",
    port: int = 587,
) -> Path:
    root = ensure_home(home)
    raw = _load_raw(root)
    raw["smtp"] = {
        "user": user.strip(),
        "password": password.strip(),
        "host": host.strip() or "smtp.gmail.com",
        "port": int(port),
    }
    if not raw["smtp"]["user"] or not raw["smtp"]["password"]:
        raise ValueError("smtp user and password are required")
    return _write_raw(root, raw)


def clear_smtp(home: Path | None = None) -> bool:
    root = ensure_home(home)
    raw = _load_raw(root)
    if "smtp" not in raw:
        return False
    raw.pop("smtp", None)
    _write_raw(root, raw)
    return True
