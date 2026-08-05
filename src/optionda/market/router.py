from __future__ import annotations

from pathlib import Path

from optionda.config import load_config
from optionda.credentials import has_alpaca, load_alpaca
from optionda.market.alpaca import AlpacaClient
from optionda.market.yahoo import YahooClient
from optionda.models import OptionIvQuote, SpotQuote


class MarketRouter:
    def __init__(self, home: Path | None = None) -> None:
        self.home = home
        self._yahoo = YahooClient()
        self._alpaca: AlpacaClient | None = None
        creds = load_alpaca(home)
        if creds is not None:
            self._alpaca = AlpacaClient(creds)

    @property
    def feed_name(self) -> str:
        cfg = load_config(self.home)
        if cfg.feed == "alpaca" and self._alpaca is not None:
            return "alpaca"
        if self._alpaca is not None and has_alpaca(self.home):
            return "alpaca"
        return "yahoo"

    def get_spots(self, symbols: list[str]) -> dict[str, SpotQuote]:
        if self.feed_name == "alpaca" and self._alpaca is not None:
            try:
                return self._alpaca.get_spots(symbols)
            except Exception:
                # soft fallback
                return self._yahoo.get_spots(symbols)
        return self._yahoo.get_spots(symbols)

    def get_option_iv(self, occ_symbol: str) -> OptionIvQuote:
        if self.feed_name == "alpaca" and self._alpaca is not None:
            try:
                return self._alpaca.get_option_iv(occ_symbol)
            except Exception:
                return self._yahoo.get_option_iv(occ_symbol)
        return self._yahoo.get_option_iv(occ_symbol)


def resolve_poll_interval(home: Path | None = None) -> int:
    cfg = load_config(home)
    if has_alpaca(home) or cfg.feed == "alpaca":
        return 15
    return cfg.poll_interval_sec or 60
