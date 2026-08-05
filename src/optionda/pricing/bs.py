from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, datetime, timezone

from optionda.models import OptionType


@dataclass(frozen=True)
class OptionResult:
    price: float
    delta: float
    gamma: float
    vega: float
    theta: float
    dte: float


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _norm_pdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


def years_to_expiry(expiry: date, now: datetime | None = None) -> float:
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    # Treat expiry as 16:00 ET ≈ 20:00 UTC on expiry day (approx)
    expiry_dt = datetime(expiry.year, expiry.month, expiry.day, 20, 0, tzinfo=timezone.utc)
    seconds = (expiry_dt - current).total_seconds()
    return max(seconds / (365.0 * 24.0 * 3600.0), 1e-8)


def black_scholes(
    spot: float,
    strike: float,
    years: float,
    iv: float,
    rate: float = 0.045,
    dividend: float = 0.0,
    option_type: OptionType = "call",
) -> OptionResult:
    if spot <= 0 or strike <= 0:
        raise ValueError("spot and strike must be > 0")
    if iv <= 0:
        raise ValueError("iv must be > 0")
    t = max(years, 1e-8)
    sqrt_t = math.sqrt(t)
    d1 = (math.log(spot / strike) + (rate - dividend + 0.5 * iv * iv) * t) / (iv * sqrt_t)
    d2 = d1 - iv * sqrt_t
    df_r = math.exp(-rate * t)
    df_q = math.exp(-dividend * t)

    if option_type == "call":
        price = spot * df_q * _norm_cdf(d1) - strike * df_r * _norm_cdf(d2)
        delta = df_q * _norm_cdf(d1)
        theta = (
            -(spot * df_q * _norm_pdf(d1) * iv) / (2 * sqrt_t)
            - rate * strike * df_r * _norm_cdf(d2)
            + dividend * spot * df_q * _norm_cdf(d1)
        ) / 365.0
    else:
        price = strike * df_r * _norm_cdf(-d2) - spot * df_q * _norm_cdf(-d1)
        delta = -df_q * _norm_cdf(-d1)
        theta = (
            -(spot * df_q * _norm_pdf(d1) * iv) / (2 * sqrt_t)
            + rate * strike * df_r * _norm_cdf(-d2)
            - dividend * spot * df_q * _norm_cdf(-d1)
        ) / 365.0

    gamma = df_q * _norm_pdf(d1) / (spot * iv * sqrt_t)
    vega = spot * df_q * _norm_pdf(d1) * sqrt_t / 100.0  # per 1 vol point
    return OptionResult(
        price=max(price, 0.0),
        delta=delta,
        gamma=gamma,
        vega=vega,
        theta=theta,
        dte=t * 365.0,
    )
