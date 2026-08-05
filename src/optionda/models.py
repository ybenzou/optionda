from __future__ import annotations

from datetime import date, datetime
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator

OptionType = Literal["call", "put"]
Side = Literal["long", "short"]
Feed = Literal["yahoo", "alpaca"]


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
    entry_premium: float | None = None
    multiplier: int = 100

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


class AppConfig(BaseModel):
    default_account: str | None = None
    r: float = 0.045
    q: float = 0.0
    feed: Feed = "yahoo"
    poll_interval_sec: int = 60


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
    error: str | None = None
