"""Clipboard sync: pack / unpack account + slim journal (no surfaces)."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from optionda.analytics import read_events
from optionda.config import load_config, save_config
from optionda.credentials import load_alpaca, save_alpaca
from optionda.journal import log_path, replace_log, sync_book
from optionda.models import Account, AppConfig
from optionda.store import AccountStore, StoreError

PACK_VERSION = 2
_SUPPORTED_VERSIONS = {1, 2}
_MUTATION_EVENTS = {"add", "merge", "sell", "delete", "refresh_iv"}
_ADD_KEYS = (
    "ts",
    "account",
    "event",
    "id",
    "occ",
    "side",
    "qty_added",
    "cost_added",
    "qty",
    "cost",
    "qty_before",
    "cost_before",
    "iv",
    "iv_source",
    "dte_at_entry",
)
_SELL_KEYS = (
    "ts",
    "account",
    "event",
    "id",
    "occ",
    "side",
    "qty_sold",
    "exit",
    "avg_cost",
    "multiplier",
    "realized",
    "qty_remaining",
    "closed",
    "dte_at_exit",
    "hold_days",
)
_DELETE_ROW_KEYS = ("id", "occ", "qty", "side")

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
    n_events: int = 0


@dataclass(frozen=True)
class Bundle:
    account: Account
    config: AppConfig
    key_id: str | None
    secret: str | None
    journal: list[dict[str, Any]] | None = None


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


def _copy_keys(event: dict[str, Any], keys: tuple[str, ...]) -> dict[str, Any]:
    return {key: event[key] for key in keys if key in event and event[key] is not None}


def _ivs_from_event(event: dict[str, Any]) -> dict[str, float]:
    raw = event.get("ivs")
    if isinstance(raw, dict):
        ivs: dict[str, float] = {}
        for position_id, value in raw.items():
            try:
                iv = float(value)
            except (TypeError, ValueError):
                continue
            if iv > 0:
                ivs[str(position_id)] = iv
        return ivs
    ivs = {}
    book = event.get("book") if isinstance(event.get("book"), list) else []
    for row in book:
        if not isinstance(row, dict):
            continue
        position_id = str(row.get("id") or "")
        try:
            iv = float(row.get("iv"))
        except (TypeError, ValueError):
            continue
        if position_id and iv > 0:
            ivs[position_id] = iv
    return ivs


def slim_journal(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep mutation commands + IV changes; drop book/export snapshots."""
    slim: list[dict[str, Any]] = []
    for event in events:
        kind = event.get("event")
        if kind not in _MUTATION_EVENTS:
            continue
        if kind == "refresh_iv":
            ivs = _ivs_from_event(event)
            if not ivs:
                continue
            row: dict[str, Any] = {"event": "refresh_iv", "ivs": ivs}
            if event.get("ts") is not None:
                row["ts"] = event["ts"]
            if event.get("account") is not None:
                row["account"] = event["account"]
            slim.append(row)
            continue
        if kind == "delete":
            removed: list[dict[str, Any]] = []
            for item in event.get("removed") or []:
                if isinstance(item, dict):
                    removed.append(_copy_keys(item, _DELETE_ROW_KEYS))
            row = {"event": "delete", "removed": removed}
            if event.get("ts") is not None:
                row["ts"] = event["ts"]
            if event.get("account") is not None:
                row["account"] = event["account"]
            slim.append(row)
            continue
        keys = _SELL_KEYS if kind == "sell" else _ADD_KEYS
        slim.append(_copy_keys(event, keys))
    return slim


def _parse_journal(payload: dict[str, Any], version: int) -> list[dict[str, Any]] | None:
    if version < 2:
        return None
    raw = payload.get("journal")
    if raw is None:
        return []
    if not isinstance(raw, list) or any(not isinstance(item, dict) for item in raw):
        raise SyncError("invalid pack journal")
    return raw


def build_payload(
    account: Account,
    config: AppConfig,
    *,
    key_id: str | None,
    secret: str | None,
    journal: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    creds: dict[str, str] | None = None
    if key_id and secret:
        creds = _encode_creds(key_id, secret)
    # Keep packaged default pointing at this book.
    cfg = config.model_copy(update={"default_account": account.name})
    return {
        "v": PACK_VERSION,
        "account": json.loads(account.model_dump_json()),
        "config": cfg.model_dump(mode="json"),
        "creds": creds,
        "journal": journal or [],
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
        n_events=len(payload.get("journal") or []),
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
    try:
        version = int(payload.get("v", 0))
    except (TypeError, ValueError) as exc:
        raise SyncError(f"unsupported pack version: {payload.get('v')!r}") from exc
    if version not in _SUPPORTED_VERSIONS:
        raise SyncError(f"unsupported pack version: {payload.get('v')!r}")
    try:
        account = Account.model_validate(payload["account"])
        config = AppConfig.model_validate(payload.get("config") or {})
        journal = _parse_journal(payload, version)
    except SyncError:
        raise
    except Exception as exc:  # noqa: BLE001 — pydantic + key errors
        raise SyncError(f"invalid pack payload: {exc}") from exc
    key_id, secret = _decode_creds(payload.get("creds"))
    return Bundle(
        account=account,
        config=config,
        key_id=key_id,
        secret=secret,
        journal=journal,
    )


def pack_account(
    store: AccountStore,
    *,
    home: Path | None = None,
) -> PackResult:
    """Pack the active account + slim journal + obfuscated Alpaca keys."""
    try:
        account = store.require_current()
    except StoreError as exc:
        raise SyncError(str(exc)) from exc
    root = home if home is not None else store.home
    config = load_config(root)
    creds = load_alpaca(root)
    journal = slim_journal(read_events(log_path(account.name, root)))
    payload = build_payload(
        account,
        config,
        key_id=creds.key_id if creds else None,
        secret=creds.secret if creds else None,
        journal=journal,
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
    if bundle.journal is not None:
        replace_log(name, bundle.journal, home=root)
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


def default_oda_path(account: str, directory: Path | None = None) -> Path:
    safe = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in account) or "account"
    root = directory if directory is not None else Path.cwd()
    return Path(root) / f"{safe}.oda"


def write_oda(path: Path, packed: PackResult) -> Path:
    dest = Path(path).expanduser()
    if dest.suffix.lower() != ".oda":
        dest = dest.with_suffix(".oda")
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(packed.code + "\n", encoding="ascii")
    return dest


def _extract_code(block: str) -> str:
    raw = ""
    for line in block.splitlines():
        line = line.strip()
        if line.startswith(PREFIX):
            raw = line
            break
    if not raw and block.strip().startswith(PREFIX):
        raw = block.strip().splitlines()[0].strip()
    if not raw:
        raise SyncError("no oda1. sync code found")
    return raw.replace(" ", "")


def read_pack_text(source: str | Path) -> str:
    text = str(source).strip()
    path = Path(text)
    if path.exists() and path.is_file():
        return _extract_code(path.read_text(encoding="utf-8"))
    if text.startswith(PREFIX):
        return text.replace("\n", "").replace(" ", "")
    raise SyncError(f"not a pack file or oda1. code: {text}")


def unpack_source(
    store: AccountStore,
    source: str | Path,
    *,
    home: Path | None = None,
    sha256: str | None = None,
    overwrite: bool = False,
) -> Bundle:
    return unpack_code(
        store,
        read_pack_text(source),
        home=home,
        sha256=sha256,
        overwrite=overwrite,
    )
