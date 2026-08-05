from __future__ import annotations

import json
import os
import re
from pathlib import Path

from optionda.config import load_config, save_config
from optionda.models import Account, Position
from optionda.paths import default_home, ensure_home

ACTIVE_ENV = "OPTIONDA_ACTIVE"

_ACCOUNT_RE = re.compile(r"^[A-Za-z0-9_\-]+$")


class StoreError(Exception):
    pass


class AccountStore:
    def __init__(self, home: Path | None = None) -> None:
        self.home = ensure_home(home or default_home())
        self.accounts_dir = self.home / "accounts"

    def _path(self, name: str) -> Path:
        if not _ACCOUNT_RE.match(name):
            raise StoreError("account name must be alphanumeric, _ or -")
        return self.accounts_dir / f"{name}.json"

    def list_accounts(self) -> list[str]:
        names = sorted(p.stem for p in self.accounts_dir.glob("*.json"))
        return names

    def exists(self, name: str) -> bool:
        return self._path(name).exists()

    def create(self, name: str) -> Account:
        path = self._path(name)
        if path.exists():
            raise StoreError(f"account already exists: {name}")
        account = Account(name=name)
        self.save(account)
        return account

    def load(self, name: str) -> Account:
        path = self._path(name)
        if not path.exists():
            raise StoreError(f"account not found: {name}")
        data = json.loads(path.read_text(encoding="utf-8"))
        return Account.model_validate(data)

    def save(self, account: Account) -> None:
        path = self._path(account.name)
        path.write_text(
            account.model_dump_json(indent=2) + "\n",
            encoding="utf-8",
        )

    def use(self, name: str) -> None:
        """Persist last-used account name (does not session-activate)."""
        if not self.exists(name):
            raise StoreError(f"account not found: {name}")
        cfg = load_config(self.home)
        save_config(cfg.model_copy(update={"default_account": name}), self.home)

    def active_name(self) -> str | None:
        """Session-activated account (conda-style); from OPTIONDA_ACTIVE."""
        value = (os.environ.get(ACTIVE_ENV) or "").strip()
        return value or None

    def current_name(self) -> str | None:
        """Prompt/session current: active env only (not disk default)."""
        return self.active_name()

    def require_current(self, name: str | None = None) -> Account:
        target = name or self.active_name()
        if not target:
            raise StoreError(
                "no account activated; run: optionda activate <name>"
            )
        return self.load(target)

    def add_position(self, account_name: str | None, position: Position) -> Account:
        account = self.require_current(account_name)
        for existing in account.positions:
            if existing.occ_symbol == position.occ_symbol and existing.side == position.side:
                raise StoreError(
                    f"position already exists: {position.occ_symbol} ({position.side})"
                )
        account.positions.append(position)
        self.save(account)
        return account

    def delete_position(self, account_name: str | None, key: str) -> Account:
        account = self.require_current(account_name)
        key_u = key.strip().upper()
        before = len(account.positions)
        account.positions = [
            p
            for p in account.positions
            if p.id != key and p.occ_symbol.upper() != key_u
        ]
        if len(account.positions) == before:
            raise StoreError(f"position not found: {key}")
        self.save(account)
        return account

    def update_positions(self, account: Account) -> None:
        self.save(account)
