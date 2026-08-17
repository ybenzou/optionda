from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from collections.abc import Callable
from typing import Any
from zoneinfo import ZoneInfo

from optionda.market.session import (
    MAX_NODE_QUOTE_SKEW,
    MarketSession,
    quote_in_close_window,
)
from optionda.occ import OccError, parse_occ
from optionda.paths import ensure_home
from optionda.pricing.bs import implied_volatility, price_option, years_to_expiry

SURFACE_SCHEMA_VERSION = 3
MAX_QUOTE_SPREAD_RATIO = 0.50
# Default: last-session / close quotes (desk is used mostly outside RTH).
MAX_CALIBRATION_QUOTE_AGE = timedelta(hours=18)
# Strict mode for US RTH (`optionda refresh-iv --fresh`).
FRESH_CALIBRATION_QUOTE_AGE = timedelta(minutes=20)
STICKY_DELTA_TOLERANCE = 1e-5
STICKY_DELTA_MAX_ITERATIONS = 30
MAX_SURFACE_AGE = timedelta(hours=36)


@dataclass(frozen=True)
class SurfaceNode:
    strike: float
    delta: float
    iv: float
    option_type: str = "put"
    bid: float | None = None
    ask: float | None = None
    quote_time: datetime | None = None
    premium: float | None = None
    vendor_iv: float | None = None


@dataclass(frozen=True)
class ExpirySmile:
    expiry: date
    nodes: list[SurfaceNode]


@dataclass(frozen=True)
class IvSurface:
    underlying: str
    spot: float
    as_of: datetime
    source: str
    smiles: list[ExpirySmile]
    quality: dict[str, int]
    schema_version: int = SURFACE_SCHEMA_VERSION
    session_date: date | None = None
    session_close_at: datetime | None = None
    legacy: bool = False

    @property
    def calibration_spot(self) -> float:
        return self.spot

    @property
    def quote_as_of(self) -> datetime:
        return self.as_of


@dataclass(frozen=True)
class OvernightIvEstimate:
    """Transparent frozen-surface scenarios for an after-hours spot move."""

    sticky_strike: float
    sticky_delta: float | None
    base: float
    low: float
    high: float
    sticky_delta_weight: float
    method: str


def surfaces_dir(home: Path | None = None) -> Path:
    root = ensure_home(home)
    path = root / "surfaces"
    path.mkdir(parents=True, exist_ok=True)
    return path


def surface_path(underlying: str, home: Path | None = None) -> Path:
    return surfaces_dir(home) / f"{underlying.strip().upper()}.json"


