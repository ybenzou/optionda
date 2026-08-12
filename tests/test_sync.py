from datetime import date, datetime, timezone

import pytest

from optionda.config import load_config, save_config
from optionda.credentials import load_alpaca, save_alpaca
from optionda.models import AppConfig, Position
from optionda.store import AccountStore
from optionda.sync import (
    PREFIX,
    SyncError,
    decode_code,
    fingerprint,
    pack_account,
    unpack_code,
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
