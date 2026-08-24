from __future__ import annotations

from datetime import date, datetime
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator

OptionType = Literal["call", "put"]
Side = Literal["long", "short"]
Feed = Literal["yahoo", "alpaca"]
AlpacaOptionsFeed = Literal["auto", "opra", "indicative"]
# mid = invert IV from option mid/last; vendor = provider's IV field; auto = mid then vendor
IvMode = Literal["mid", "vendor", "auto"]


class Position(BaseModel):
    id: str = Field(default_factory=lambda: uuid4().hex[:10])
    occ_symbol: str
    underlying: str
    expiry: date
    strike: float
    option_type: OptionType
    qty: float = 1.0
    side: Side = "long"
    iv_frozen: float
    iv_as_of: datetime
    iv_source: str | None = None
    entry_premium: float | None = None
    multiplier: int = 100
    opened_at: datetime | None = None

    @field_validator("underlying")
    @classmethod
    def upper_underlying(cls, value: str) -> str:
        return value.strip().upper()

    @field_validator("occ_symbol")
    @classmethod
    def upper_occ(cls, value: str) -> str:
        return value.strip().upper()

    @field_validator("iv_frozen")
    @classmethod
    def positive_iv(cls, value: float) -> float:
        if value <= 0:
            raise ValueError("iv_frozen must be > 0")
        return value

    @field_validator("entry_premium")
    @classmethod
    def non_negative_entry(cls, value: float | None) -> float | None:
        if value is not None and value <= 0:
            raise ValueError("entry_premium must be > 0")
        return value


class Account(BaseModel):
    name: str
    positions: list[Position] = Field(default_factory=list)

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("account name required")
        return cleaned


ExerciseStyle = Literal["american", "european"]
OvernightIvMode = Literal["hybrid", "sticky_delta", "sticky_strike"]


class AppConfig(BaseModel):
    default_account: str | None = None
    r: float = 0.045
    q: float = 0.0
    feed: Feed = "yahoo"
    poll_interval_sec: int = 60
    # Alpaca options snapshot feed: auto tries OPRA then free indicative.
    alpaca_options_feed: AlpacaOptionsFeed = "auto"
    # Prefer mid-implied IV (closer to broker desks) over vendor indicative IV.
    iv_mode: IvMode = "mid"
    # US equity/ETF options are American; european keeps closed-form BS.
    option_style: ExerciseStyle = "american"
    # Frozen-surface dynamics used when the underlying keeps trading overnight.
    overnight_iv_mode: OvernightIvMode = "hybrid"
    sticky_delta_weight: float = 0.5
    # Optional simple term structure: [{"days": 30, "rate": 0.04}, ...].
    rate_curve: list[tuple[int, float]] = Field(default_factory=list)
    # Per-ticker continuous dividend yields. Values absent here fall back to q.
    dividend_yields: dict[str, float] = Field(default_factory=dict)


class SpotQuote(BaseModel):
    symbol: str
    price: float
    as_of: datetime | None = None
    source: str = "unknown"


class OptionIvQuote(BaseModel):
    occ_symbol: str
    iv: float
    as_of: datetime | None = None
    source: str = "unknown"


class RowMark(BaseModel):
    position: Position
    spot: float | None
    theo: float | None
    delta: float | None
    dte: float | None
    notional: float | None
    cost: float | None = None  # avg entry premium / share
    upnl: float | None = None  # $ vs cost: (theo - cost) * mult * qty * sign
    live: float | None = None  # live option mid / last (verify only)
    valuation_mode: Literal["surface", "frozen"] = "frozen"
    surface_iv: float | None = None
    model_iv: float | None = None
    surface_as_of: datetime | None = None
    surface_source: str | None = None
    surface_session_date: date | None = None
    reference_session_date: date | None = None
    iv_stale: bool = False
    iv_fallback: bool = False
    model_low: float | None = None
    model_high: float | None = None
    iv_dynamics: str | None = None
    sticky_strike_iv: float | None = None
    sticky_delta_iv: float | None = None
    sticky_strike_model: float | None = None
    sticky_delta_model: float | None = None
    rate_used: float | None = None
    dividend_used: float | None = None
    spot_as_of: datetime | None = None
    spot_source: str | None = None
    # Underlying print used when the IV surface was frozen (close / quote-time spot).
    close_spot: float | None = None
    # Last-session option mid (or close-spot model) for the held OCC.
    close_premium: float | None = None
    # Per-share Model$ minus that close premium.
    theo_chg: float | None = None
    # Last add / merge / sell on this position (journal, US/Eastern display).
    last_op_at: datetime | None = None
    error: str | None = None
