from datetime import date, datetime, timezone

import pytest

from optionda.journal import log_path
from optionda.models import Position
from optionda.store import AccountStore, StoreError, realized_pnl_summary


def _pos(
    symbol: str = "SPCX260918P00100000",
    *,
    qty: float = 2,
    entry: float = 6.7,
    side: str = "long",
) -> Position:
    return Position(
        occ_symbol=symbol,
        underlying="SPCX",
        expiry=date(2026, 9, 18),
        strike=100,
        option_type="put",
        qty=qty,
        side=side,  # type: ignore[arg-type]
        iv_frozen=0.9,
        iv_as_of=datetime.now(timezone.utc),
        entry_premium=entry,
        multiplier=100,
    )


def test_partial_sell_long_realizes_pnl_and_keeps_remainder(tmp_path, monkeypatch) -> None:
    store = AccountStore(tmp_path)
    store.create("demo")
    monkeypatch.setenv("OPTIONDA_ACTIVE", "demo")
    store.add_position(None, _pos(qty=2, entry=6.7))

    outcome = store.sell_position(None, "SPCX260918P00100000", qty=1, exit_premium=8.5)
    assert outcome.qty_sold == 1
    assert outcome.exit_premium == 8.5
    assert outcome.avg_cost == pytest.approx(6.7)
    # long: (8.5 - 6.7) * 100 * 1
    assert outcome.realized == pytest.approx(180.0)
    assert outcome.closed is False
    assert outcome.position is not None
    assert outcome.position.qty == 1
    assert outcome.position.entry_premium == pytest.approx(6.7)

    lines = log_path("demo", tmp_path).read_text(encoding="utf-8").strip().splitlines()
    events = [__import__("json").loads(line) for line in lines]
    sell = next(event for event in events if event["event"] == "sell")
    assert sell["qty_sold"] == 1
    assert sell["exit"] == 8.5
    assert sell["realized"] == pytest.approx(180.0)


def test_full_sell_removes_position(tmp_path, monkeypatch) -> None:
    store = AccountStore(tmp_path)
    store.create("demo")
    monkeypatch.setenv("OPTIONDA_ACTIVE", "demo")
    store.add_position(None, _pos(qty=2, entry=5.0))

    outcome = store.sell_position(None, "SPCX260918P00100000", qty=2, exit_premium=4.0)
    assert outcome.closed is True
    assert outcome.position is None
    # long: (4 - 5) * 100 * 2 = -200
    assert outcome.realized == pytest.approx(-200.0)
    assert store.load("demo").positions == []


def test_sell_short_uses_cover_math(tmp_path, monkeypatch) -> None:
    store = AccountStore(tmp_path)
    store.create("demo")
    monkeypatch.setenv("OPTIONDA_ACTIVE", "demo")
    store.add_position(None, _pos(qty=1, entry=3.0, side="short"))

    outcome = store.sell_position(None, "SPCX260918P00100000", qty=1, exit_premium=1.2)
    # short cover: (entry - exit) * mult * qty = (3 - 1.2) * 100 = 180
    assert outcome.realized == pytest.approx(180.0)
    assert outcome.closed is True


def test_sell_rejects_oversize_qty(tmp_path, monkeypatch) -> None:
    store = AccountStore(tmp_path)
    store.create("demo")
    monkeypatch.setenv("OPTIONDA_ACTIVE", "demo")
    store.add_position(None, _pos(qty=1, entry=6.7))
    with pytest.raises(StoreError, match="qty"):
        store.sell_position(None, "SPCX260918P00100000", qty=2, exit_premium=8.0)


def test_realized_summary_sums_sell_events(tmp_path, monkeypatch) -> None:
    store = AccountStore(tmp_path)
    store.create("demo")
    monkeypatch.setenv("OPTIONDA_ACTIVE", "demo")
    store.add_position(None, _pos(qty=3, entry=6.0))
    store.sell_position(None, "SPCX260918P00100000", qty=1, exit_premium=8.0)
    store.sell_position(None, "SPCX260918P00100000", qty=1, exit_premium=7.0)

    summary = realized_pnl_summary("demo", tmp_path)
    # (8-6)*100 + (7-6)*100 = 200 + 100 = 300
    assert summary["realized"] == pytest.approx(300.0)
    assert summary["n_sells"] == 2
    assert summary["by_occ"]["SPCX260918P00100000"] == pytest.approx(300.0)
