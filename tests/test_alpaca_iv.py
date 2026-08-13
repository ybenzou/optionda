from unittest.mock import MagicMock, patch

import pytest

from optionda.credentials import AlpacaCredentials
from optionda.market.alpaca import AlpacaClient, AlpacaError


def _client(feed: str = "auto") -> AlpacaClient:
    return AlpacaClient(
        AlpacaCredentials(key_id="PK", secret="SEC"),
        options_feed=feed,  # type: ignore[arg-type]
    )


def test_auto_prefers_opra_then_indicative() -> None:
    client = _client("auto")
    assert client._options_feed_candidates() == ["opra", "indicative"]


def test_option_chain_uses_underlying_snapshot_endpoint() -> None:
    client = _client("indicative")
    first_page = {
        "next_page_token": "page-2",
        "snapshots": {
            "SPCX260918P00100000": {
                "impliedVolatility": 0.86,
                "greeks": {"delta": -0.25},
                "latestQuote": {"bp": 6.14, "ap": 6.32},
            }
        }
    }
    second_page = {
        "next_page_token": None,
        "snapshots": {
            "SPCX261218C00150000": {
                "impliedVolatility": 0.91,
                "greeks": {"delta": 0.35},
                "latestQuote": {"bp": 7.10, "ap": 7.30},
            }
        },
    }
    seen: list[tuple[str, dict | None]] = []

    def fake_get(_client, url, params=None):
        seen.append((url, params))
        return second_page if (params or {}).get("page_token") else first_page

    with patch.object(client, "_get", side_effect=fake_get):
        with patch("optionda.market.alpaca.httpx.Client") as cls:
            cls.return_value.__enter__.return_value = MagicMock()
            snapshots = client.get_option_chain_snapshots("SPCX")

    assert snapshots == {
        **first_page["snapshots"],
        **second_page["snapshots"],
    }
    assert seen == [
        (
            "https://data.alpaca.markets/v1beta1/options/snapshots/SPCX",
            {"feed": "indicative", "limit": "1000"},
        ),
        (
            "https://data.alpaca.markets/v1beta1/options/snapshots/SPCX",
            {"feed": "indicative", "limit": "1000", "page_token": "page-2"},
        ),
    ]


def test_get_spots_prefers_newest_overnight_feed() -> None:
    from datetime import datetime, timezone

    client = _client("auto")

    def fake_get(_client, url, params=None):
        feed = (params or {}).get("feed")
        if feed == "boats":
            raise AlpacaError("alpaca HTTP 403: no boats")
        if "trades" in url:
            if feed == "overnight":
                return {
                    "trades": {
                        "SPCX": {
                            "p": 116.17,
                            "t": "2026-08-05T05:55:39Z",
                        }
                    }
                }
            return {
                "trades": {
                    "SPCX": {"p": 118.81, "t": "2026-08-04T20:59:36Z"}
                }
            }
        if "quotes" in url:
            if feed == "overnight":
                return {
                    "quotes": {
                        "SPCX": {
                            "bp": 115.95,
                            "ap": 116.11,
                            "t": "2026-08-05T06:10:47Z",
                        }
                    }
                }
            return {
                "quotes": {
                    "SPCX": {"bp": 117.09, "ap": 117.48, "t": "2026-08-04T20:57:09Z"}
                }
            }
        return {}

    with patch.object(client, "_get", side_effect=fake_get):
        with patch("optionda.market.alpaca.httpx.Client") as cls:
            cls.return_value.__enter__.return_value = MagicMock()
            spots = client.get_spots(["SPCX"])
    q = spots["SPCX"]
    assert q.price == pytest.approx(116.03, abs=0.01)  # overnight quote mid
    assert "overnight" in q.source
    assert q.as_of == datetime(2026, 8, 5, 6, 10, 47, tzinfo=timezone.utc)


def test_get_spot_at_uses_historical_trade_before_option_quote() -> None:
    from datetime import datetime, timezone

    client = _client("auto")
    option_quote_time = datetime(2026, 8, 11, 19, 59, 59, tzinfo=timezone.utc)
    seen: list[dict] = []

    def fake_get(_client, url, params=None):
        seen.append(dict(params or {}))
        assert url.endswith("/v2/stocks/SKHY/trades")
        return {
            "trades": [
                {"p": 141.65, "t": "2026-08-11T19:59:58Z"},
            ]
        }

    with patch.object(client, "_get", side_effect=fake_get):
        with patch("optionda.market.alpaca.httpx.Client") as cls:
            cls.return_value.__enter__.return_value = MagicMock()
            spot = client.get_spot_at("SKHY", option_quote_time)

    assert spot.price == pytest.approx(141.65)
    assert spot.as_of == datetime(2026, 8, 11, 19, 59, 58, tzinfo=timezone.utc)
    assert spot.source == "alpaca/sip/historical-trade"
    assert seen[0]["feed"] == "sip"
    assert seen[0]["sort"] == "desc"
    assert seen[0]["end"] == option_quote_time.isoformat()