def build_surface(
    underlying: str,
    *,
    spot: float,
    snapshots: dict[str, dict[str, Any]],
    as_of: datetime,
    source: str,
    quote_as_of: datetime | None = None,
    target_session: MarketSession | None = None,
    max_quote_spread_ratio: float = MAX_QUOTE_SPREAD_RATIO,
    max_quote_age: timedelta = MAX_CALIBRATION_QUOTE_AGE,
    max_node_quote_skew: timedelta = MAX_NODE_QUOTE_SKEW,
    rate: float | Callable[[float], float] = 0.045,
    dividend: float | Callable[[str], float] = 0.0,
    style: str = "american",
) -> IvSurface:
    """Build a model-consistent smile from timestamped option mid quotes."""
    if spot <= 0:
        raise ValueError("surface spot must be > 0")
    symbol = underlying.strip().upper()
    calibration_time = quote_as_of or as_of
    if target_session is not None and not quote_in_close_window(
        calibration_time, target_session
    ):
        raise ValueError(
            f"option quotes at {calibration_time.isoformat()} are outside the "
            f"{target_session.session_date.isoformat()} close window"
        )
    by_expiry: dict[date, list[SurfaceNode]] = {}
    accepted = 0
    rejected = 0

    for occ_symbol, node in snapshots.items():
        try:
            parts = parse_occ(occ_symbol)
        except OccError:
            rejected += 1
            continue
        if parts.underlying != symbol:
            rejected += 1
            continue
        if not _has_usable_quote(
            node,
            max_quote_spread_ratio,
            as_of=calibration_time,
            max_quote_age=min(max_quote_age, max_node_quote_skew),
        ):
            rejected += 1
            continue
        quote = node.get("latestQuote") or node.get("quote") or {}
        bid = float(quote.get("bp", quote.get("bid")))
        ask = float(quote.get("ap", quote.get("ask")))
        premium = 0.5 * (bid + ask)
        vendor_iv = _extract_iv(node)
        years = years_to_expiry(parts.expiry, calibration_time)
        node_rate = rate(years * 365.0) if callable(rate) else rate
        node_dividend = (
            dividend(parts.underlying) if callable(dividend) else dividend
        )
        try:
            # Market IV feeds conventionally quote a Black–Scholes implied
            # volatility.  Keep inversion closed-form/fast for full chains;
            # use the configured American model only for the delta and mark.
            iv = implied_volatility(
                spot=spot,
                strike=parts.strike,
                years=years,
                price=premium,
                rate=node_rate,
                dividend=node_dividend,
                option_type=parts.option_type,
                style="european",
            )
            delta = price_option(
                spot=spot,
                strike=parts.strike,
                years=years,
                iv=iv,
                rate=node_rate,
                dividend=node_dividend,
                option_type=parts.option_type,
                style=style,  # type: ignore[arg-type]
            ).delta
        except (ValueError, OverflowError):
            rejected += 1
            continue
        if iv <= 0 or abs(delta) > 1:
            rejected += 1
            continue
        by_expiry.setdefault(parts.expiry, []).append(
            SurfaceNode(
                strike=parts.strike,
                delta=delta,
                iv=iv,
                option_type=parts.option_type,
                bid=bid,
                ask=ask,
                quote_time=_parse_ts(str(quote.get("t") or quote.get("timestamp"))),
                premium=premium,
                vendor_iv=vendor_iv,
            )
        )
        accepted += 1

    smiles = [
        ExpirySmile(
            expiry=expiry,
            nodes=sorted(nodes, key=lambda item: item.delta),
        )
        for expiry, nodes in sorted(by_expiry.items())
        if nodes
    ]
    if not smiles:
        raise ValueError(f"no usable surface nodes for {symbol}")
    session_date = (
        target_session.session_date
        if target_session is not None
        else last_completed_session_date(_utc(calibration_time))
    )
    session_close_at = (
        target_session.close_at
        if target_session is not None
        else last_completed_close_at(_utc(calibration_time))
    )
    return IvSurface(
        underlying=symbol,
        spot=spot,
        as_of=_utc(calibration_time),
        source=source,
        smiles=smiles,
        quality={"accepted": accepted, "rejected": rejected},
        session_date=session_date,
        session_close_at=session_close_at,
        schema_version=SURFACE_SCHEMA_VERSION,
        legacy=False,
    )


def save_surface(surface: IvSurface, home: Path | None = None) -> Path:
    path = surface_path(surface.underlying, home)
    payload = {
        "schema_version": surface.schema_version,
        "underlying": surface.underlying,
        "spot": surface.spot,
        "calibration_spot": surface.calibration_spot,
        "as_of": surface.as_of.isoformat(),
        "quote_as_of": surface.quote_as_of.isoformat(),
        "source": surface.source,
        "quality": surface.quality,
        "session_date": (
            surface.session_date.isoformat() if surface.session_date else None
        ),
        "session_close_at": (
            surface.session_close_at.isoformat()
            if surface.session_close_at is not None
            else None
        ),
        "legacy": surface.legacy,
        "smiles": [
            {
                "expiry": smile.expiry.isoformat(),
                "nodes": [
                    {
                        "strike": node.strike,
                        "delta": node.delta,
                        "iv": node.iv,
                        "option_type": node.option_type,
                        "bid": node.bid,
                        "ask": node.ask,
                        "quote_time": (
                            node.quote_time.isoformat()
                            if node.quote_time is not None
                            else None
                        ),
                        "premium": node.premium,
                        "vendor_iv": node.vendor_iv,
                    }
                    for node in smile.nodes
                ],
            }
            for smile in surface.smiles
        ],
    }
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)
    return path


