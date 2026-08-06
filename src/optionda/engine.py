from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from optionda.config import dividend_for_symbol, load_config, rate_for_days
from optionda.market.router import MarketRouter
from optionda.models import Account, Position, RowMark
from optionda.pricing.bs import price_option, years_to_expiry
from optionda.pricing.surface import (
    MAX_CALIBRATION_QUOTE_AGE,
    IvSurface,
    build_surface,
    is_surface_fresh,
    load_surface,
    save_surface,
    estimate_overnight_iv,
    sticky_delta_iv,
)

ProgressCallback = Callable[[str, int, int], None]


@dataclass
class CalibrationResult:
    surfaces: dict[str, IvSurface] = field(default_factory=dict)
    errors: dict[str, str] = field(default_factory=dict)


def calibrate_surfaces(
    account: Account,
    *,
    home: Path | None = None,
    router: MarketRouter | None = None,
    now: datetime | None = None,
    max_quote_age: timedelta | None = None,
    on_progress: ProgressCallback | None = None,
) -> CalibrationResult:
    """Build and persist one vendor-IV smile surface per held underlying.

    Failures are per-underlying: one dead ticker does not abort the rest.
    Chain fetches are slow; ``on_progress(label, done, total)`` reports each step.
    """
    market = router or MarketRouter(home)
    cfg = load_config(home)
    current = now or datetime.now(timezone.utc)
    age = max_quote_age if max_quote_age is not None else MAX_CALIBRATION_QUOTE_AGE
    underlyings = sorted({position.underlying for position in account.positions})
    total = max(len(underlyings), 1)

    def report(label: str, done: int) -> None:
        if on_progress is not None:
            on_progress(label, done, total)

    report("spots…", 0)
    spots = market.get_spots(underlyings)
    result = CalibrationResult()
    for index, underlying in enumerate(underlyings):
        report(f"{underlying} chain…", index)
        spot_q = spots.get(underlying)
        if spot_q is None:
            result.errors[underlying] = "no spot"
            report(f"{underlying} skip", index + 1)
            continue
        try:
            snapshots = market.get_option_chain_snapshots(underlying)
            surface = build_surface(
                underlying,
                spot=spot_q.price,
                snapshots=snapshots,
                as_of=current,
                source=f"{market.feed_name}/chain",
                max_quote_age=age,
                rate=lambda days: rate_for_days(cfg, days),
                dividend=lambda symbol: dividend_for_symbol(cfg, symbol),
                style=cfg.option_style,
            )
            save_surface(surface, home)
            result.surfaces[underlying] = surface
            report(f"{underlying} ok", index + 1)
        except Exception as exc:  # noqa: BLE001
            result.errors[underlying] = str(exc)
            report(f"{underlying} skip", index + 1)
    if on_progress is not None:
        on_progress("done", total, total)
    return result


def apply_surface_reference_ivs(
    positions: list[Position],
    surfaces: dict[str, IvSurface],
    *,
    spots: dict[str, float],
    rate: float | Callable[[float], float],
    dividend: float | Callable[[str], float],
    now: datetime,
) -> list[Position]:
    """Update fallback IVs from newly calibrated surfaces where possible."""
    refreshed: list[Position] = []
    for position in positions:
        surface = surfaces.get(position.underlying)
        spot = spots.get(position.underlying)
        if surface is None or spot is None:
            refreshed.append(position)
            continue
        years = years_to_expiry(position.expiry, now)
        position_rate = rate(years * 365.0) if callable(rate) else rate
        position_dividend = (
            dividend(position.underlying) if callable(dividend) else dividend
        )
        iv = sticky_delta_iv(
            surface,
            position,
            spot=spot,
            years=years,
            rate=position_rate,
            dividend=position_dividend,
        )
        if iv is None:
            refreshed.append(position)
            continue
        refreshed.append(
            position.model_copy(
                update={
                    "iv_frozen": iv,
                    "iv_as_of": surface.as_of,
                    "iv_source": f"surface/{surface.source}",
                }
            )
        )
    return refreshed


