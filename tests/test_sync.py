import json
from datetime import date, datetime, timezone

import pytest

from optionda.analytics import build_report, read_events
from optionda.config import load_config, save_config
from optionda.credentials import load_alpaca, save_alpaca
from optionda.journal import append_event, log_path
from optionda.marks import book_on
from optionda.models import AppConfig, Position
from optionda.store import AccountStore
from optionda.sync import (
    PREFIX,
    SyncError,
    decode_code,
    encode_payload,
    fingerprint,
    pack_account,
    read_pack_text,
    unpack_code,
    unpack_source,
    write_oda,
)


def _pos(symbol: str = "SPCX260918P00100000") -> Position:
    return Position(
        occ_symbol=symbol,
        underlying="SPCX",
        expiry=date(2026, 9, 18),
        strike=100,
        option_type="put",
        qty=2,
        side="long",
        iv_frozen=0.9,
        iv_as_of=datetime.now(timezone.utc),
        entry_premium=6.7,
    )


def test_pack_unpack_roundtrip(tmp_path, monkeypatch) -> None:
    store = AccountStore(tmp_path)
    store.create("desk")
    monkeypatch.setenv("OPTIONDA_ACTIVE", "desk")
    store.add_position(None, _pos())
    save_config(
        AppConfig(r=0.05, overnight_iv_mode="sticky_delta", default_account="desk"),
        tmp_path,
    )
    save_alpaca("PKTESTKEY", "secret-value-xyz", tmp_path)

    packed = pack_account(store, home=tmp_path)
    assert packed.code.startswith(PREFIX)
    assert packed.sha256 == fingerprint(packed.code)
    assert packed.n_positions == 1
    assert packed.has_creds is True
    # Credentials must not appear in plaintext in the code.
    assert "PKTESTKEY" not in packed.code
    assert "secret-value-xyz" not in packed.code

    # Fresh home: import replaces into empty store.
    other = AccountStore(tmp_path / "other")
    bundle = unpack_code(other, packed.code, home=tmp_path / "other", overwrite=True)
    assert bundle.account.name == "desk"
    loaded = other.load("desk")
    assert len(loaded.positions) == 1
    assert loaded.positions[0].occ_symbol == "SPCX260918P00100000"
    assert loaded.positions[0].entry_premium == pytest.approx(6.7)
    cfg = load_config(tmp_path / "other")
    assert cfg.r == pytest.approx(0.05)
    assert cfg.overnight_iv_mode == "sticky_delta"
    creds = load_alpaca(tmp_path / "other")
    assert creds is not None
    assert creds.key_id == "PKTESTKEY"
    assert creds.secret == "secret-value-xyz"
    assert other.active_name() == "desk"


def test_unpack_requires_yes_when_exists(tmp_path, monkeypatch) -> None:
    store = AccountStore(tmp_path)
    store.create("desk")
    monkeypatch.setenv("OPTIONDA_ACTIVE", "desk")
    store.add_position(None, _pos())
    packed = pack_account(store, home=tmp_path)

    with pytest.raises(SyncError, match="--yes"):
        unpack_code(store, packed.code, home=tmp_path, overwrite=False)

    unpack_code(store, packed.code, home=tmp_path, overwrite=True)
    assert len(store.load("desk").positions) == 1


def test_sha256_mismatch_rejected(tmp_path, monkeypatch) -> None:
    store = AccountStore(tmp_path)
    store.create("desk")
    monkeypatch.setenv("OPTIONDA_ACTIVE", "desk")
    store.add_position(None, _pos())
    packed = pack_account(store, home=tmp_path)
    with pytest.raises(SyncError, match="sha256"):
        unpack_code(
            store,
            packed.code,
            home=tmp_path,
            sha256="0" * 64,
            overwrite=True,
        )


def test_pack_without_creds_does_not_clear_local(tmp_path, monkeypatch) -> None:
    src = AccountStore(tmp_path / "src")
    src.create("desk")
    monkeypatch.setenv("OPTIONDA_ACTIVE", "desk")
    src.add_position(None, _pos())
    packed = pack_account(src, home=tmp_path / "src")
    assert packed.has_creds is False

    dst_home = tmp_path / "dst"
    dst = AccountStore(dst_home)
    save_alpaca("PKKEEP", "keep-secret", dst_home)
    unpack_code(dst, packed.code, home=dst_home, overwrite=True)
    creds = load_alpaca(dst_home)
    assert creds is not None
    assert creds.key_id == "PKKEEP"


def test_decode_rejects_garbage() -> None:
    with pytest.raises(SyncError):
        decode_code("not-a-code")
    with pytest.raises(SyncError):
        decode_code(PREFIX + "@@@@")


