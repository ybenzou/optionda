from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from optionda.config import load_config
from optionda.market.router import MarketRouter
from optionda.models import Account, Position, RowMark
from optionda.pricing.bs import black_scholes, years_to_expiry


def mark_account(
    account: Account,
    *,
    home: Path | None = None,
    router: MarketRouter | None = None,
    now: datetime | None = None,
) -> list[RowMark]:
    cfg = load_config(home)
    market = router or MarketRouter(home)
    underlyings = [p.underlying for p in account.positions]
    spots = market.get_spots(underlyings) if underlyings else {}
    current = now or datetime.now(timezone.utc)
    rows: list[RowMark] = []

    for pos in account.positions:
        spot_q = spots.get(pos.underlying)
        if spot_q is None:
            rows.append(
                RowMark(
                    position=pos,
                    spot=None,
                    theo=None,
                    delta=None,
                    dte=None,
                    notional=None,
                    error="no spot",
                )
            )
            continue
        try:
            t = years_to_expiry(pos.expiry, current)
            result = black_scholes(
                spot=spot_q.price,
                strike=pos.strike,
                years=t,
                iv=pos.iv_frozen,
                rate=cfg.r,
                dividend=cfg.q,
                option_type=pos.option_type,
            )
            sign = 1.0 if pos.side == "long" else -1.0
            notional = result.price * pos.multiplier * pos.qty * sign
            rows.append(
                RowMark(
                    position=pos,
                    spot=spot_q.price,
                    theo=result.price,
                    delta=result.delta * sign,
                    dte=result.dte,
                    notional=notional,
                )
            )
        except Exception as exc:  # noqa: BLE001
            rows.append(
                RowMark(
                    position=pos,
                    spot=spot_q.price,
                    theo=None,
                    delta=None,
                    dte=None,
                    notional=None,
                    error=str(exc),
                )
            )
    return rows


def freeze_iv_for_position(
    position: Position,
    *,
    iv: float | None,
    home: Path | None = None,
    router: MarketRouter | None = None,
) -> Position:
    if iv is not None:
        return position.model_copy(
            update={"iv_frozen": iv, "iv_as_of": datetime.now(timezone.utc)}
        )
    market = router or MarketRouter(home)
    quote = market.get_option_iv(position.occ_symbol)
    return position.model_copy(
        update={
            "iv_frozen": quote.iv,
            "iv_as_of": quote.as_of or datetime.now(timezone.utc),
        }
    )
