from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any, Literal
from zoneinfo import ZoneInfo

import httpx

from pathlib import Path

from optionda.config import load_config
from optionda.credentials import AlpacaCredentials
from optionda.market.iv_mid import imply_iv_from_premium, quote_mid
from optionda.market.session import (
    CALENDAR_LOOKAHEAD_DAYS,
    CALENDAR_LOOKBACK_DAYS,
    DailyClose,
    MarketClock,
    MarketSession,
    parse_calendar_days,
    parse_clock,
)
from optionda.models import IvMode, OptionIvQuote, SpotQuote
from optionda.occ import parse_occ

DATA_URL = "https://data.alpaca.markets"
OPTIONS_URL = "https://data.alpaca.markets"
LIVE_TRADING_URL = "https://api.alpaca.markets"
PAPER_TRADING_URL = "https://paper-api.alpaca.markets"
_TRADING_HOSTS = (LIVE_TRADING_URL, PAPER_TRADING_URL)
_CALENDAR_TTL = timedelta(hours=6)
_trading_host: str | None = None
_calendar_cache: dict[str, tuple[datetime, list[MarketSession]]] = {}

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

    def get_spot_at(self, symbol: str, as_of: datetime) -> SpotQuote:
        """Latest underlying trade at/before an option quote timestamp."""
        ticker = symbol.strip().upper()
        instant = as_of
        if instant.tzinfo is None:
            instant = instant.replace(tzinfo=timezone.utc)
        instant = instant.astimezone(timezone.utc)
        start = instant - timedelta(hours=8)
        params_base = {
            "start": start.isoformat(),
            "end": instant.isoformat(),
            "limit": "1",
            "sort": "desc",
        }
        errors: list[str] = []
        with httpx.Client(timeout=self.timeout, headers=self._headers()) as client:
            # Historical SIP is available once sufficiently delayed on Basic;
            # IEX remains a usable fallback.
            for feed in ("sip", "iex"):
                try:
                    payload = self._get(
                        client,
                        f"{DATA_URL}/v2/stocks/{ticker}/trades",
                        params={**params_base, "feed": feed},
                    )
                except AlpacaError as exc:
                    errors.append(f"{feed}: {exc}")
                    continue
                trades = payload.get("trades")
                if not isinstance(trades, list):
                    errors.append(f"{feed}: unexpected trades shape")
                    continue
                for trade in trades:
                    if not isinstance(trade, dict):
                        continue
                    price = trade.get("p")
                    trade_time = _parse_ts(trade.get("t"))
                    if (
                        price is not None
                        and float(price) > 0
                        and trade_time is not None
                        and trade_time <= instant
                    ):
                        return SpotQuote(
                            symbol=ticker,
                            price=float(price),
                            as_of=trade_time,
                            source=f"alpaca/{feed}/historical-trade",
                        )
                errors.append(f"{feed}: no trade before option quote")
        detail = "; ".join(errors) if errors else "unknown"
        raise AlpacaError(
            f"no {ticker} spot at {instant.isoformat()} ({detail})"
        )

    def get_market_clock(self) -> MarketClock:
        payload = self._trading_get("/v2/clock")
        return parse_clock(payload)

    def get_market_calendar(
        self,
        start: date,
        end: date,
    ) -> list[MarketSession]:
        key = f"{start.isoformat()}:{end.isoformat()}"
        cached = _calendar_cache.get(key)
        now = datetime.now(timezone.utc)
        if cached is not None and now - cached[0] <= _CALENDAR_TTL:
            return cached[1]
        payload = self._trading_get(
            "/v2/calendar",
            params={"start": start.isoformat(), "end": end.isoformat()},
        )
        rows = payload if isinstance(payload, list) else payload.get("calendar") or []
        if not isinstance(rows, list):
            raise AlpacaError("unexpected alpaca calendar shape")
        sessions = parse_calendar_days(rows)
        _calendar_cache[key] = (now, sessions)
        return sessions

    def get_completed_calendar_window(
        self,
        timestamp: datetime,
    ) -> list[MarketSession]:
        instant = timestamp.astimezone(timezone.utc)
        start = instant.date() - timedelta(days=CALENDAR_LOOKBACK_DAYS)
        end = instant.date() + timedelta(days=CALENDAR_LOOKAHEAD_DAYS)
        return self.get_market_calendar(start, end)

    def get_daily_closes(
        self,
        symbols: list[str],
        session_date: date,
    ) -> dict[str, DailyClose]:
        uniq = sorted({item.strip().upper() for item in symbols if item})
        if not uniq:
            return {}
        start = session_date.isoformat()
        end = (session_date + timedelta(days=1)).isoformat()
        errors: list[str] = []
        with httpx.Client(timeout=self.timeout, headers=self._headers()) as client:
            for feed in ("sip", "iex"):
                try:
                    payload = self._get(
                        client,
                        f"{DATA_URL}/v2/stocks/bars",
                        params={
                            "symbols": ",".join(uniq),
                            "timeframe": "1Day",
                            "start": start,
                            "end": end,
                            "limit": "1000",
                            "adjustment": "all",
                            "feed": feed,
                        },
                    )
                except AlpacaError as exc:
                    errors.append(f"{feed}: {exc}")
                    continue
                bars = payload.get("bars")
                if not isinstance(bars, dict):
                    errors.append(f"{feed}: unexpected bars shape")
                    continue
                out: dict[str, DailyClose] = {}
                for symbol in uniq:
                    series = bars.get(symbol)
                    if not isinstance(series, list) or not series:
                        continue
                    bar = series[-1]
                    if not isinstance(bar, dict) or bar.get("c") is None:
                        continue
                    close = float(bar["c"])
                    if close <= 0:
                        continue
                    out[symbol] = DailyClose(
                        symbol=symbol,
                        session_date=session_date,
                        close=close,
                        source=f"alpaca/{feed}/1Day",
                        as_of=_parse_ts(bar.get("t")),
                    )
                if out:
                    return out
                errors.append(f"{feed}: no daily close for {','.join(uniq)}")
        detail = "; ".join(errors) if errors else "unknown"
        raise AlpacaError(
            f"no official daily close for {','.join(uniq)} "
            f"on {session_date.isoformat()} ({detail})"
        )

    def get_daily_closes_range(
        self,
        symbols: list[str],
        start: date,
        end: date,
    ) -> dict[str, dict[date, DailyClose]]:
        uniq = sorted({item.strip().upper() for item in symbols if item})
        if not uniq or end < start:
            return {}
        errors: list[str] = []
        with httpx.Client(timeout=self.timeout, headers=self._headers()) as client:
            for feed in ("sip", "iex"):
                try:
                    bars = self._paged_daily_bars(client, uniq, start, end, feed)
                except AlpacaError as exc:
                    errors.append(f"{feed}: {exc}")
                    continue
                out: dict[str, dict[date, DailyClose]] = {}
                for symbol, series in bars.items():
                    days: dict[date, DailyClose] = {}
                    for bar in series:
                        if not isinstance(bar, dict) or bar.get("c") is None:
                            continue
                        close = float(bar["c"])
                        if close <= 0:
                            continue
                        session = _bar_session_date(bar.get("t"))
                        if session is None or session < start or session > end:
                            continue
                        days[session] = DailyClose(
                            symbol=symbol,
                            session_date=session,
                            close=close,
                            source=f"alpaca/{feed}/1Day",
                            as_of=_parse_ts(bar.get("t")),
                        )
                    if days:
                        out[symbol] = days
                if out:
                    return out
                errors.append(f"{feed}: no daily closes for {','.join(uniq)}")
        detail = "; ".join(errors) if errors else "unknown"
        raise AlpacaError(
            f"no official daily closes for {','.join(uniq)} "
            f"{start.isoformat()}..{end.isoformat()} ({detail})"
        )

    def _paged_daily_bars(
        self,
        client: httpx.Client,
        symbols: list[str],
        start: date,
        end: date,
        feed: str,
    ) -> dict[str, list[dict[str, Any]]]:
        collected: dict[str, list[dict[str, Any]]] = {symbol: [] for symbol in symbols}
        page_token: str | None = None
        while True:
            params = {
                "symbols": ",".join(symbols),
                "timeframe": "1Day",
                "start": start.isoformat(),
                "end": (end + timedelta(days=1)).isoformat(),
                "limit": "10000",
                "adjustment": "all",
                "feed": feed,
            }
            if page_token:
                params["page_token"] = page_token
            payload = self._get(
                client,
                f"{DATA_URL}/v2/stocks/bars",
                params=params,
            )
            bars = payload.get("bars")
            if not isinstance(bars, dict):
                raise AlpacaError(f"{feed}: unexpected bars shape")
            for symbol in symbols:
                series = bars.get(symbol)
                if isinstance(series, list):
                    collected[symbol].extend(
                        item for item in series if isinstance(item, dict)
                    )
            next_token = payload.get("next_page_token")
            if not next_token:
                return collected
            page_token = str(next_token)

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
        """Prefer IV implied from option mid; fall back to vendor IV field.

        Invert against the underlying print at the option quote time, not the
        live overnight/pre-market snapshot — same pairing as refresh-iv.
        """
        symbol = occ_symbol.strip().upper()
        parts = parse_occ(symbol)
        mode = self.iv_mode or load_config(self.home).iv_mode
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
                latest = node.get("latestQuote") or node.get("latestTrade")
                if isinstance(latest, dict):
                    as_of = _parse_ts(latest.get("t") or latest.get("timestamp"))

                mid = quote_mid(node)
                if mode in {"mid", "auto"} and mid is not None:
                    spot_q = self._spot_for_option_quote(parts.underlying, as_of)
                    if spot_q is not None:
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

    def _spot_for_option_quote(
        self,
        underlying: str,
        quote_time: datetime | None,
    ) -> SpotQuote | None:
        if quote_time is not None:
            try:
                return self.get_spot_at(underlying, quote_time)
            except Exception:  # noqa: BLE001
                pass
        spots = self.get_spots([underlying])
        return spots.get(underlying)

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

    def _trading_get(
        self,
        path: str,
        params: dict[str, str] | None = None,
    ) -> Any:
        global _trading_host
        hosts = [_trading_host] if _trading_host else list(_TRADING_HOSTS)
        errors: list[str] = []
        with httpx.Client(timeout=self.timeout, headers=self._headers()) as client:
            for host in hosts:
                if not host:
                    continue
                try:
                    payload = self._request(client, f"{host}{path}", params)
                except AlpacaError as exc:
                    errors.append(f"{host}: {exc}")
                    continue
                _trading_host = host
                return payload
        detail = "; ".join(errors) if errors else "unknown"
        raise AlpacaError(f"trading API unavailable ({detail})")

    @staticmethod
    def _request(
        client: httpx.Client,
        url: str,
        params: dict[str, str] | None = None,
    ) -> Any:
        response = client.get(url, params=params)
        if response.status_code >= 400:
            raise AlpacaError(f"alpaca HTTP {response.status_code}: {response.text[:200]}")
        return response.json()

    @staticmethod
    def _get(client: httpx.Client, url: str, params: dict[str, str] | None = None) -> dict:
        payload = AlpacaClient._request(client, url, params)
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


def _bar_session_date(value: Any) -> date | None:
    instant = _parse_ts(value)
    if instant is None:
        return None
    if instant.tzinfo is None:
        instant = instant.replace(tzinfo=timezone.utc)
    return instant.astimezone(ZoneInfo("America/New_York")).date()
