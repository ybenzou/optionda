from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path

from typing import Any

from optionda.config import load_config, save_config
from optionda.journal import (
    append_add_event,
    append_delete_event,
    append_refresh_iv_event,
    append_sell_event,
    log_path,
    sync_book,
)
from optionda.models import Account, Position
from optionda.paths import ensure_home

ACTIVE_ENV = "OPTIONDA_ACTIVE"
ACTIVE_FILE = "active"

_ACCOUNT_RE = re.compile(r"^[A-Za-z0-9_\-]+$")


class StoreError(Exception):
    pass


@dataclass
class AddOutcome:
    account: Account
    position: Position
    merged: bool
    previous_qty: float
    previous_entry: float | None = None
    qty_added: float = 0.0
    cost_added: float = 0.0


@dataclass
class DeleteOutcome:
    account: Account
    removed: list[Position]


@dataclass
class SellOutcome:
    account: Account
    position: Position | None
    qty_sold: float
    exit_premium: float
    avg_cost: float
    realized: float
    closed: bool
    side: str
    occ_symbol: str
    multiplier: int = 100


def weighted_avg_cost(
    old_qty: float,
    old_cost: float,
    new_qty: float,
    new_cost: float,
) -> float:
    total_qty = old_qty + new_qty
    if total_qty <= 0:
        raise StoreError("qty must be > 0 when merging cost")
    return (old_qty * old_cost + new_qty * new_cost) / total_qty


