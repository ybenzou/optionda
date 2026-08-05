from __future__ import annotations

from datetime import datetime, timezone

import yfinance as yf

from optionda.models import OptionIvQuote, SpotQuote
from optionda.occ import parse_occ


class YahooError(RuntimeError):
    pass


class YahooClient:
    source = "yahoo"

    def get_spots(self, symbols: list[str]) -> dict[str, SpotQuote]:
        out: dict[str, SpotQuote] = {}
        for symbol in sorted(set(s.upper() for s in symbols)):
            ticker = yf.Ticker(symbol)
            price = self._spot_from_ticker(ticker)
            if price is None:
                continue
            out[symbol] = SpotQuote(
                symbol=symbol,
                price=price,
                as_of=datetime.now(timezone.utc),
                source=self.source,
            )
        return out

    def get_option_iv(self, occ_symbol: str) -> OptionIvQuote:
        parts = parse_occ(occ_symbol)
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
            # fallback nearest strike
            rows = frame[abs(frame["strike"] - parts.strike) < 1e-6]
        if rows.empty:
            raise YahooError(f"contract not found on yahoo: {occ_symbol}")
        iv = float(rows.iloc[0]["impliedVolatility"])
        if iv <= 0 or iv != iv:  # NaN check
            raise YahooError(f"invalid IV from yahoo for {occ_symbol}: {iv}")
        return OptionIvQuote(
            occ_symbol=parts.occ_symbol,
            iv=iv,
            as_of=datetime.now(timezone.utc),
            source=self.source,
        )

    @staticmethod
    def _spot_from_ticker(ticker: yf.Ticker) -> float | None:
        try:
            fast = getattr(ticker, "fast_info", None)
            if fast is not None:
                for key in ("last_price", "lastPrice", "regular_market_price"):
                    value = None
                    if isinstance(fast, dict):
                        value = fast.get(key)
                    else:
                        value = getattr(fast, key, None)
                    if value is not None and float(value) > 0:
                        return float(value)
        except Exception:  # noqa: BLE001
            pass
        try:
            hist = ticker.history(period="1d")
            if hist is not None and not hist.empty:
                return float(hist["Close"].iloc[-1])
        except Exception:  # noqa: BLE001
            pass
        return None