def load_surface(underlying: str, home: Path | None = None) -> IvSurface | None:
    path = surface_path(underlying, home)
    if not path.exists():
        return None
    raw = json.loads(path.read_text(encoding="utf-8"))
    schema = int(raw.get("schema_version") or 0)
    if schema not in (1, 2, SURFACE_SCHEMA_VERSION):
        return None
    legacy = bool(raw.get("legacy")) or schema < SURFACE_SCHEMA_VERSION
    as_of = _parse_ts(str(raw.get("quote_as_of") or raw["as_of"]))
    spot = float(raw.get("calibration_spot", raw["spot"]))
    session_date = (
        date.fromisoformat(str(raw["session_date"]))
        if raw.get("session_date")
        else surface_session_date_from_as_of(as_of)
    )
    session_close_at = (
        _parse_ts(str(raw["session_close_at"]))
        if raw.get("session_close_at")
        else last_completed_close_at(as_of)
    )
    smiles = [
        ExpirySmile(
            expiry=date.fromisoformat(item["expiry"]),
            nodes=[
                SurfaceNode(
                    strike=float(node["strike"]),
                    delta=float(node["delta"]),
                    iv=float(node["iv"]),
                    option_type=str(
                        node.get(
                            "option_type",
                            "put" if float(node["delta"]) < 0 else "call",
                        )
                    ),
                    bid=(
                        float(node["bid"])
                        if node.get("bid") is not None
                        else None
                    ),
                    ask=(
                        float(node["ask"])
                        if node.get("ask") is not None
                        else None
                    ),
                    quote_time=(
                        _parse_ts(str(node["quote_time"]))
                        if node.get("quote_time")
                        else None
                    ),
                    premium=(
                        float(node["premium"])
                        if node.get("premium") is not None
                        else None
                    ),
                    vendor_iv=(
                        float(node["vendor_iv"])
                        if node.get("vendor_iv") is not None
                        else None
                    ),
                )
                for node in item["nodes"]
            ],
        )
        for item in raw["smiles"]
    ]
    return IvSurface(
        underlying=str(raw["underlying"]).upper(),
        spot=spot,
        as_of=as_of,
        source=str(raw["source"]),
        smiles=smiles,
        quality={key: int(value) for key, value in raw.get("quality", {}).items()},
        schema_version=schema,
        session_date=session_date,
        session_close_at=session_close_at,
        legacy=legacy,
    )


def sticky_delta_iv(
    surface: IvSurface,
    position: Any,
    *,
    spot: float,
    years: float,
    rate: float,
    dividend: float,
) -> float | None:
    """Look up a same-expiry IV using a sticky-delta smile iteration.

    Returns None rather than extrapolating beyond the calibrated delta range.
    """
    if spot <= 0 or years <= 0:
        return None
    smile = next(
        (candidate for candidate in surface.smiles if candidate.expiry == position.expiry),
        None,
    )
    if smile is None:
        return None
    wing = [
        node for node in smile.nodes
        if node.option_type == position.option_type
    ]
    if len(wing) < 2:
        return None

    initial = min(wing, key=lambda node: abs(node.strike - position.strike))
    iv = initial.iv
    for _ in range(STICKY_DELTA_MAX_ITERATIONS):
        try:
            delta = price_option(
                spot=spot,
                strike=position.strike,
                years=years,
                iv=iv,
                rate=rate,
                dividend=dividend,
                option_type=position.option_type,
                style="american",
                steps=100,
            ).delta
        except ValueError:
            return None
        next_iv = _interpolate_iv_by_delta(wing, delta)
        if next_iv is None:
            return None
        if abs(next_iv - iv) <= STICKY_DELTA_TOLERANCE:
            return next_iv
        iv = next_iv
    return iv


def close_premium_from_surface(surface: IvSurface, position: Any) -> float | None:
    """Last-session option mid from the frozen smile: exact strike, else interpolate."""
    smile = next(
        (candidate for candidate in surface.smiles if candidate.expiry == position.expiry),
        None,
    )
    if smile is None:
        return None
    wing = [
        node
        for node in smile.nodes
        if node.option_type == position.option_type and node.premium is not None
    ]
    return _interpolate_premium_by_strike(wing, position.strike)


def sticky_strike_iv(surface: IvSurface, position: Any) -> float | None:
    """Read IV at the same strike from the same option-type wing."""
    smile = next(
        (candidate for candidate in surface.smiles if candidate.expiry == position.expiry),
        None,
    )
    if smile is None:
        return _term_interpolated_strike_iv(surface, position)
    wing = [node for node in smile.nodes if node.option_type == position.option_type]
    return _interpolate_iv_by_strike(wing, position.strike)


