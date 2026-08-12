"""Clipboard sync: pack / unpack account snapshot (no journal, no surfaces)."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from optionda.config import load_config, save_config
from optionda.credentials import load_alpaca, save_alpaca
from optionda.journal import sync_book
from optionda.models import Account, AppConfig
from optionda.store import AccountStore, StoreError

PREFIX = "oda1."
_OBFUSCATE_LABEL = b"optionda-pack-v1"


class SyncError(Exception):
    pass


@dataclass(frozen=True)
class PackResult:
    code: str
    sha256: str
    account: str
    n_positions: int
    has_creds: bool


@dataclass(frozen=True)
class Bundle:
    account: Account
    config: AppConfig
    key_id: str | None
    secret: str | None


def fingerprint(code: str) -> str:
    return hashlib.sha256(code.strip().encode("utf-8")).hexdigest()


def _b64encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64decode(text: str) -> bytes:
    pad = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + pad)


def _xor_bytes(data: bytes, key: bytes) -> bytes:
    return bytes(b ^ key[i % len(key)] for i, b in enumerate(data))


def _obfuscate(text: str, salt: bytes) -> str:
    key = hashlib.sha256(_OBFUSCATE_LABEL + salt).digest()
    return _b64encode(_xor_bytes(text.encode("utf-8"), key))


def _deobfuscate(token: str, salt: bytes) -> str:
    key = hashlib.sha256(_OBFUSCATE_LABEL + salt).digest()
    return _xor_bytes(_b64decode(token), key).decode("utf-8")


def _encode_creds(key_id: str, secret: str) -> dict[str, str]:
    salt = os.urandom(16)
    return {
        "salt": _b64encode(salt),
        "key_id": _obfuscate(key_id, salt),
        "secret": _obfuscate(secret, salt),
    }


def _decode_creds(blob: dict[str, Any] | None) -> tuple[str | None, str | None]:
    if not blob:
        return None, None
    try:
        salt = _b64decode(str(blob["salt"]))
        key_id = _deobfuscate(str(blob["key_id"]), salt).strip()
        secret = _deobfuscate(str(blob["secret"]), salt).strip()
    except (KeyError, ValueError, UnicodeDecodeError) as exc:
        raise SyncError(f"invalid credentials blob: {exc}") from exc
    if not key_id or not secret:
        return None, None
    return key_id, secret


def build_payload(
    account: Account,
    config: AppConfig,
    *,
    key_id: str | None,
    secret: str | None,
) -> dict[str, Any]:
    creds: dict[str, str] | None = None
    if key_id and secret:
        creds = _encode_creds(key_id, secret)
    # Keep packaged default pointing at this book.
    cfg = config.model_copy(update={"default_account": account.name})
    return {
        "v": 1,
        "account": json.loads(account.model_dump_json()),
        "config": cfg.model_dump(mode="json"),
        "creds": creds,
    }


def encode_payload(payload: dict[str, Any]) -> PackResult:
    raw = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    code = PREFIX + _b64encode(zlib.compress(raw, 9))
    account = str(payload.get("account", {}).get("name") or "?")
    positions = payload.get("account", {}).get("positions") or []
    return PackResult(
        code=code,
        sha256=fingerprint(code),
        account=account,
        n_positions=len(positions),
        has_creds=bool(payload.get("creds")),
    )


def decode_code(code: str) -> Bundle:
    text = code.strip().replace("\n", "").replace(" ", "")
    if not text.startswith(PREFIX):
        raise SyncError(f"unsupported sync code (expected {PREFIX}…)")
    try:
        raw = zlib.decompress(_b64decode(text[len(PREFIX) :]))
        payload = json.loads(raw.decode("utf-8"))
    except (ValueError, zlib.error, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SyncError(f"corrupt sync code: {exc}") from exc
    if int(payload.get("v", 0)) != 1:
        raise SyncError(f"unsupported pack version: {payload.get('v')!r}")
    try:
        account = Account.model_validate(payload["account"])
        config = AppConfig.model_validate(payload.get("config") or {})
    except Exception as exc:  # noqa: BLE001 — pydantic + key errors
        raise SyncError(f"invalid pack payload: {exc}") from exc
    key_id, secret = _decode_creds(payload.get("creds"))
    return Bundle(account=account, config=config, key_id=key_id, secret=secret)


def pack_account(
    store: AccountStore,
    *,
    home: Path | None = None,
) -> PackResult:
    """Pack the active account + config + obfuscated Alpaca keys."""
    try:
        account = store.require_current()
    except StoreError as exc:
        raise SyncError(str(exc)) from exc
    root = home if home is not None else store.home
    config = load_config(root)
    creds = load_alpaca(root)
    payload = build_payload(
        account,
        config,
        key_id=creds.key_id if creds else None,
        secret=creds.secret if creds else None,
    )
    return encode_payload(payload)


def apply_bundle(
    store: AccountStore,
    bundle: Bundle,
    *,
    home: Path | None = None,
    overwrite: bool = False,
) -> Account:
    """Replace the named account and restore config / credentials."""
    root = home if home is not None else store.home
    name = bundle.account.name
    if store.exists(name) and not overwrite:
        raise SyncError(
            f"account '{name}' already exists — re-run with --yes to overwrite"
        )
    store.save(bundle.account)
    sync_book(bundle.account, root)
    save_config(
        bundle.config.model_copy(update={"default_account": name}),
        root,
    )
    if bundle.key_id and bundle.secret:
        save_alpaca(bundle.key_id, bundle.secret, root)
    store.activate(name)
    return bundle.account


def unpack_code(
    store: AccountStore,
    code: str,
    *,
    home: Path | None = None,
    sha256: str | None = None,
    overwrite: bool = False,
) -> Bundle:
    cleaned = code.strip()
    if sha256:
        expected = sha256.strip().lower().removeprefix("sha256:")
        actual = fingerprint(cleaned)
        if actual != expected:
            raise SyncError("sha256 mismatch — code may be truncated or altered")
    bundle = decode_code(cleaned)
    apply_bundle(store, bundle, home=home, overwrite=overwrite)
    return bundle
