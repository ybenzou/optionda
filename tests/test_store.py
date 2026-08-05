from datetime import date, datetime, timezone

from optionda.models import Position
from optionda.store import AccountStore, StoreError
import pytest


def _pos(symbol: str = "AAPL250117C00200000") -> Position:
    return Position(
        occ_symbol=symbol,
        underlying="AAPL",
        expiry=date(2025, 1, 17),
        strike=200,
        option_type="call",
        qty=2,
        side="long",
        iv_frozen=0.3,
        iv_as_of=datetime.now(timezone.utc),
    )


def test_create_activate_add_delete(tmp_path, monkeypatch) -> None:
    store = AccountStore(tmp_path)
    store.create("main")
    assert store.list_accounts() == ["main"]
    assert store.active_name() is None

    store.create("hedge")
    monkeypatch.setenv("OPTIONDA_ACTIVE", "hedge")
    assert store.active_name() == "hedge"
    assert store.current_name() == "hedge"

    store.add_position(None, _pos())
    acc = store.load("hedge")
    assert len(acc.positions) == 1

    store.delete_position(None, "AAPL250117C00200000")
    assert store.load("hedge").positions == []


def test_require_current_needs_activate(tmp_path) -> None:
    store = AccountStore(tmp_path)
    store.create("main")
    with pytest.raises(StoreError, match="activate"):
        store.require_current()


def test_cannot_peek_other_account(tmp_path, monkeypatch) -> None:
    store = AccountStore(tmp_path)
    store.create("demo")
    store.create("hedge")
    monkeypatch.setenv("OPTIONDA_ACTIVE", "demo")
    store.add_position(None, _pos("AAPL270115C00200000"))
    with pytest.raises(StoreError, match="not active"):
        store.require_current("hedge")
    # Without activation, no book is visible
    monkeypatch.delenv("OPTIONDA_ACTIVE", raising=False)
    with pytest.raises(StoreError, match="activate"):
        store.require_current("demo")


def test_duplicate_account(tmp_path) -> None:
    store = AccountStore(tmp_path)
    store.create("main")
    with pytest.raises(StoreError):
        store.create("main")


def test_add_merges_qty(tmp_path, monkeypatch) -> None:
    store = AccountStore(tmp_path)
    store.create("demo")
    monkeypatch.setenv("OPTIONDA_ACTIVE", "demo")

    first = store.add_position(None, _pos("AAPL270115C00200000"))
    assert first.merged is False
    assert first.position.qty == 2

    again = _pos("AAPL270115C00200000")
    again = again.model_copy(update={"qty": 3, "iv_frozen": 0.35})
    second = store.add_position(None, again)
    assert second.merged is True
    assert second.previous_qty == 2
    assert second.position.qty == 5
    assert second.position.iv_frozen == 0.35
    assert len(store.load("demo").positions) == 1
