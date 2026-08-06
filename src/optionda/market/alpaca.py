from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

import httpx

from pathlib import Path

from optionda.config import load_config
from optionda.credentials import AlpacaCredentials
from optionda.market.iv_mid import imply_iv_from_premium, quote_mid
from optionda.models import IvMode, OptionIvQuote, SpotQuote
from optionda.occ import parse_occ

DATA_URL = "https://data.alpaca.markets"
OPTIONS_URL = "https://data.alpaca.markets"

OptionsFeed = Literal["opra", "indicative"]

# 24/5 stock coverage for Basic plans:
# - overnight: 20:00–04:00 ET (BOATS-derived; available on Basic)
# - delayed_sip: RTH + extended with delay (broader tape than IEX)
# - iex: default free RTH/AH tape
# boats (real-time overnight) needs Algo Trader Plus — try soft-fail.
_STOCK_FEEDS = ("overnight", "boats", "delayed_sip", "iex")


class AlpacaError(RuntimeError):
    pass


class AlpacaClient:
    source = "alpaca"

    def __init__(
        self,
        creds: AlpacaCredentials,
        timeout: float = 20.0,
        *,
        options_feed: Literal["auto", "opra", "indicative"] = "auto",
        iv_mode: IvMode = "mid",
        home: Path | None = None,
    ) -> None:
        self.creds = creds
        self.timeout = timeout
        self.options_feed = options_feed
        self.iv_mode = iv_mode
        self.home = home

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
        """Resolve spots across RTH / AH / overnight feeds; newest print wins."""
        uniq = sorted(set(s.upper() for s in symbols))
        if not uniq:
            return {}
        by_symbol: dict[str, list[tuple[datetime, float, str]]] = {
            s: [] for s in uniq
        }
        with httpx.Client(timeout=self.timeout, headers=self._headers()) as client:
            for feed in _STOCK_FEEDS:
                params = {"symbols": ",".join(uniq), "feed": feed}
                try:
                    trades = self._get(
                        client, f"{DATA_URL}/v2/stocks/trades/latest", params
                    )
                    quotes = self._get(
                        client, f"{DATA_URL}/v2/stocks/quotes/latest", params
                    )
                except AlpacaError:
                    continue
                trade_map = (trades or {}).get("trades") or {}
                quote_map = (quotes or {}).get("quotes") or {}
                for symbol in uniq:
                    trade = trade_map.get(symbol)
                    if trade and trade.get("p") is not None and float(trade["p"]) > 0:
                        by_symbol[symbol].append(
                            (
                                _parse_ts(trade.get("t"))
                                or datetime.now(timezone.utc),
                                float(trade["p"]),
                                f"alpaca/{feed}/trade",
                            )
                        )
                    quote = quote_map.get(symbol)
                    if quote:
                        mid = _quote_mid_price(quote)
                        if mid is not None:
                            by_symbol[symbol].append(
                                (
                                    _parse_ts(quote.get("t"))
                                    or datetime.now(timezone.utc),
                                    mid,
                                    f"alpaca/{feed}/quote",
                                )
                            )

        out: dict[str, SpotQuote] = {}
        for symbol, candidates in by_symbol.items():
            if not candidates:
                continue
            # Prefer quote mid over trade when timestamps are equal-ish (<2s)
            candidates.sort(key=lambda c: c[0], reverse=True)
            best_t, best_px, best_src = candidates[0]
            for as_of, price, src in candidates[1:]:
                if abs((best_t - as_of).total_seconds()) <= 2.0 and "/quote" in src:
                    best_t, best_px, best_src = as_of, price, src
                    break
            out[symbol] = SpotQuote(
                symbol=symbol,
                price=best_px,
                as_of=best_t,
                source=best_src,
            )
        return out

    def get_option_mid(self, occ_symbol: str) -> float | None:
        symbol = occ_symbol.strip().upper()
        with httpx.Client(timeout=self.timeout, headers=self._headers()) as client:
            for feed in self._options_feed_candidates():
                try:
                    data = self._fetch_option_snapshot(client, symbol, feed)
                    node = _extract_option_snapshot(data, symbol)
                    mid = quote_mid(node)
                    if mid is not None:
                        return mid
                except AlpacaError:
                    continue
        return None

    def get_option_chain_snapshots(self, underlying: str) -> dict[str, dict[str, Any]]:
        """Fetch a full underlying chain snapshot from the best available feed."""
        symbol = underlying.strip().upper()
        errors: list[str] = []
        with httpx.Client(timeout=self.timeout, headers=self._headers()) as client:
            for feed in self._options_feed_candidates():
                try:
                    snapshots = self._fetch_chain_pages(client, symbol, feed)
                except AlpacaError as exc:
                    errors.append(f"{feed}: {exc}")
                    continue
                if snapshots:
                    return {
                        str(occ).upper(): node
                        for occ, node in snapshots.items()
                        if isinstance(node, dict)
                    }
                errors.append(f"{feed}: missing snapshots")
        detail = "; ".join(errors) if errors else "unknown"
        raise AlpacaError(f"no option chain snapshots for {symbol} ({detail})")

    def _fetch_chain_pages(
        self,
        client: httpx.Client,
        symbol: str,
        feed: OptionsFeed,
    ) -> dict[str, Any]:
        """Fetch all snapshot pages; Alpaca limits each page to 1,000 contracts."""
        snapshots: dict[str, Any] = {}
        page_token: str | None = None
        for _ in range(100):
            params = {"feed": feed, "limit": "1000"}
            if page_token:
                params["page_token"] = page_token
            payload = self._get(
                client,
                f"{OPTIONS_URL}/v1beta1/options/snapshots/{symbol}",
                params=params,
            )
            page = payload.get("snapshots")
            if not isinstance(page, dict):
                raise AlpacaError("unexpected option chain snapshot shape")
            snapshots.update(page)
            next_token = payload.get("next_page_token")
            if not next_token:
                return snapshots
            page_token = str(next_token)
        raise AlpacaError("option chain pagination exceeded 100 pages")

    def _options_feed_candidates(self) -> list[OptionsFeed]:
        if self.options_feed == "opra":
            return ["opra"]
        if self.options_feed == "indicative":
            return ["indicative"]
        # auto: prefer real OPRA; free accounts fall back to indicative
        return ["opra", "indicative"]

    def get_option_iv(self, occ_symbol: str) -> OptionIvQuote:
        """Prefer IV implied from option mid; fall back to vendor IV field."""
        symbol = occ_symbol.strip().upper()
        parts = parse_occ(symbol)
        mode = self.iv_mode or load_config(self.home).iv_mode
        spot_map = self.get_spots([parts.underlying])
        spot_q = spot_map.get(parts.underlying)
        errors: list[str] = []
        with httpx.Client(timeout=self.timeout, headers=self._headers()) as client:
            for feed in self._options_feed_candidates():
                try:
                    data = self._fetch_option_snapshot(client, symbol, feed)
                    node = _extract_option_snapshot(data, symbol)
                except AlpacaError as exc:
                    errors.append(f"{feed}: {exc}")
                    continue

                as_of = None
                latest = node.get("latestTrade") or node.get("latestQuote")
                if isinstance(latest, dict):
                    as_of = _parse_ts(latest.get("t") or latest.get("timestamp"))

                mid = quote_mid(node)
                if mode in {"mid", "auto"} and mid is not None and spot_q is not None:
                    try:
                        quote = imply_iv_from_premium(
                            symbol,
                            spot_q.price,
                            mid,
                            home=self.home,
                            source=f"alpaca/{feed}+mid",
                        )
                        if as_of is not None:
                            quote = quote.model_copy(update={"as_of": as_of})
                        return quote
                    except Exception as exc:  # noqa: BLE001
                        errors.append(f"{feed}+mid: {exc}")
                        if mode == "mid":
                            # still try vendor on this feed before next feed
                            pass

                if mode in {"vendor", "auto", "mid"}:
                    iv = _extract_iv(node)
                    if iv is not None and iv > 0:
                        if iv > 5.0:
                            iv = iv / 100.0
                        return OptionIvQuote(
                            occ_symbol=symbol,
                            iv=iv,
                            as_of=as_of or datetime.now(timezone.utc),
                            source=f"alpaca/{feed}",
                        )
                    errors.append(f"{feed}: no IV field")
        detail = "; ".join(errors) if errors else "unknown"
        raise AlpacaError(f"no IV for {symbol} ({detail})")

    def _fetch_option_snapshot(
        self,
        client: httpx.Client,
        symbol: str,
        feed: OptionsFeed,
    ) -> dict:
        # Official shape: GET /v1beta1/options/snapshots?symbols=...&feed=opra|indicative
        try:
            return self._get(
                client,
                f"{OPTIONS_URL}/v1beta1/options/snapshots",
                params={"symbols": symbol, "feed": feed},
            )
        except AlpacaError:
            return self._get(
                client,
                f"{OPTIONS_URL}/v1beta1/options/snapshots/{symbol}",
                params={"feed": feed},
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


def _quote_mid_price(quote: dict) -> float | None:
    bid = quote.get("bp")
    ask = quote.get("ap")
    try:
        if bid is not None and ask is not None and float(bid) > 0 and float(ask) > 0:
            return (float(bid) + float(ask)) / 2.0
        if ask is not None and float(ask) > 0:
            return float(ask)
        if bid is not None and float(bid) > 0:
            return float(bid)
    except (TypeError, ValueError):
        return None
    return None


def _extract_iv(node: dict) -> float | None:
    # Alpaca OptionsSnapshot: top-level implied_volatility + nested greeks
    for key in ("implied_volatility", "impliedVolatility", "iv"):
        if node.get(key) is not None:
            return float(node[key])
    greeks = node.get("greeks")
    if isinstance(greeks, dict):
        for key in ("implied_volatility", "impliedVolatility", "iv"):
            if greeks.get(key) is not None:
                return float(greeks[key])
    return None


def _parse_ts(value: Any) -> datetime | None:
    if not value or not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
