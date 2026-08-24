from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Literal
from zoneinfo import ZoneInfo

from optionda.models import OptionType

ExerciseStyle = Literal["american", "european"]
DEFAULT_TREE_STEPS = 120


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


_ET = ZoneInfo("America/New_York")
_SECONDS_PER_YEAR = 365.0 * 24.0 * 3600.0
# Weekend hours are omitted; stretch weekdays so Fri–Fri still equals 7/365.
_WEEKDAY_STRETCH = 7.0 / 5.0


def _weekday_seconds(start: datetime, end: datetime) -> float:
    """Monday–Friday seconds between ``start`` and ``end`` in New York time."""
    if end <= start:
        return 0.0
    start_et = start.astimezone(_ET)
    end_et = end.astimezone(_ET)
    total = 0.0
    day = start_et.date()
    last = end_et.date()
    while day <= last:
        if day.weekday() < 5:
            day_start = datetime(day.year, day.month, day.day, tzinfo=_ET)
            day_end = day_start + timedelta(days=1)
            lo = start_et if start_et > day_start else day_start
            hi = end_et if end_et < day_end else day_end
            if hi > lo:
                total += (hi - lo).total_seconds()
        day += timedelta(days=1)
    return total


def years_to_expiry(expiry: date, now: datetime | None = None) -> float:
    """Trading-day year fraction: Saturday and Sunday do not consume theta.

    Weekday hours are stretched by 7/5 so a Friday close to the next Friday
    close is still one calendar week, matching existing 365-day surfaces.
    """
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    # Equity options expire at 16:00 New York time; ZoneInfo handles EDT/EST.
    expiry_dt = datetime(
        expiry.year,
        expiry.month,
        expiry.day,
        16,
        0,
        tzinfo=_ET,
    )
    seconds = _weekday_seconds(current, expiry_dt)
    return max(seconds * _WEEKDAY_STRETCH / _SECONDS_PER_YEAR, 1e-8)


def black_scholes(
    spot: float,
    strike: float,
    years: float,
    iv: float,
    rate: float = 0.045,
    dividend: float = 0.0,
    option_type: OptionType = "call",
) -> OptionResult:
    """European Black–Scholes–Merton closed form (kept for reference / tests)."""
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


def _crr_price(
    spot: float,
    strike: float,
    years: float,
    iv: float,
    rate: float,
    dividend: float,
    option_type: OptionType,
    *,
    american: bool,
    steps: int,
) -> float:
    """Cox–Ross–Rubinstein tree; American nodes take max(exercise, hold)."""
    t = max(years, 1e-8)
    n = max(int(steps), 2)
    dt = t / n
    u = math.exp(iv * math.sqrt(dt))
    d = 1.0 / u
    growth = math.exp((rate - dividend) * dt)
    disc = math.exp(-rate * dt)
    p = (growth - d) / (u - d)
    # Numerical guard for extreme params
    p = min(max(p, 0.0), 1.0)
    q = 1.0 - p
    is_call = option_type == "call"

    # Terminal payoffs at spot * u^j * d^(n-j)
    values = [0.0] * (n + 1)
    for j in range(n + 1):
        s_t = spot * (u**j) * (d ** (n - j))
        values[j] = max(s_t - strike, 0.0) if is_call else max(strike - s_t, 0.0)

    for step in range(n - 1, -1, -1):
        for j in range(step + 1):
            hold = disc * (p * values[j + 1] + q * values[j])
            if american:
                s = spot * (u**j) * (d ** (step - j))
                exercise = (s - strike) if is_call else (strike - s)
                values[j] = max(hold, exercise, 0.0)
            else:
                values[j] = max(hold, 0.0)
    return values[0]


def _american_price(
    spot: float,
    strike: float,
    years: float,
    iv: float,
    rate: float,
    dividend: float,
    option_type: OptionType,
    *,
    steps: int,
) -> float:
    """Average adjacent CRR trees to suppress the even/odd lattice oscillation."""
    first = _crr_price(
        spot, strike, years, iv, rate, dividend, option_type,
        american=True, steps=steps,
    )
    second = _crr_price(
        spot, strike, years, iv, rate, dividend, option_type,
        american=True, steps=steps + 1,
    )
    return 0.5 * (first + second)


