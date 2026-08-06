from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from optionda.config import load_config
from optionda.models import OptionIvQuote
from optionda.occ import parse_occ
from optionda.pricing.bs import implied_volatility, years_to_expiry


def quote_mid(node: dict[str, Any]) -> float | None:
    """Best available option premium: quote mid, else last trade."""
    quote = node.get("latestQuote") or node.get("quote") or {}
    if isinstance(quote, dict):
        bid = quote.get("bp") if quote.get("bp") is not None else quote.get("bid")
        ask = quote.get("ap") if quote.get("ap") is not None else quote.get("ask")
        try:
            if bid is not None and ask is not None:
                b, a = float(bid), float(ask)
                if b > 0 and a > 0:
                    return 0.5 * (b + a)
            if ask is not None and float(ask) > 0:
                return float(ask)
            if bid is not None and float(bid) > 0:
                return float(bid)
        except (TypeError, ValueError):
            pass
    trade = node.get("latestTrade") or node.get("trade") or {}
    if isinstance(trade, dict):
        price = trade.get("p") if trade.get("p") is not None else trade.get("price")
        try:
            if price is not None and float(price) > 0:
                return float(price)
        except (TypeError, ValueError):
            pass
    return None


def imply_iv_from_premium(
    occ_symbol: str,
    spot: float,
    premium: float,
    *,
    home=None,
    now: datetime | None = None,
    source: str = "mid",
) -> OptionIvQuote:
    parts = parse_occ(occ_symbol)
    cfg = load_config(home)
    current = now or datetime.now(timezone.utc)
    t = years_to_expiry(parts.expiry, current)
    iv = implied_volatility(
        spot=spot,
        strike=parts.strike,
        years=t,
        price=premium,
        rate=cfg.r,
        dividend=cfg.q,
        option_type=parts.option_type,
        style=cfg.option_style,
    )
    return OptionIvQuote(
        occ_symbol=parts.occ_symbol,
        iv=iv,
        as_of=current,
        source=source,
    )
