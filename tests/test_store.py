import json
from datetime import date, datetime, timezone

from optionda.journal import log_path
from optionda.models import Position
from optionda.store import AccountStore, StoreError
import pytest


def _pos(symbol: str = "AAPL250117C00200000", *, entry: float = 5.0) -> Position:
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
        entry_premium=entry,
    )


def test_create_activate_add_delete(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("OPTIONDA_ACTIVE", raising=False)
    store = AccountStore(tmp_path)
    store.create("main")
    assert store.list_accounts() == ["main"]
    assert store.active_name() is None

    store.create("hedge")
    store.activate("hedge")
    assert store.active_name() == "hedge"
    assert (tmp_path / "active").read_text(encoding="utf-8").strip() == "hedge"
    assert store.current_name() == "hedge"

    store.add_position(None, _pos())
    acc = store.load("hedge")
    assert len(acc.positions) == 1

    store.delete_position(None, "AAPL250117C00200000")
    assert store.load("hedge").positions == []


def test_require_current_needs_activate(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("OPTIONDA_ACTIVE", raising=False)
    store = AccountStore(tmp_path)
    store.create("main")
    with pytest.raises(StoreError, match="activate"):
        store.require_current()


def test_cannot_peek_other_account(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("OPTIONDA_ACTIVE", raising=False)
    store = AccountStore(tmp_path)
    store.create("demo")
    store.create("hedge")
    store.activate("demo")
    store.add_position(None, _pos("AAPL270115C00200000"))
    with pytest.raises(StoreError, match="not active"):
        store.require_current("hedge")
    store.deactivate()
    with pytest.raises(StoreError, match="activate"):
        store.require_current("demo")


def test_env_override_beats_active_file(tmp_path, monkeypatch) -> None:
    store = AccountStore(tmp_path)
    store.create("demo")
    store.create("hedge")
    store.activate("demo")
    monkeypatch.setenv("OPTIONDA_ACTIVE", "hedge")
    assert store.active_name() == "hedge"


def test_duplicate_account(tmp_path) -> None:
    store = AccountStore(tmp_path)
    store.create("main")
    with pytest.raises(StoreError):
        store.create("main")


def test_add_merges_qty_and_weighted_cost(tmp_path, monkeypatch) -> None:
    store = AccountStore(tmp_path)
    store.create("demo")
    monkeypatch.setenv("OPTIONDA_ACTIVE", "demo")

    first = store.add_position(None, _pos("AAPL270115C00200000", entry=5.0))
    assert first.merged is False
    assert first.position.qty == 2
    assert first.position.entry_premium == 5.0

    again = _pos("AAPL270115C00200000", entry=8.0)
    again = again.model_copy(update={"qty": 3, "iv_frozen": 0.35})
    second = store.add_position(None, again)
    assert second.merged is True
    assert second.previous_qty == 2
    assert second.previous_entry == 5.0
    assert second.position.qty == 5
    # (2*5 + 3*8) / 5 = 6.8
    assert second.position.entry_premium == pytest.approx(6.8)
    assert second.position.iv_frozen == 0.35
    assert len(store.load("demo").positions) == 1


def test_add_requires_cost(tmp_path, monkeypatch) -> None:
    store = AccountStore(tmp_path)
    store.create("demo")
    monkeypatch.setenv("OPTIONDA_ACTIVE", "demo")
    bare = _pos().model_copy(update={"entry_premium": None})
    with pytest.raises(StoreError, match="cost required"):
        store.add_position(None, bare)


def test_add_delete_append_event_log(tmp_path, monkeypatch) -> None:
    store = AccountStore(tmp_path)
    store.create("demo")
    monkeypatch.setenv("OPTIONDA_ACTIVE", "demo")

    store.add_position(None, _pos("AAPL270115C00200000", entry=5.0))
    again = _pos("AAPL270115C00200000", entry=8.0).model_copy(update={"qty": 3})
    store.add_position(None, again)
    store.delete_position(None, "AAPL270115C00200000")

    path = log_path("demo", tmp_path)
    events = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert [e["event"] for e in events] == ["add", "merge", "delete"]
    assert events[0]["qty_added"] == 2
    assert events[1]["qty"] == 5
    assert events[1]["cost"] == pytest.approx(6.8)
    assert events[2]["removed"][0]["occ"] == "AAPL270115C00200000"
    # book is rewritten (current state), log is append-only history
    assert len(events) == 3