def mark_account(
    account: Account,
    *,
    home: Path | None = None,
    router: MarketRouter | None = None,
    now: datetime | None = None,
    on_progress: ProgressCallback | None = None,
) -> list[RowMark]:
    cfg = load_config(home)
    market = router or MarketRouter(home)
    underlyings = [p.underlying for p in account.positions]
    n_pos = len(account.positions)
    # spots fetch + one step per position (live mid + BS)
    total_steps = (1 if underlyings else 0) + max(n_pos, 0)
    done = 0

    def report(label: str) -> None:
        if on_progress is not None:
            on_progress(label, done, max(total_steps, 1))

    uniq = sorted(set(underlyings))
    report(
        "fetching spots · " + (" ".join(uniq[:6]) + ("…" if len(uniq) > 6 else ""))
        if uniq
        else "no positions"
    )
    spots = market.get_spots(underlyings) if underlyings else {}
    if underlyings:
        done = 1
        report("spots ready · loading IV surfaces")

    current = now or datetime.now(timezone.utc)
    rows: list[RowMark] = []
    surfaces = {
        underlying: load_surface(underlying, home)
        for underlying in set(underlyings)
    }

    for index, pos in enumerate(account.positions, start=1):
        report(f"marking {index}/{n_pos}  {pos.occ_symbol}")
        spot_q = spots.get(pos.underlying)
        live = None
        try:
            live = market.get_option_mid(pos.occ_symbol)
        except Exception:  # noqa: BLE001
            live = None

        if spot_q is None:
            rows.append(
                RowMark(
                    position=pos,
                    spot=None,
                    theo=None,
                    delta=None,
                    dte=None,
                    notional=None,
                    live=live,
                    error="no spot",
                )
            )
            done += 1
            continue
        try:
            t = years_to_expiry(pos.expiry, current)
            rate = rate_for_days(cfg, t * 365.0)
            dividend = dividend_for_symbol(cfg, pos.underlying)
            candidate_surface = surfaces.get(pos.underlying)
            surface = (
                candidate_surface
                if candidate_surface is not None
                and is_surface_fresh(candidate_surface, current)
                else None
            )
            surface_iv = (
                sticky_delta_iv(
                    surface,
                    pos,
                    spot=spot_q.price,
                    years=t,
                    rate=rate,
                    dividend=dividend,
                )
                if surface is not None
                else None
            )
            estimate = (
                estimate_overnight_iv(
                    surface,
                    pos,
                    spot=spot_q.price,
                    years=t,
                    rate=rate,
                    dividend=dividend,
                    sticky_delta_weight=(
                        1.0 if cfg.overnight_iv_mode == "sticky_delta"
                        else 0.0 if cfg.overnight_iv_mode == "sticky_strike"
                        else cfg.sticky_delta_weight
                    ),
                )
                if surface is not None
                else None
            )
            surface_iv = estimate.base if estimate is not None else surface_iv
            model_iv = estimate.base if estimate is not None else pos.iv_frozen
            valuation_mode = "surface" if estimate is not None else "frozen"
            result = price_option(
                spot=spot_q.price,
                strike=pos.strike,
                years=t,
                iv=model_iv,
                rate=rate,
                dividend=dividend,
                option_type=pos.option_type,
                style=cfg.option_style,
            )
            low_result = (
                price_option(
                    spot=spot_q.price,
                    strike=pos.strike,
                    years=t,
                    iv=estimate.low,
                    rate=rate,
                    dividend=dividend,
                    option_type=pos.option_type,
                    style=cfg.option_style,
                    greeks=False,
                )
                if estimate is not None
                else None
            )
            high_result = (
                price_option(
                    spot=spot_q.price,
                    strike=pos.strike,
                    years=t,
                    iv=estimate.high,
                    rate=rate,
                    dividend=dividend,
                    option_type=pos.option_type,
                    style=cfg.option_style,
                    greeks=False,
                )
                if estimate is not None
                else None
            )
            strike_result = (
                price_option(
                    spot=spot_q.price,
                    strike=pos.strike,
                    years=t,
                    iv=estimate.sticky_strike,
                    rate=rate,
                    dividend=dividend,
                    option_type=pos.option_type,
                    style=cfg.option_style,
                    greeks=False,
                )
                if estimate is not None
                else None
            )
            delta_result = (
                price_option(
                    spot=spot_q.price,
                    strike=pos.strike,
                    years=t,
                    iv=estimate.sticky_delta,
                    rate=rate,
                    dividend=dividend,
                    option_type=pos.option_type,
                    style=cfg.option_style,
                    greeks=False,
                )
                if estimate is not None and estimate.sticky_delta is not None
                else None
            )
            sign = 1.0 if pos.side == "long" else -1.0
            notional = result.price * pos.multiplier * pos.qty * sign
            cost = pos.entry_premium
            upnl = None
            if cost is not None:
                upnl = (result.price - cost) * pos.multiplier * pos.qty * sign
            rows.append(
                RowMark(
                    position=pos,
                    spot=spot_q.price,
                    theo=result.price,
                    delta=result.delta * sign,
                    dte=result.dte,
                    notional=notional,
                    cost=cost,
                    upnl=upnl,
                    live=live,
                    valuation_mode=valuation_mode,
                    surface_iv=surface_iv,
                    surface_as_of=surface.as_of if surface_iv is not None else None,
                    surface_source=surface.source if surface_iv is not None else None,
                    model_low=low_result.price if low_result is not None else None,
                    model_high=high_result.price if high_result is not None else None,
                    iv_dynamics=estimate.method if estimate is not None else "frozen",
                    sticky_strike_iv=(
                        estimate.sticky_strike if estimate is not None else None
                    ),
                    sticky_delta_iv=(
                        estimate.sticky_delta if estimate is not None else None
                    ),
                    sticky_strike_model=(
                        strike_result.price if strike_result is not None else None
                    ),
                    sticky_delta_model=(
                        delta_result.price if delta_result is not None else None
                    ),
                    rate_used=rate,
                    dividend_used=dividend,
                    spot_as_of=spot_q.as_of,
                    spot_source=spot_q.source,
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
                    live=live,
                    error=str(exc),
                )
            )
        done += 1
    if on_progress is not None:
        on_progress("done", max(total_steps, 1), max(total_steps, 1))
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
            update={
                "iv_frozen": iv,
                "iv_as_of": datetime.now(timezone.utc),
                "iv_source": "manual",
            }
        )
    market = router or MarketRouter(home)
    quote = market.get_option_iv(position.occ_symbol)
    return position.model_copy(
        update={
            "iv_frozen": quote.iv,
            "iv_as_of": quote.as_of or datetime.now(timezone.utc),
            "iv_source": quote.source,
        }
    )
