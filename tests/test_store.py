from datetime import date, datetime, timezone

from optionda.config import load_config
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


def test_create_use_add_delete(tmp_path) -> None:
    store = AccountStore(tmp_path)
    store.create("main")
    assert store.list_accounts() == ["main"]
    assert load_config(tmp_path).default_account == "main"

    store.create("hedge")
    store.use("hedge")
    assert store.current_name() == "hedge"

    store.add_position(None, _pos())
    acc = store.load("hedge")
    assert len(acc.positions) == 1

    store.delete_position(None, "AAPL250117C00200000")
    assert store.load("hedge").positions == []


def test_duplicate_account(tmp_path) -> None:
    store = AccountStore(tmp_path)
    store.create("main")
    with pytest.raises(StoreError):
        store.create("main")