class AccountStore:
    def __init__(self, home: Path | None = None) -> None:
        self.home = ensure_home(home)
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
        sync_book(account, self.home)
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
        """Persist last-used account name (alias of activate)."""
        self.activate(name)

    def _active_path(self) -> Path:
        return self.home / ACTIVE_FILE

    def activate(self, name: str) -> None:
        """Mark account active for this data home (no shell hook required)."""
        if not self.exists(name):
            raise StoreError(f"account not found: {name}")
        self._active_path().write_text(name.strip() + "\n", encoding="utf-8")
        cfg = load_config(self.home)
        save_config(cfg.model_copy(update={"default_account": name.strip()}), self.home)

    def deactivate(self) -> bool:
        """Clear active account. Returns True if something was cleared."""
        path = self._active_path()
        existed = path.exists()
        if existed:
            path.unlink()
        cfg = load_config(self.home)
        if cfg.default_account is not None:
            save_config(cfg.model_copy(update={"default_account": None}), self.home)
            return True
        return existed

    def active_name(self) -> str | None:
        """Active account: OPTIONDA_ACTIVE override, else `<data>/active` file."""
        value = (os.environ.get(ACTIVE_ENV) or "").strip()
        if value:
            return value
        path = self._active_path()
        if path.exists():
            name = path.read_text(encoding="utf-8").strip()
            return name or None
        # Migrate older installs that only stored default_account.
        cfg = load_config(self.home)
        if cfg.default_account:
            return cfg.default_account
        return None

    def current_name(self) -> str | None:
        return self.active_name()

    def require_current(self, name: str | None = None) -> Account:
        """Load the active account only.

        An explicit name is allowed only when it matches the active account
        (cannot peek at other books via --account).
        """
        active = self.active_name()
        if not active:
            raise StoreError(
                "no account activated; run: optionda activate <name>"
            )
        if name is not None and name.strip() and name.strip() != active:
            raise StoreError(
                f"account '{name}' is not active (active={active}); "
                f"run: optionda activate {name}"
            )
        return self.load(active)

    def add_position(self, account_name: str | None, position: Position) -> AddOutcome:
        """Add a position, or merge qty into the same OCC+side if it already exists.

        Cost (entry_premium) is required. On merge, qty sums and cost becomes the
        quantity-weighted average: (q1*c1 + q2*c2) / (q1+q2).
        """
        if position.entry_premium is None or position.entry_premium <= 0:
            raise StoreError(
                "cost required — use '@ 5.20' on the line or pass --entry 5.20"
            )
        account = self.require_current(account_name)
        for index, existing in enumerate(account.positions):
            if (
                existing.occ_symbol == position.occ_symbol
                and existing.side == position.side
            ):
                previous = existing.qty
                previous_entry = existing.entry_premium
                if previous_entry is None or previous_entry <= 0:
                    avg_cost = position.entry_premium
                else:
                    avg_cost = weighted_avg_cost(
                        existing.qty,
                        previous_entry,
                        position.qty,
                        position.entry_premium,
                    )
                merged = existing.model_copy(
                    update={
                        "qty": existing.qty + position.qty,
                        "iv_frozen": position.iv_frozen,
                        "iv_as_of": position.iv_as_of,
                        "iv_source": position.iv_source or existing.iv_source,
                        "entry_premium": avg_cost,
                    }
                )
                account.positions[index] = merged
                self.save(account)
                sync_book(account, self.home)
                outcome = AddOutcome(
                    account=account,
                    position=merged,
                    merged=True,
                    previous_qty=previous,
                    previous_entry=previous_entry,
                    qty_added=position.qty,
                    cost_added=position.entry_premium,
                )
                append_add_event(
                    account,
                    position_after=merged,
                    qty_added=position.qty,
                    cost_added=position.entry_premium,
                    merged=True,
                    previous_qty=previous,
                    previous_entry=previous_entry,
                    home=self.home,
                )
                return outcome
        account.positions.append(position)
        self.save(account)
        sync_book(account, self.home)
        append_add_event(
            account,
            position_after=position,
            qty_added=position.qty,
            cost_added=position.entry_premium,
            merged=False,
            previous_qty=0.0,
            previous_entry=None,
            home=self.home,
        )
        return AddOutcome(
            account=account,
            position=position,
            merged=False,
            previous_qty=0.0,
            previous_entry=None,
            qty_added=position.qty,
            cost_added=position.entry_premium,
        )

    def delete_position(self, account_name: str | None, key: str) -> DeleteOutcome:
        account = self.require_current(account_name)
        key_u = key.strip().upper()
        removed = [
            p
            for p in account.positions
            if p.id == key or p.occ_symbol.upper() == key_u
        ]
        if not removed:
            raise StoreError(f"position not found: {key}")
        account.positions = [
            p
            for p in account.positions
            if p.id != key and p.occ_symbol.upper() != key_u
        ]
        self.save(account)
        sync_book(account, self.home)
        append_delete_event(account, removed, home=self.home)
        return DeleteOutcome(account=account, removed=removed)

    def sell_position(
        self,
        account_name: str | None,
        key: str,
        *,
        qty: float,
        exit_premium: float,
    ) -> SellOutcome:
        """Close qty at an exit premium; records realized cash PnL in the journal.

        Long close:  (exit - avg_cost) * multiplier * qty
        Short cover: (avg_cost - exit) * multiplier * qty
        """
        if qty <= 0:
            raise StoreError("sell qty must be > 0")
        if exit_premium <= 0:
            raise StoreError("exit premium must be > 0 — use '@ 8.50'")
        account = self.require_current(account_name)
        key_u = key.strip().upper()
        index = next(
            (
                i
                for i, pos in enumerate(account.positions)
                if pos.id == key or pos.occ_symbol.upper() == key_u
            ),
            None,
        )
        if index is None:
            raise StoreError(f"position not found: {key}")
        position = account.positions[index]
        if qty > position.qty + 1e-12:
            raise StoreError(
                f"sell qty {qty:g} exceeds open qty {position.qty:g} "
                f"for {position.occ_symbol}"
            )
        avg_cost = position.entry_premium
        if avg_cost is None or avg_cost <= 0:
            raise StoreError(
                f"{position.occ_symbol} has no entry cost — cannot realize PnL"
            )
        sign = 1.0 if position.side == "long" else -1.0
        realized = (exit_premium - avg_cost) * position.multiplier * qty * sign
        remaining = position.qty - qty
        closed = remaining <= 1e-12
        if closed:
            account.positions.pop(index)
            after: Position | None = None
            qty_remaining = 0.0
        else:
            after = position.model_copy(update={"qty": remaining})
            account.positions[index] = after
            qty_remaining = remaining
        self.save(account)
        sync_book(account, self.home)
        append_sell_event(
            account,
            position_id=position.id,
            occ_symbol=position.occ_symbol,
            side=position.side,
            qty_sold=qty,
            exit_premium=exit_premium,
            avg_cost=avg_cost,
            realized=realized,
            qty_remaining=qty_remaining,
            closed=closed,
            multiplier=position.multiplier,
            home=self.home,
        )
        return SellOutcome(
            account=account,
            position=after,
            qty_sold=qty,
            exit_premium=exit_premium,
            avg_cost=avg_cost,
            realized=realized,
            closed=closed,
            side=position.side,
            occ_symbol=position.occ_symbol,
            multiplier=position.multiplier,
        )

    def update_positions(
        self,
        account: Account,
        *,
        log_refresh_iv: bool = False,
        surface_summary: list[dict] | None = None,
    ) -> None:
        self.save(account)
        sync_book(account, self.home)
        if log_refresh_iv:
            append_refresh_iv_event(
                account,
                home=self.home,
                surfaces=surface_summary,
            )


def realized_pnl_summary(
    account: str,
    home: Path | None = None,
) -> dict[str, Any]:
    """Sum realized cash PnL from journal ``sell`` events for one account."""
    path = log_path(account, home)
    total = 0.0
    n_sells = 0
    by_occ: dict[str, float] = {}
    if not path.exists():
        return {"realized": 0.0, "n_sells": 0, "by_occ": {}}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("event") != "sell":
            continue
        try:
            realized = float(event.get("realized", 0.0))
        except (TypeError, ValueError):
            continue
        occ = str(event.get("occ") or "?").upper()
        total += realized
        n_sells += 1
        by_occ[occ] = by_occ.get(occ, 0.0) + realized
    return {"realized": total, "n_sells": n_sells, "by_occ": by_occ}
