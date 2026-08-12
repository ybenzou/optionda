from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from optionda.config import load_config
from optionda.credentials import has_alpaca, load_alpaca
from optionda.market.alpaca import AlpacaClient
from optionda.market.yahoo import YahooClient
from optionda.models import OptionIvQuote, SpotQuote


class MarketDataError(RuntimeError):
    pass


class MarketRouter:
    def __init__(self, home: Path | None = None) -> None:
        self.home = home
        cfg = load_config(home)
        self._yahoo = YahooClient(iv_mode=cfg.iv_mode, home=home)
        self._alpaca: AlpacaClient | None = None
        creds = load_alpaca(home)
        if creds is not None:
            self._alpaca = AlpacaClient(
                creds,
                options_feed=cfg.alpaca_options_feed,
                iv_mode=cfg.iv_mode,
                home=home,
            )

    @property
    def feed_name(self) -> str:
        cfg = load_config(self.home)
        if cfg.feed == "alpaca" and self._alpaca is not None:
            return "alpaca"
        if self._alpaca is not None and has_alpaca(self.home):
            return "alpaca"
        return "yahoo"

    def get_spots(self, symbols: list[str]) -> dict[str, SpotQuote]:
        """Resolve spots; merge Yahoo pre/post when newer than primary feed."""
        primary: dict[str, SpotQuote] = {}
        if self.feed_name == "alpaca" and self._alpaca is not None:
            try:
                primary = self._alpaca.get_spots(symbols)
            except Exception:
                primary = {}
        if not primary:
            return self._yahoo.get_spots(symbols)

        try:
            yahoo = self._yahoo.get_spots(symbols)
        except Exception:
            return primary

        out = dict(primary)
        for symbol, yq in yahoo.items():
            pq = out.get(symbol)
            if pq is None:
                out[symbol] = yq
                continue
            if _is_newer(yq.as_of, pq.as_of) and yq.source in {
                "yahoo+post",
                "yahoo+pre",
            }:
                out[symbol] = yq
            elif yq.source in {"yahoo+post", "yahoo+pre"} and _is_stale(pq.as_of):
                # Primary stuck on last RTH print overnight → prefer Yahoo AH
                out[symbol] = yq
        return out

    def get_spot_at(self, symbol: str, as_of: datetime) -> SpotQuote:
        """Underlying print contemporaneous with a frozen option quote."""
        if self.feed_name == "alpaca" and self._alpaca is not None:
            return self._alpaca.get_spot_at(symbol, as_of)
        raise MarketDataError(
            "historical spot alignment requires Alpaca market data"
        )

    def get_option_iv(self, occ_symbol: str) -> OptionIvQuote:
        if self.feed_name == "alpaca" and self._alpaca is not None:
            try:
                return self._alpaca.get_option_iv(occ_symbol)
            except Exception:
                return self._yahoo.get_option_iv(occ_symbol)
        return self._yahoo.get_option_iv(occ_symbol)

    def get_option_mid(self, occ_symbol: str) -> float | None:
        if self.feed_name == "alpaca" and self._alpaca is not None:
            try:
                mid = self._alpaca.get_option_mid(occ_symbol)
                if mid is not None:
                    return mid
            except Exception:
                pass
        try:
            return self._yahoo.get_option_mid(occ_symbol)
        except Exception:
            return None

    def get_option_chain_snapshots(self, underlying: str) -> dict:
        if self.feed_name == "alpaca" and self._alpaca is not None:
            return self._alpaca.get_option_chain_snapshots(underlying)
        raise MarketDataError(
            "surface calibration requires Alpaca option chain snapshots"
        )


def _is_newer(a: datetime | None, b: datetime | None) -> bool:
    if a is None:
        return False
    if b is None:
        return True
    if a.tzinfo is None:
        a = a.replace(tzinfo=timezone.utc)
    if b.tzinfo is None:
        b = b.replace(tzinfo=timezone.utc)
    return a > b


def _is_stale(as_of: datetime | None, *, hours: float = 6.0) -> bool:
    if as_of is None:
        return True
    if as_of.tzinfo is None:
        as_of = as_of.replace(tzinfo=timezone.utc)
    age = datetime.now(timezone.utc) - as_of
    return age.total_seconds() > hours * 3600


def resolve_poll_interval(home: Path | None = None) -> int:
    cfg = load_config(home)
    if has_alpaca(home) or cfg.feed == "alpaca":
        return 15
    return cfg.poll_interval_sec or 60