def estimate_overnight_iv(
    surface: IvSurface,
    position: Any,
    *,
    spot: float,
    years: float,
    rate: float,
    dividend: float,
    sticky_delta_weight: float = 0.5,
) -> OvernightIvEstimate:
    """Estimate frozen-surface IV with explicit sticky-strike/delta scenarios."""
    strike = sticky_strike_iv(surface, position)
    if strike is None:
        raise ValueError("no same-wing IV at strike or surrounding expiry")
    delta = sticky_delta_iv(
        surface,
        position,
        spot=spot,
        years=years,
        rate=rate,
        dividend=dividend,
    )
    weight = min(max(sticky_delta_weight, 0.0), 1.0)
    if delta is None:
        return OvernightIvEstimate(
            sticky_strike=strike,
            sticky_delta=None,
            base=strike,
            low=strike,
            high=strike,
            sticky_delta_weight=0.0,
            method="sticky-strike",
        )
    base = strike * (1.0 - weight) + delta * weight
    return OvernightIvEstimate(
        sticky_strike=strike,
        sticky_delta=delta,
        base=base,
        low=min(strike, delta),
        high=max(strike, delta),
        sticky_delta_weight=weight,
        method="hybrid" if 0.0 < weight < 1.0 else (
            "sticky-delta" if weight == 1.0 else "sticky-strike"
        ),
    )


def _term_interpolated_strike_iv(surface: IvSurface, position: Any) -> float | None:
    """Interpolate total variance across bracketing expiries at one strike."""
    before = [smile for smile in surface.smiles if smile.expiry < position.expiry]
    after = [smile for smile in surface.smiles if smile.expiry > position.expiry]
    if not before or not after:
        return None
    lower, upper = before[-1], after[0]
    lo_iv = _interpolate_iv_by_strike(
        [node for node in lower.nodes if node.option_type == position.option_type],
        position.strike,
    )
    hi_iv = _interpolate_iv_by_strike(
        [node for node in upper.nodes if node.option_type == position.option_type],
        position.strike,
    )
    if lo_iv is None or hi_iv is None:
        return None
    lo_t = max((lower.expiry - surface.as_of.date()).days / 365.0, 1e-8)
    hi_t = max((upper.expiry - surface.as_of.date()).days / 365.0, 1e-8)
    target_t = max((position.expiry - surface.as_of.date()).days / 365.0, 1e-8)
    ratio = (target_t - lo_t) / (hi_t - lo_t)
    total_var = lo_iv * lo_iv * lo_t + ratio * (
        hi_iv * hi_iv * hi_t - lo_iv * lo_iv * lo_t
    )
    return (max(total_var, 0.0) / target_t) ** 0.5


_ET = ZoneInfo("America/New_York")
_RTH_CLOSE = (16, 0)
# Last RTH prints are typically 15:59:59 ET, not 16:00:00. Count the last
# half-hour as the close so a successful align is not stale on the next run.
_CLOSE_QUOTE_SLACK = timedelta(minutes=30)


def last_completed_session_date(now: datetime) -> date:
    """US equity session date whose 16:00 ET close has already printed.

    Before the close, and on weekends, this is the previous weekday.
    NYSE holidays are not modeled — those days may need a manual refresh-iv.
    """
    return last_completed_close_at(now).astimezone(_ET).date()


def last_completed_close_at(now: datetime) -> datetime:
    """16:00 ET of the last weekday whose regular close has already printed."""
    local = _utc(now).astimezone(_ET)
    session = local.date()
    if (local.hour, local.minute) < _RTH_CLOSE:
        session -= timedelta(days=1)
    while session.weekday() >= 5:
        session -= timedelta(days=1)
    return datetime(session.year, session.month, session.day, 16, 0, tzinfo=_ET)


def surface_session_date_from_as_of(as_of: datetime) -> date:
    return _utc(as_of).astimezone(_ET).date()


def surface_session_date(surface: IvSurface) -> date:
    if surface.session_date is not None:
        return surface.session_date
    return surface_session_date_from_as_of(surface.as_of)


def surface_matches_session(
    surface: IvSurface,
    session: MarketSession,
    *,
    allow_legacy: bool = False,
) -> bool:
    if surface.legacy and not allow_legacy:
        return False
    return surface_session_date(surface) == session.session_date