def _fat_journal(tmp_path, monkeypatch) -> AccountStore:
    store = AccountStore(tmp_path)
    store.create("desk")
    monkeypatch.setenv("OPTIONDA_ACTIVE", "desk")
    store.add_position(None, _pos())
    account = store.load("desk")
    account.positions[0] = account.positions[0].model_copy(update={"iv_frozen": 0.55})
    store.update_positions(account, log_refresh_iv=True)
    append_event(
        "desk",
        {
            "event": "export",
            "feed": "alpaca",
            "sum_model": 1.0,
            "n": 1,
            "rows": [{"occ": "SPCX260918P00100000", "qty": 2, "upnl": 10.0}],
        },
        home=tmp_path,
    )
    store.sell_position(None, account.positions[0].id, qty=1, exit_premium=7.1)
    return store


def test_pack_keeps_commands_and_iv_changes_only(tmp_path, monkeypatch) -> None:
    store = _fat_journal(tmp_path, monkeypatch)
    packed = pack_account(store, home=tmp_path)
    bundle = decode_code(packed.code)
    kinds = [event.get("event") for event in bundle.journal]
    assert kinds == ["add", "refresh_iv", "sell"]
    assert packed.n_events == 3
    for event in bundle.journal:
        assert "book" not in event
        assert "rows" not in event
        assert "surfaces" not in event
    refresh = bundle.journal[1]
    assert refresh["ivs"]
    assert all(isinstance(value, float) and value > 0 for value in refresh["ivs"].values())


def test_unpack_writes_slim_journal_for_stats(tmp_path, monkeypatch) -> None:
    src = _fat_journal(tmp_path / "src", monkeypatch)
    packed = pack_account(src, home=tmp_path / "src")
    dest = tmp_path / "dest"
    other = AccountStore(dest)
    unpack_code(other, packed.code, home=dest, overwrite=True)

    events = read_events(log_path("desk", dest))
    assert [event.get("event") for event in events] == ["add", "refresh_iv", "sell"]
    assert all("book" not in event for event in events)

    held = book_on(events, date(2099, 1, 1))
    assert len(held.lots) == 1
    assert held.lots[0].qty == pytest.approx(1.0)
    assert held.lots[0].iv == pytest.approx(0.55)
    assert held.realized_cum > 0

    report = build_report("desk", dest, with_marks=False)
    assert report.n_sells == 1
    assert len(report.open_lots) == 1
    assert report.open_lots[0].occ == "SPCX260918P00100000"


def test_v1_pack_does_not_replace_destination_journal(tmp_path, monkeypatch) -> None:
    src = AccountStore(tmp_path / "src")
    src.create("desk")
    monkeypatch.setenv("OPTIONDA_ACTIVE", "desk")
    src.add_position(None, _pos())
    account = json.loads(src.load("desk").model_dump_json())
    packed = encode_payload(
        {"v": 1, "account": account, "config": {}, "creds": None}
    )

    dest = tmp_path / "dest"
    dest_store = AccountStore(dest)
    dest_store.create("desk")
    log_path("desk", dest).write_text(
        '{"ts":"2026-03-10T20:00:00+00:00","event":"add","id":"keep","occ":"HOOD260618C00150000","qty":1,"cost":1,"iv":0.4}\n',
        encoding="utf-8",
    )
    unpack_code(dest_store, packed.code, home=dest, overwrite=True)
    kept = read_events(log_path("desk", dest))
    assert kept[0]["id"] == "keep"


def test_pack_file_roundtrip(tmp_path, monkeypatch) -> None:
    store = AccountStore(tmp_path)
    store.create("desk")
    monkeypatch.setenv("OPTIONDA_ACTIVE", "desk")
    store.add_position(None, _pos())
    packed = pack_account(store, home=tmp_path)
    path = write_oda(tmp_path / "desk.oda", packed)
    assert path.name == "desk.oda"
    assert path.read_text(encoding="utf-8").startswith(PREFIX)

    dest = tmp_path / "dest"
    other = AccountStore(dest)
    bundle = unpack_source(other, path, home=dest, overwrite=True)
    assert bundle.account.name == "desk"
    assert len(other.load("desk").positions) == 1
    assert read_pack_text(path).startswith(PREFIX)


def test_read_pack_text_still_accepts_oda1_code(tmp_path, monkeypatch) -> None:
    store = AccountStore(tmp_path)
    store.create("desk")
    monkeypatch.setenv("OPTIONDA_ACTIVE", "desk")
    store.add_position(None, _pos())
    packed = pack_account(store, home=tmp_path)
    assert read_pack_text(packed.code).startswith(PREFIX)


def test_read_pack_text_rejects_missing_file() -> None:
    with pytest.raises(SyncError, match="not a pack"):
        read_pack_text("missing.oda")


def test_invalid_journal_rejects_unpack(tmp_path, monkeypatch) -> None:
    store = AccountStore(tmp_path)
    store.create("desk")
    monkeypatch.setenv("OPTIONDA_ACTIVE", "desk")
    store.add_position(None, _pos())
    account = json.loads(store.load("desk").model_dump_json())
    packed = encode_payload(
        {"v": 2, "account": account, "config": {}, "creds": None, "journal": "nope"}
    )
    with pytest.raises(SyncError, match="journal"):
        decode_code(packed.code)
