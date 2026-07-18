"""Canonical, immutable point-in-time consensus estimate records."""

from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum
from math import isfinite

from pydantic import BaseModel, Field, model_validator

from smct_research.core.models import normalize_utc


class EstimateMetric(StrEnum):
    EPS = "eps"
    REVENUE = "revenue"
    EBITDA = "ebitda"
    FREE_CASH_FLOW = "free_cash_flow"


class EstimateBasis(StrEnum):
    GAAP = "gaap"
    NON_GAAP = "non_gaap"
    PROVIDER_DEFINED = "provider_defined"


class EstimatePeriod(StrEnum):
    QUARTER = "quarter"
    FISCAL_YEAR = "fiscal_year"


class ConsensusEstimate(BaseModel):
    ticker: str
    metric: EstimateMetric
    target_period_end: date
    period_type: EstimatePeriod
    horizon_label: str | None = None
    consensus: float
    unit: str
    currency: str
    basis: EstimateBasis = EstimateBasis.PROVIDER_DEFINED
    analyst_count: int | None = None
    high: float | None = None
    low: float | None = None
    standard_deviation: float | None = None
    estimate_breadth: float | None = None
    provider: str
    provider_record_id: str
    source_identifier: str
    published_at: datetime | None = None
    retrieved_at: datetime
    available_at: datetime

    @model_validator(mode="after")
    def valid(self):
        self.ticker = self.ticker.upper().strip()
        self.currency = self.currency.upper().strip()
        self.provider = self.provider.strip()
        self.provider_record_id = self.provider_record_id.strip()
        self.source_identifier = self.source_identifier.strip()
        self.unit = self.unit.strip()
        self.retrieved_at = normalize_utc(self.retrieved_at)
        self.available_at = normalize_utc(self.available_at)
        if self.published_at:
            self.published_at = normalize_utc(self.published_at)
        if (
            not self.provider
            or not self.ticker
            or not self.provider_record_id
            or not self.source_identifier
            or not self.unit
            or not self.currency.isalpha()
            or len(self.currency) != 3
        ):
            raise ValueError("missing or unsupported estimate identity")
        for value in (
            self.consensus,
            self.high,
            self.low,
            self.standard_deviation,
            self.estimate_breadth,
        ):
            if value is not None and not isfinite(value):
                raise ValueError("estimate values must be finite")
        if self.analyst_count is not None and self.analyst_count < 0:
            raise ValueError("analyst_count cannot be negative")
        if self.standard_deviation is not None and self.standard_deviation < 0:
            raise ValueError("standard_deviation cannot be negative")
        if self.high is not None and self.low is not None and self.high < self.low:
            raise ValueError("high estimate below low estimate")
        return self

    @property
    def identity(self) -> tuple[object, ...]:
        return (
            self.provider,
            self.ticker,
            self.metric,
            self.target_period_end,
            self.period_type,
            self.unit,
            self.currency,
            self.basis,
        )


class EstimateSnapshot(BaseModel):
    estimate: ConsensusEstimate


class EstimateRevision(BaseModel):
    requested_lookback_days: int
    actual_lookback_days: int | None = None
    value: float | None = None
    conventional: float | None = None
    symmetric: float | None = None
    used_symmetric: bool = False
    prior: ConsensusEstimate | None = None
    diagnostics: list[str] = Field(default_factory=list)


class EstimateDataQuality(BaseModel):
    score: float = Field(ge=0, le=1)
    coverage_percentage: float = Field(ge=0, le=100)
    diagnostics: list[str] = Field(default_factory=list)


class EstimateRevisionFeatures(BaseModel):
    ticker: str
    as_of: datetime
    metric: EstimateMetric
    current: ConsensusEstimate
    revisions: dict[int, EstimateRevision]
    acceleration: float | None = None
    streak: int = 0
    analyst_count_change_30d: float | int | None = None
    high_change_30d: float | None = None
    low_change_30d: float | None = None
    dispersion_change_30d: float | None = None
    breadth_change_30d: float | None = None
    days_since_latest_update: int
    sign_transition: str | None = None
    quality: EstimateDataQuality
    diagnostics: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_point_in_time(self):
        self.as_of = normalize_utc(self.as_of)
        if self.current.available_at > self.as_of:
            raise ValueError("current estimate is not available as_of")
        if any(
            item.prior and item.prior.available_at > self.as_of for item in self.revisions.values()
        ):
            raise ValueError("historical estimate is not available as_of")
        return self
