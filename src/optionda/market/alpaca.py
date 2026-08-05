from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import httpx

from optionda.credentials import AlpacaCredentials
from optionda.models import OptionIvQuote, SpotQuote

DATA_URL = "https://data.alpaca.markets"
OPTIONS_URL = "https://data.alpaca.markets"


class AlpacaError(RuntimeError):
    pass


class AlpacaClient:
    source = "alpaca"

    def __init__(self, creds: AlpacaCredentials, timeout: float = 20.0) -> None:
        self.creds = creds
        self.timeout = timeout

    def _headers(self) -> dict[str, str]:
        return {
            "APCA-API-KEY-ID": self.creds.key_id,
            "APCA-API-SECRET-KEY": self.creds.secret,
            "Accept": "application/json",
        }

    def verify(self) -> str:
        """Probe Alpaca with these credentials. Raises AlpacaError if rejected."""
        with httpx.Client(timeout=self.timeout, headers=self._headers()) as client:
            response = client.get(
                f"{DATA_URL}/v2/stocks/trades/latest",
                params={"symbols": "SPY"},
            )
            if response.status_code in (401, 403):
                raise AlpacaError(
                    f"credentials rejected by Alpaca (HTTP {response.status_code})"
                )
            if response.status_code >= 400:
                raise AlpacaError(
                    f"verify failed (HTTP {response.status_code}): {response.text[:160]}"
                )
            payload = response.json()
            trades = payload.get("trades") if isinstance(payload, dict) else None
            spy = (trades or {}).get("SPY") if isinstance(trades, dict) else None
            price = spy.get("p") if isinstance(spy, dict) else None
            if price is not None:
                return f"verified (SPY last={float(price):.2f})"
            return "verified (market data reachable)"

    def get_spots(self, symbols: list[str]) -> dict[str, SpotQuote]:
        uniq = sorted(set(s.upper() for s in symbols))
        if not uniq:
            return {}
        # Prefer latest trades; fall back to latest quotes mid
        params = {"symbols": ",".join(uniq)}
        with httpx.Client(timeout=self.timeout, headers=self._headers()) as client:
            trades = self._get(client, f"{DATA_URL}/v2/stocks/trades/latest", params)
            quotes = self._get(client, f"{DATA_URL}/v2/stocks/quotes/latest", params)

        trade_map = (trades or {}).get("trades") or {}
        quote_map = (quotes or {}).get("quotes") or {}
        out: dict[str, SpotQuote] = {}
        for symbol in uniq:
            price = None
            as_of = None
            trade = trade_map.get(symbol)
            if trade and trade.get("p") is not None:
                price = float(trade["p"])
                as_of = _parse_ts(trade.get("t"))
            if price is None:
                quote = quote_map.get(symbol)
                if quote:
                    bid = quote.get("bp")
                    ask = quote.get("ap")
                    if bid and ask and float(bid) > 0 and float(ask) > 0:
                        price = (float(bid) + float(ask)) / 2.0
                    elif ask and float(ask) > 0:
                        price = float(ask)
                    elif bid and float(bid) > 0:
                        price = float(bid)
                    as_of = _parse_ts(quote.get("t"))
            if price is None or price <= 0:
                continue
            out[symbol] = SpotQuote(
                symbol=symbol,
                price=price,
                as_of=as_of or datetime.now(timezone.utc),
                source=self.source,
            )
        return out

    def get_option_iv(self, occ_symbol: str) -> OptionIvQuote:
        symbol = occ_symbol.strip().upper()
        with httpx.Client(timeout=self.timeout, headers=self._headers()) as client:
            # Official shape: GET /v1beta1/options/snapshots?symbols=...&feed=indicative
            try:
                data = self._get(
                    client,
                    f"{OPTIONS_URL}/v1beta1/options/snapshots",
                    params={"symbols": symbol, "feed": "indicative"},
                )
            except AlpacaError:
                data = self._get(
                    client,
                    f"{OPTIONS_URL}/v1beta1/options/snapshots/{symbol}",
                    params={"feed": "indicative"},
                )
        if not data:
            raise AlpacaError(f"empty alpaca snapshot for {symbol}")
        node = _extract_option_snapshot(data, symbol)
        iv = _extract_iv(node)
        if iv is None or iv <= 0:
            raise AlpacaError(f"no IV in alpaca snapshot for {symbol}")
        as_of = None
        latest = node.get("latestTrade") or node.get("latestQuote")
        if isinstance(latest, dict):
            as_of = _parse_ts(latest.get("t") or latest.get("timestamp"))
        return OptionIvQuote(
            occ_symbol=symbol,
            iv=iv,
            as_of=as_of or datetime.now(timezone.utc),
            source=self.source,
        )

    @staticmethod
    def _get(client: httpx.Client, url: str, params: dict[str, str] | None = None) -> dict:
        response = client.get(url, params=params)
        if response.status_code >= 400:
            raise AlpacaError(f"alpaca HTTP {response.status_code}: {response.text[:200]}")
        payload = response.json()
        if not isinstance(payload, dict):
            raise AlpacaError("unexpected alpaca response")
        return payload


def _extract_option_snapshot(data: dict, symbol: str) -> dict:
    node: Any = data
    if isinstance(data.get("snapshots"), dict):
        node = data["snapshots"].get(symbol) or next(iter(data["snapshots"].values()), None)
    elif symbol in data and isinstance(data[symbol], dict):
        node = data[symbol]
    if isinstance(node, dict) and symbol in node and isinstance(node[symbol], dict):
        node = node[symbol]
    if not isinstance(node, dict):
        raise AlpacaError(f"unexpected alpaca snapshot shape for {symbol}")
    return node


def _extract_iv(node: dict) -> float | None:
    greeks = node.get("greeks")
    if isinstance(greeks, dict):
        for key in ("implied_volatility", "impliedVolatility", "iv"):
            if greeks.get(key) is not None:
                return float(greeks[key])
    for key in ("implied_volatility", "impliedVolatility", "iv"):
        if node.get(key) is not None:
            return float(node[key])
    return None


def _parse_ts(value: Any) -> datetime | None:
    if not value or not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
