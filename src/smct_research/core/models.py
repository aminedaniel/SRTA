from __future__ import annotations

from datetime import UTC, date, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, model_validator


def normalize_utc(value: datetime) -> datetime:
    """Interpret naive timestamps as UTC and convert aware ones to UTC."""
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


class SignalDirection(StrEnum):
    POSITIVE = "positive"
    NEGATIVE = "negative"
    NEUTRAL = "neutral"


class ThesisStatus(StrEnum):
    DRAFT = "draft"
    ACTIVE = "active"
    STRENGTHENING = "strengthening"
    WEAKENING = "weakening"
    INVALIDATED = "invalidated"
    FULLY_PRICED = "fully_priced"


class CongressionalChamber(StrEnum):
    HOUSE = "house"
    SENATE = "senate"


class CongressionalOwner(StrEnum):
    MEMBER = "member"
    SPOUSE = "spouse"
    DEPENDENT = "dependent"
    JOINT = "joint"
    UNKNOWN = "unknown"


class CongressionalTransactionType(StrEnum):
    PURCHASE = "purchase"
    SALE = "sale"
    EXCHANGE = "exchange"


class CongressionalTransaction(BaseModel):
    """Canonical normalized record for a publicly disclosed transaction."""

    filer_name: str
    chamber: CongressionalChamber
    owner: CongressionalOwner = CongressionalOwner.UNKNOWN
    ticker: str
    transaction_type: CongressionalTransactionType
    transaction_date: date
    disclosure_date: date
    amount_low_usd: float = Field(ge=0)
    amount_high_usd: float = Field(ge=0)
    source_url: str

    @model_validator(mode="after")
    def validate_transaction(self) -> CongressionalTransaction:
        self.ticker = self.ticker.upper().strip()
        if self.amount_high_usd < self.amount_low_usd:
            raise ValueError("amount_high_usd must be greater than or equal to amount_low_usd")
        return self

    @property
    def estimated_amount_usd(self) -> float:
        return (self.amount_low_usd + self.amount_high_usd) / 2

    @property
    def disclosure_lag_days(self) -> int:
        return max(0, (self.disclosure_date - self.transaction_date).days)


class Company(BaseModel):
    ticker: str = Field(min_length=1, max_length=12)
    name: str
    market_cap_usd: float = Field(gt=0)
    sector: str
    industry: str | None = None
    exchange: str | None = None
    cik: str | None = None
    country: str | None = None
    average_daily_dollar_volume: float | None = Field(default=None, ge=0)
    is_active: bool | None = None
    security_type: str | None = None

    @model_validator(mode="after")
    def normalize_ticker(self) -> Company:
        self.ticker = self.ticker.upper().strip()
        return self


class FeatureSnapshot(BaseModel):
    ticker: str
    as_of: datetime = Field(default_factory=lambda: datetime.now(UTC))
    values: dict[str, float | int | str | bool | None] = Field(default_factory=dict)
    sources: dict[str, str] = Field(default_factory=dict)
    source_as_of: dict[str, datetime] = Field(default_factory=dict)

    @model_validator(mode="after")
    def normalize_snapshot(self) -> FeatureSnapshot:
        self.ticker = self.ticker.upper().strip()
        self.as_of = normalize_utc(self.as_of)
        self.source_as_of = {
            source: normalize_utc(timestamp) for source, timestamp in self.source_as_of.items()
        }
        return self

    def require_float(self, key: str) -> float:
        value = self.values.get(key)
        if value is None or isinstance(value, bool) or not isinstance(value, (int, float)):
            raise KeyError(f"Required numeric feature missing: {key}")
        return float(value)


class SignalResult(BaseModel):
    signal_id: str
    ticker: str
    score: float = Field(ge=-100, le=100)
    confidence: float = Field(ge=0, le=1)
    direction: SignalDirection
    thesis: str
    evidence: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    evaluated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @model_validator(mode="after")
    def normalize_evaluated_at(self) -> SignalResult:
        self.ticker = self.ticker.upper().strip()
        self.evaluated_at = normalize_utc(self.evaluated_at)
        return self


class ResearchThesis(BaseModel):
    ticker: str
    title: str
    variant_perception: str
    supporting_evidence: list[str]
    key_risks: list[str]
    invalidation_conditions: list[str]
    valuation_low: float | None = Field(default=None, ge=0)
    valuation_base: float | None = Field(default=None, ge=0)
    valuation_high: float | None = Field(default=None, ge=0)
    horizon_months: int = Field(default=30, ge=12, le=60)
    status: ThesisStatus = ThesisStatus.DRAFT
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