def is_surface_fresh(
    surface: IvSurface,
    now: datetime,
    *,
    max_age: timedelta = MAX_SURFACE_AGE,
    session: MarketSession | None = None,
) -> bool:
    """True when the surface is last-session close quotes, not a pre-open freeze.

    Alpaca stamps the last RTH print at 15:59:59 ET. Treat quotes in the last
    half-hour of that session as the close. ``max_age`` is kept for callers.
    """
    del max_age
    if session is not None:
        return surface_matches_session(surface, session)
    if surface.session_date is not None and not surface.legacy:
        return surface.session_date == last_completed_session_date(now)
    close_at = last_completed_close_at(now)
    return _utc(surface.as_of) >= _utc(close_at) - _CLOSE_QUOTE_SLACK


def _interpolate_iv_by_delta(
    nodes: list[SurfaceNode],
    target_delta: float,
) -> float | None:
    ordered = sorted(nodes, key=lambda node: node.delta)
    if target_delta < ordered[0].delta or target_delta > ordered[-1].delta:
        return None
    for lower, upper in zip(ordered, ordered[1:]):
        if lower.delta <= target_delta <= upper.delta:
            width = upper.delta - lower.delta
            if width <= 0:
                return None
            ratio = (target_delta - lower.delta) / width
            return lower.iv + ratio * (upper.iv - lower.iv)
    return ordered[-1].iv


def _interpolate_premium_by_strike(
    nodes: list[SurfaceNode],
    target_strike: float,
) -> float | None:
    if not nodes:
        return None
    ordered = sorted(nodes, key=lambda node: node.strike)
    exact = next(
        (node for node in ordered if abs(node.strike - target_strike) < 1e-6),
        None,
    )
    if exact is not None and exact.premium is not None:
        return exact.premium
    if target_strike < ordered[0].strike or target_strike > ordered[-1].strike:
        return None
    for lower, upper in zip(ordered, ordered[1:]):
        if lower.strike <= target_strike <= upper.strike:
            if lower.premium is None or upper.premium is None:
                return None
            width = upper.strike - lower.strike
            if width <= 0:
                return lower.premium
            ratio = (target_strike - lower.strike) / width
            return lower.premium + ratio * (upper.premium - lower.premium)
    last = ordered[-1].premium
    return last


def _interpolate_iv_by_strike(
    nodes: list[SurfaceNode],
    target_strike: float,
) -> float | None:
    if not nodes:
        return None
    ordered = sorted(nodes, key=lambda node: node.strike)
    if target_strike < ordered[0].strike or target_strike > ordered[-1].strike:
        return None
    for lower, upper in zip(ordered, ordered[1:]):
        if lower.strike <= target_strike <= upper.strike:
            width = upper.strike - lower.strike
            if width <= 0:
                return lower.iv
            ratio = (target_strike - lower.strike) / width
            return lower.iv + ratio * (upper.iv - lower.iv)
    return ordered[-1].iv


def _has_usable_quote(
    node: dict[str, Any],
    max_spread_ratio: float,
    *,
    as_of: datetime,
    max_quote_age: timedelta,
) -> bool:
    quote = node.get("latestQuote") or node.get("quote")
    if not isinstance(quote, dict):
        return False
    try:
        bid = float(quote.get("bp", quote.get("bid")))
        ask = float(quote.get("ap", quote.get("ask")))
    except (TypeError, ValueError):
        return False
    if bid <= 0 or ask <= 0 or ask < bid:
        return False
    mid = 0.5 * (bid + ask)
    if (ask - bid) / mid > max_spread_ratio:
        return False
    quote_time = quote.get("t") or quote.get("timestamp")
    if quote_time is None:
        return False
    try:
        return _utc(as_of) - _parse_ts(str(quote_time)) <= max_quote_age
    except (TypeError, ValueError):
        return False


def _extract_iv(node: dict[str, Any]) -> float | None:
    for key in ("impliedVolatility", "implied_volatility", "iv"):
        try:
            value = node.get(key)
            if value is not None:
                iv = float(value)
                return iv / 100.0 if iv > 5 else iv
        except (TypeError, ValueError):
            return None
    return None


def _extract_delta(node: dict[str, Any]) -> float | None:
    greeks = node.get("greeks")
    if not isinstance(greeks, dict):
        return None
    try:
        value = greeks.get("delta")
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _parse_ts(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return _utc(parsed)


def _utc(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
