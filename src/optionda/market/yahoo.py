from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yfinance as yf

from optionda.config import load_config
from optionda.market.iv_mid import imply_iv_from_premium
from optionda.models import IvMode, OptionIvQuote, SpotQuote
from optionda.occ import parse_occ


class YahooError(RuntimeError):
    pass


class YahooClient:
    source = "yahoo"

    def __init__(
        self,
        *,
        iv_mode: IvMode = "mid",
        home: Path | None = None,
    ) -> None:
        self.iv_mode = iv_mode
        self.home = home

    def get_spots(self, symbols: list[str]) -> dict[str, SpotQuote]:
        out: dict[str, SpotQuote] = {}
        for symbol in sorted(set(s.upper() for s in symbols)):
            ticker = yf.Ticker(symbol)
            quote = self._spot_quote_from_ticker(symbol, ticker)
            if quote is not None:
                out[symbol] = quote
        return out

    def get_option_iv(self, occ_symbol: str) -> OptionIvQuote:
        parts = parse_occ(occ_symbol)
        mode = self.iv_mode or load_config(self.home).iv_mode
        ticker = yf.Ticker(parts.underlying)
        expiry_str = parts.expiry.isoformat()
        try:
            chain = ticker.option_chain(expiry_str)
        except Exception as exc:  # noqa: BLE001 - yfinance raises varied types
            raise YahooError(f"yahoo option chain failed for {occ_symbol}: {exc}") from exc
        frame = chain.calls if parts.option_type == "call" else chain.puts
        if frame is None or frame.empty:
            raise YahooError(f"empty yahoo chain for {occ_symbol}")
        rows = frame[frame["contractSymbol"].astype(str).str.upper() == parts.occ_symbol]
        if rows.empty:
            rows = frame[abs(frame["strike"] - parts.strike) < 1e-6]
        if rows.empty:
            raise YahooError(f"contract not found on yahoo: {occ_symbol}")
        row = rows.iloc[0]

        spot_q = self._spot_quote_from_ticker(parts.underlying, ticker)
        spot = spot_q.price if spot_q else None
        mid = _yahoo_premium(row)
        errors: list[str] = []
        if mode in {"mid", "auto"} and mid is not None and spot is not None:
            try:
                return imply_iv_from_premium(
                    parts.occ_symbol,
                    spot,
                    mid,
                    home=self.home,
                    source="yahoo+mid",
                )
            except Exception as exc:  # noqa: BLE001
                errors.append(str(exc))

        if mode in {"vendor", "auto", "mid"}:
            try:
                iv = float(row["impliedVolatility"])
            except Exception as exc:  # noqa: BLE001
                raise YahooError(
                    f"invalid IV from yahoo for {occ_symbol}: {exc}; mid errors={errors}"
                ) from exc
            if iv <= 0 or iv != iv:
                raise YahooError(f"invalid IV from yahoo for {occ_symbol}: {iv}")
            if iv > 5.0:
                iv = iv / 100.0
            return OptionIvQuote(
                occ_symbol=parts.occ_symbol,
                iv=iv,
                as_of=datetime.now(timezone.utc),
                source=self.source,
            )
        raise YahooError(
            f"could not imply IV for {occ_symbol}: {'; '.join(errors) or 'no premium'}"
        )

    def get_option_mid(self, occ_symbol: str) -> float | None:
        parts = parse_occ(occ_symbol)
        ticker = yf.Ticker(parts.underlying)
        try:
            chain = ticker.option_chain(parts.expiry.isoformat())
        except Exception:  # noqa: BLE001
            return None
        frame = chain.calls if parts.option_type == "call" else chain.puts
        if frame is None or frame.empty:
            return None
        rows = frame[frame["contractSymbol"].astype(str).str.upper() == parts.occ_symbol]
        if rows.empty:
            rows = frame[abs(frame["strike"] - parts.strike) < 1e-6]
        if rows.empty:
            return None
        return _yahoo_premium(rows.iloc[0])

    def _spot_quote_from_ticker(self, symbol: str, ticker: yf.Ticker) -> SpotQuote | None:
        """Prefer post/pre-market when Yahoo has a newer extended print."""
        candidates: list[tuple[datetime, float, str]] = []
        info: dict[str, Any] = {}
        try:
            info = ticker.info or {}
        except Exception:  # noqa: BLE001
            info = {}

        for price_key, time_key, src in (
            ("postMarketPrice", "postMarketTime", "yahoo+post"),
            ("preMarketPrice", "preMarketTime", "yahoo+pre"),
            ("regularMarketPrice", "regularMarketTime", "yahoo"),
        ):
            price = info.get(price_key)
            ts = info.get(time_key)
            try:
                if price is None or float(price) <= 0:
                    continue
                as_of = _epoch_to_dt(ts) or datetime.now(timezone.utc)
                candidates.append((as_of, float(price), src))
            except (TypeError, ValueError):
                continue

        # fast_info has no reliable timestamp — only use if no info prices exist.
        # Never stamp it with "now", or it always beats real post/pre times.
        if not candidates:
            try:
                fast_price = self._spot_from_fast_info(ticker)
                if fast_price is not None:
                    reg_t = _epoch_to_dt(info.get("regularMarketTime"))
                    candidates.append(
                        (
                            reg_t or datetime.now(timezone.utc),
                            fast_price,
                            "yahoo",
                        )
                    )
            except Exception:  # noqa: BLE001
                pass

        if not candidates:
            hist_price = self._spot_from_history(ticker)
            if hist_price is None:
                return None
            return SpotQuote(
                symbol=symbol,
                price=hist_price,
                as_of=datetime.now(timezone.utc),
                source=self.source,
            )

        # Prefer explicit post/pre when present; else newest regular timestamp.
        extended = [c for c in candidates if c[2] in {"yahoo+post", "yahoo+pre"}]
        pool = extended or candidates
        pool.sort(key=lambda c: c[0], reverse=True)
        as_of, price, src = pool[0]
        return SpotQuote(symbol=symbol, price=price, as_of=as_of, source=src)

    @staticmethod
    def _spot_from_fast_info(ticker: yf.Ticker) -> float | None:
        try:
            fast = getattr(ticker, "fast_info", None)
            if fast is None:
                return None
            for key in ("last_price", "lastPrice", "regular_market_price"):
                value = fast.get(key) if isinstance(fast, dict) else getattr(fast, key, None)
                if value is not None and float(value) > 0:
                    return float(value)
        except Exception:  # noqa: BLE001
            return None
        return None

    @staticmethod
    def _spot_from_history(ticker: yf.Ticker) -> float | None:
        try:
            hist = ticker.history(period="1d")
            if hist is not None and not hist.empty:
                return float(hist["Close"].iloc[-1])
        except Exception:  # noqa: BLE001
            pass
        return None


def _epoch_to_dt(value: Any) -> datetime | None:
    if value is None:
        return None
    try:
        ts = float(value)
        # yfinance sometimes returns ms
        if ts > 1e12:
            ts /= 1000.0
        return datetime.fromtimestamp(ts, tz=timezone.utc)
    except (TypeError, ValueError, OSError):
        return None


def _yahoo_premium(row) -> float | None:
    bid = _cell(row, "bid")
    ask = _cell(row, "ask")
    if bid is not None and ask is not None and bid > 0 and ask > 0:
        return 0.5 * (bid + ask)
    last = _cell(row, "lastPrice")
    if last is not None and last > 0:
        return last
    if ask is not None and ask > 0:
        return ask
    if bid is not None and bid > 0:
        return bid
    return None


def _cell(row, key: str) -> float | None:
    try:
        if key not in row.index:
            return None
        value = float(row[key])
        if value != value:
            return None
        return value
    except Exception:  # noqa: BLE001
        return None
