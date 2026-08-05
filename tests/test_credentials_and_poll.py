from optionda.config import apply_feed_defaults, load_config, save_config
from optionda.credentials import clear_alpaca, has_alpaca, load_alpaca, save_alpaca
from optionda.market.router import resolve_poll_interval


def test_alpaca_key_sets_15s_poll(tmp_path) -> None:
    assert has_alpaca(tmp_path) is False
    assert resolve_poll_interval(tmp_path) == 60

    save_alpaca("PKTEST", "SECRET", tmp_path)
    cfg = apply_feed_defaults(load_config(tmp_path), "alpaca")
    save_config(cfg, tmp_path)

    assert has_alpaca(tmp_path) is True
    creds = load_alpaca(tmp_path)
    assert creds is not None
    assert creds.key_id == "PKTEST"
    assert creds.secret == "SECRET"
    assert resolve_poll_interval(tmp_path) == 15

    clear_alpaca(tmp_path)
    save_config(apply_feed_defaults(load_config(tmp_path), "yahoo"), tmp_path)
    assert has_alpaca(tmp_path) is False
    assert resolve_poll_interval(tmp_path) == 60
