import json
from datetime import date, datetime, timezone

import pytest

from optionda.analytics import read_events
from optionda.batch import add_batch
from optionda.journal import log_path
from optionda.models import Position
from optionda.store import AccountStore, StoreError, realized_pnl_summary
from optionda.undo import undo_last


def _pos(
    occ: str = "SPCX260918P00100000",
    *,
    qty: float = 2,
    entry: float = 6.7,
    underlying: str = "SPCX",
    expiry: date | None = None,
    strike: float = 100,
    option_type: str = "put",
) -> Position:
    return Position(
        occ_symbol=occ,
        underlying=underlying,
        expiry=expiry or date(2026, 9, 18),
        strike=strike,
        option_type=option_type,  # type: ignore[arg-type]
        qty=qty,
        side="long",
        iv_frozen=0.9,
        iv_as_of=datetime.now(timezone.utc),
        entry_premium=entry,
        multiplier=100,
    )


def _events(home, name: str = "demo") -> list[dict]:
    return read_events(log_path(name, home))


def test_undo_reverses_add_batch_qty_and_cost(tmp_path, monkeypatch) -> None:
    store = AccountStore(tmp_path)
    store.create("demo")
    monkeypatch.setenv("OPTIONDA_ACTIVE", "demo")
    store.add_position(None, _pos(qty=2, entry=6.0))
    add_batch(
        store,
        ["SPCX 260918 100 P x1 @ 8.0"],
        iv=0.9,
        home=tmp_path,
    )
    pos = store.require_current().positions[0]
    assert pos.qty == pytest.approx(3)
    assert pos.entry_premium == pytest.approx(20 / 3)

    result = undo_last(store)
    pos = store.require_current().positions[0]
    assert pos.qty == pytest.approx(2)
    assert pos.entry_premium == pytest.approx(6.0)
    assert result.realized == pytest.approx(0.0)
    assert result.n_events == 1
    events = _events(tmp_path)
    assert events[-1]["event"] == "undo"
    assert events[-2].get("batch_id")
    assert events[-2]["batch_id"] == events[-1]["undone_batch_id"]


def test_undo_reverses_sell_realized_and_restores_position(
    tmp_path, monkeypatch
) -> None:
    store = AccountStore(tmp_path)
    store.create("demo")
    monkeypatch.setenv("OPTIONDA_ACTIVE", "demo")
    store.add_position(None, _pos(qty=2, entry=6.7))
    store.sell_position(None, "SPCX260918P00100000", qty=2, exit_premium=8.5)
    assert store.require_current().positions == []
    assert realized_pnl_summary("demo", tmp_path)["realized"] == pytest.approx(360.0)

    result = undo_last(store)
    acc = store.require_current()
    assert len(acc.positions) == 1
    assert acc.positions[0].qty == pytest.approx(2)
    assert acc.positions[0].entry_premium == pytest.approx(6.7)
    assert result.realized == pytest.approx(-360.0)
    assert realized_pnl_summary("demo", tmp_path)["realized"] == pytest.approx(0.0)
    assert acc.positions[0].occ_symbol == "SPCX260918P00100000"


def test_undo_reverses_mixed_add_and_sell_batch(tmp_path, monkeypatch) -> None:
    store = AccountStore(tmp_path)
    store.create("demo")
    monkeypatch.setenv("OPTIONDA_ACTIVE", "demo")
    store.add_position(
        None,
        _pos(
            "HOOD261218C00150000",
            qty=4,
            entry=3.0,
            underlying="HOOD",
            expiry=date(2026, 12, 18),
            strike=150,
            option_type="call",
        ),
    )
    add_batch(
        store,
        [
            "INTC 261016 140 C x5 @ 1.05",
            "sell HOOD 261218 150 C x2 @ 3.35",
        ],
        iv=0.7,
        home=tmp_path,
    )
    acc = store.require_current()
    occs = {pos.occ_symbol: pos for pos in acc.positions}
    assert "INTC261016C00140000" in occs
    assert occs["HOOD261218C00150000"].qty == pytest.approx(2)
    before_undo = realized_pnl_summary("demo", tmp_path)["realized"]
    assert before_undo > 0

    undo_last(store)
    acc = store.require_current()
    occs = {pos.occ_symbol: pos for pos in acc.positions}
    assert "INTC261016C00140000" not in occs
    assert occs["HOOD261218C00150000"].qty == pytest.approx(4)
    assert occs["HOOD261218C00150000"].entry_premium == pytest.approx(3.0)
    assert realized_pnl_summary("demo", tmp_path)["realized"] == pytest.approx(0.0)


def test_undo_of_undo_restores_the_batch(tmp_path, monkeypatch) -> None:
    store = AccountStore(tmp_path)
    store.create("demo")
    monkeypatch.setenv("OPTIONDA_ACTIVE", "demo")
    store.add_position(None, _pos(qty=2, entry=6.7))
    store.sell_position(None, "SPCX260918P00100000", qty=1, exit_premium=8.5)
    undo_last(store)
    undo_last(store)
    acc = store.require_current()
    assert acc.positions[0].qty == pytest.approx(1)
    assert realized_pnl_summary("demo", tmp_path)["realized"] == pytest.approx(180.0)
    assert _events(tmp_path)[-1]["event"] == "undo"


def test_undo_does_not_touch_older_command(tmp_path, monkeypatch) -> None:
    store = AccountStore(tmp_path)
    store.create("demo")
    monkeypatch.setenv("OPTIONDA_ACTIVE", "demo")
    store.add_position(None, _pos(qty=1, entry=6.0))
    store.add_position(
        None,
        _pos(
            "INTC261016C00140000",
            qty=5,
            entry=1.85,
            underlying="INTC",
            expiry=date(2026, 10, 16),
            strike=140,
            option_type="call",
        ),
    )
    undo_last(store)
    occs = [pos.occ_symbol for pos in store.require_current().positions]
    assert occs == ["SPCX260918P00100000"]


def test_legacy_cluster_without_batch_id(tmp_path, monkeypatch) -> None:
    store = AccountStore(tmp_path)
    store.create("demo")
    monkeypatch.setenv("OPTIONDA_ACTIVE", "demo")
    store.add_position(None, _pos(qty=2, entry=6.0))
    store.sell_position(None, "SPCX260918P00100000", qty=1, exit_premium=7.0)
    path = log_path("demo", tmp_path)
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    for row in rows:
        row.pop("batch_id", None)
    rows[0]["ts"] = "2026-08-01T00:00:00+00:00"
    path.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n",
        encoding="utf-8",
    )
    undo_last(store)
    acc = store.require_current()
    assert acc.positions[0].qty == pytest.approx(2)
    assert realized_pnl_summary("demo", tmp_path)["realized"] == pytest.approx(0.0)


def test_undo_without_mutations_errors(tmp_path, monkeypatch) -> None:
    store = AccountStore(tmp_path)
    store.create("demo")
    monkeypatch.setenv("OPTIONDA_ACTIVE", "demo")
    with pytest.raises(StoreError, match="nothing to undo"):
        undo_last(store)