def price_option(
    spot: float,
    strike: float,
    years: float,
    iv: float,
    rate: float = 0.045,
    dividend: float = 0.0,
    option_type: OptionType = "call",
    *,
    style: ExerciseStyle = "american",
    steps: int = DEFAULT_TREE_STEPS,
    greeks: bool = True,
) -> OptionResult:
    """Price equity options. Default style is American (CRR tree).

    US listed stock/ETF options are American-style; European BS is available
    via ``style='european'`` (closed form).
    """
    if spot <= 0 or strike <= 0:
        raise ValueError("spot and strike must be > 0")
    if iv <= 0:
        raise ValueError("iv must be > 0")
    t = max(years, 1e-8)

    if style == "european":
        euro = black_scholes(
            spot=spot,
            strike=strike,
            years=t,
            iv=iv,
            rate=rate,
            dividend=dividend,
            option_type=option_type,
        )
        if not greeks:
            return euro
        return euro

    # A floor keeps small caller-provided trees from producing lattice noise.
    stable_steps = max(int(steps), DEFAULT_TREE_STEPS)
    price = _american_price(
        spot,
        strike,
        t,
        iv,
        rate,
        dividend,
        option_type,
        steps=stable_steps,
    )
    if not greeks:
        return OptionResult(
            price=price,
            delta=0.0,
            gamma=0.0,
            vega=0.0,
            theta=0.0,
            dte=t * 365.0,
        )

    # Bump greeks (same tree depth) — matches desk FD convention.
    d_spot = max(spot * 1e-4, 1e-3)
    up = _american_price(
        spot + d_spot, strike, t, iv, rate, dividend, option_type,
        steps=stable_steps,
    )
    dn = _american_price(
        spot - d_spot, strike, t, iv, rate, dividend, option_type,
        steps=stable_steps,
    )
    delta = (up - dn) / (2.0 * d_spot)
    gamma = (up - 2.0 * price + dn) / (d_spot * d_spot)

    d_iv = 0.01
    v_up = _american_price(
        spot, strike, t, iv + d_iv, rate, dividend, option_type,
        steps=stable_steps,
    )
    v_dn = _american_price(
        spot, strike, t, max(iv - d_iv, 1e-4), rate, dividend, option_type,
        steps=stable_steps,
    )
    vega = (v_up - v_dn) / (2.0 * (d_iv * 100.0))  # per 1 vol point

    d_t = min(1.0 / 365.0, t * 0.5)
    if t > d_t * 1.01:
        theta_price = _american_price(
            spot, strike, t - d_t, iv, rate, dividend, option_type,
            steps=stable_steps,
        )
        theta = (theta_price - price) / (d_t * 365.0)  # per calendar day
    else:
        theta = 0.0

    return OptionResult(
        price=max(price, 0.0),
        delta=delta,
        gamma=gamma,
        vega=vega,
        theta=theta,
        dte=t * 365.0,
    )


def _intrinsic(
    spot: float,
    strike: float,
    years: float,
    rate: float,
    dividend: float,
    option_type: OptionType,
) -> float:
    # Undiscounted intrinsic for American early-exercise floor on premium checks.
    if option_type == "call":
        return max(spot - strike, 0.0)
    return max(strike - spot, 0.0)


def implied_volatility(
    spot: float,
    strike: float,
    years: float,
    price: float,
    rate: float = 0.045,
    dividend: float = 0.0,
    option_type: OptionType = "call",
    *,
    style: ExerciseStyle = "american",
    steps: int = 100,
    lo: float = 1e-4,
    hi: float = 5.0,
    tol: float = 1e-6,
    max_iter: int = 80,
) -> float:
    """Solve IV from an option premium under European or American pricing."""
    if spot <= 0 or strike <= 0:
        raise ValueError("spot and strike must be > 0")
    if price <= 0:
        raise ValueError("option price must be > 0")
    t = max(years, 1e-8)
    floor = _intrinsic(spot, strike, t, rate, dividend, option_type)
    if price < floor - 1e-4:
        raise ValueError(
            f"price {price:.4f} below intrinsic {floor:.4f}; cannot imply IV"
        )
    target = max(price, floor + 1e-8)

    def model(vol: float) -> float:
        return price_option(
            spot=spot,
            strike=strike,
            years=t,
            iv=vol,
            rate=rate,
            dividend=dividend,
            option_type=option_type,
            style=style,
            steps=steps,
            greeks=False,
        ).price

    upper = hi
    for _ in range(12):
        if model(upper) >= target:
            break
        upper *= 2.0
        if upper > 20.0:
            raise ValueError("IV solver failed to bracket premium")
    lower = lo
    for _ in range(max_iter):
        mid = 0.5 * (lower + upper)
        value = model(mid)
        if abs(value - target) < tol:
            return mid
        if value > target:
            upper = mid
        else:
            lower = mid
    return 0.5 * (lower + upper)