def test_get_option_iv_falls_back_to_indicative_vendor() -> None:
    client = _client("auto")
    client.iv_mode = "vendor"
    snap = {
        "snapshots": {
            "INTC261016C00140000": {
                "implied_volatility": 0.8652,
                "latestTrade": {"t": "2026-08-05T12:00:00Z"},
            }
        }
    }

    def fake_get(_client, url, params=None):
        feed = (params or {}).get("feed")
        if feed == "opra":
            raise AlpacaError("alpaca HTTP 403: subscription required")
        assert feed == "indicative"
        return snap

    with patch.object(client, "_get", side_effect=fake_get):
        with patch.object(client, "get_spots", return_value={}):
            with patch("optionda.market.alpaca.httpx.Client") as cls:
                cls.return_value.__enter__.return_value = MagicMock()
                quote = client.get_option_iv("INTC261016C00140000")
    assert quote.iv == pytest.approx(0.8652)
    assert quote.source == "alpaca/indicative"


def test_get_option_iv_prefers_mid() -> None:
    from optionda.models import SpotQuote
    from datetime import datetime, timezone

    client = _client("indicative")
    client.iv_mode = "mid"
    snap = {
        "snapshots": {
            "INTC261016C00140000": {
                "implied_volatility": 0.931,
                "latestQuote": {"bp": 3.40, "ap": 3.56, "t": "2026-08-05T12:00:00Z"},
            }
        }
    }
    spots = {
        "INTC": SpotQuote(
            symbol="INTC",
            price=120.0,
            as_of=datetime.now(timezone.utc),
            source="mock",
        )
    }
    with patch.object(client, "_get", return_value=snap):
        with patch.object(client, "get_spots", return_value=spots):
            with patch("optionda.market.alpaca.httpx.Client") as cls:
                cls.return_value.__enter__.return_value = MagicMock()
                quote = client.get_option_iv("INTC261016C00140000")
    assert quote.source == "alpaca/indicative+mid"
    # Mid-implied should not blindly copy the vendor 93.1% field
    assert quote.iv != pytest.approx(0.931, abs=1e-3)


def test_get_option_iv_uses_spot_at_option_quote_time_not_overnight() -> None:
    from datetime import datetime, timezone

    from optionda.models import SpotQuote

    client = _client("indicative")
    client.iv_mode = "mid"
    quote_t = datetime(2026, 8, 12, 20, 0, tzinfo=timezone.utc)
    snap = {
        "snapshots": {
            "GOOG261218C00400000": {
                "implied_volatility": 0.40,
                "latestQuote": {"bp": 9.8, "ap": 10.0, "t": quote_t.isoformat()},
            }
        }
    }
    close = SpotQuote(
        symbol="GOOG",
        price=330.0,
        as_of=quote_t,
        source="alpaca/sip/historical-trade",
    )
    overnight = SpotQuote(
        symbol="GOOG",
        price=350.0,
        as_of=datetime(2026, 8, 13, 3, 0, tzinfo=timezone.utc),
        source="alpaca/overnight/trade",
    )
    with patch.object(client, "_get", return_value=snap):
        with patch.object(client, "get_spot_at", return_value=close) as at:
            with patch.object(client, "get_spots", return_value={"GOOG": overnight}) as spots:
                with patch("optionda.market.alpaca.httpx.Client") as cls:
                    cls.return_value.__enter__.return_value = MagicMock()
                    with patch(
                        "optionda.market.alpaca.imply_iv_from_premium"
                    ) as imply:
                        from optionda.models import OptionIvQuote

                        imply.return_value = OptionIvQuote(
                            occ_symbol="GOOG261218C00400000",
                            iv=0.33,
                            as_of=quote_t,
                            source="alpaca/indicative+mid",
                        )
                        quote = client.get_option_iv("GOOG261218C00400000")
    at.assert_called_once()
    assert at.call_args.args[0] == "GOOG"
    assert at.call_args.args[1] == quote_t
    spots.assert_not_called()
    assert imply.call_args.kwargs["source"] == "alpaca/indicative+mid"
    assert imply.call_args.args[1] == pytest.approx(330.0)
    assert quote.iv == pytest.approx(0.33)


def test_percent_scale_iv_normalized() -> None:
    client = _client("indicative")
    client.iv_mode = "vendor"
    snap = {
        "snapshots": {
            "INTC261016C00140000": {"implied_volatility": 86.52},
        }
    }
    with patch.object(client, "_get", return_value=snap):
        with patch.object(client, "get_spots", return_value={}):
            with patch("optionda.market.alpaca.httpx.Client") as cls:
                cls.return_value.__enter__.return_value = MagicMock()
                quote = client.get_option_iv("INTC261016C00140000")
    assert quote.iv == pytest.approx(0.8652)
