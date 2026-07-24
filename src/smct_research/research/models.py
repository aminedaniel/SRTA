from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import datetime
from enum import StrEnum
from math import isfinite
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from smct_research.core.models import normalize_utc


class FrozenDict(dict[str, Any]):
    """Small immutable dict that remains JSON-serializable through Pydantic."""

    def __init__(self, value: Mapping[str, Any] | None = None, /, **kwargs: Any) -> None:
        data: dict[str, Any] = {}
        if value is not None:
            data.update(value)
        data.update(kwargs)
        super().__init__((key, _freeze(value)) for key, value in data.items())

    def _immutable(self, *_args: Any, **_kwargs: Any) -> None:
        raise TypeError("FrozenDict is immutable")

    def __setitem__(self, _key: str, _value: Any) -> None:
        self._immutable()

    def __delitem__(self, _key: str) -> None:
        self._immutable()

    def clear(self) -> None:
        self._immutable()

    def pop(self, _key: str, _default: Any = None) -> Any:
        self._immutable()

    def popitem(self) -> tuple[str, Any]:
        self._immutable()
        raise AssertionError("unreachable")

    def setdefault(self, _key: str, _default: Any = None) -> Any:
        self._immutable()

    def update(self, *_args: Any, **_kwargs: Any) -> None:
        self._immutable()


def _freeze(value: Any) -> Any:
    if isinstance(value, FrozenDict):
        return value
    if isinstance(value, Mapping):
        return FrozenDict(value)
    if isinstance(value, (list, tuple, set, frozenset)):
        return tuple(_freeze(item) for item in value)
    return value


def _freeze_mapping(value: Mapping[str, Any] | None) -> FrozenDict | None:
    return None if value is None else FrozenDict(value)


def _freeze_collection(value: Iterable[Any] | None) -> tuple[Any, ...] | None:
    return None if value is None else tuple(_freeze(item) for item in value)


def _finite(value: float | int | None, field_name: str) -> float | int | None:
    if value is not None and not isfinite(float(value)):
        raise ValueError(f"{field_name} must be finite")
    return value


class SignalAvailability(StrEnum):
    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"
    EXCLUDED = "excluded"


class ImmutableModel(BaseModel):
    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)


class SignalAssessment(ImmutableModel):
    signal_id: str
    signal_name: str | None = None
    score: float | None = Field(default=None, ge=-100, le=100)
    confidence: float | None = Field(default=None, ge=0, le=1)
    direction: str | None = None
    weighted_contribution: float | None = None
    thesis: str | None = None
    evidence: tuple[str, ...] | None = None
    risks: tuple[str, ...] | None = None
    metadata: FrozenDict | None = None
    evaluated_at: datetime | None = None
    availability: SignalAvailability | None = None
    stale_evidence_warnings: tuple[str, ...] | None = None
    point_in_time_warnings: tuple[str, ...] | None = None

    @field_validator("signal_id")
    @classmethod
    def normalize_signal_id(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("signal_id must not be blank")
        return normalized

    @field_validator("weighted_contribution")
    @classmethod
    def validate_weighted_contribution(cls, value: float | None) -> float | None:
        return _finite(value, "weighted_contribution")

    @field_validator("evaluated_at")
    @classmethod
    def normalize_evaluated_at(cls, value: datetime | None) -> datetime | None:
        return None if value is None else normalize_utc(value)

    @field_validator(
        "evidence", "risks", "stale_evidence_warnings", "point_in_time_warnings", mode="before"
    )
    @classmethod
    def freeze_collections(cls, value: Iterable[Any] | None) -> tuple[Any, ...] | None:
        return _freeze_collection(value)

    @field_validator("metadata", mode="before")
    @classmethod
    def freeze_metadata(cls, value: Mapping[str, Any] | None) -> FrozenDict | None:
        return _freeze_mapping(value)


class ValuationSummary(ImmutableModel):
    current_market_price: float | None = Field(default=None, ge=0)
    reverse_dcf_conservative_value: float | None = Field(default=None, ge=0)
    reverse_dcf_base_value: float | None = Field(default=None, ge=0)
    reverse_dcf_optimistic_value: float | None = Field(default=None, ge=0)
    implied_revenue_growth: float | None = None
    implied_terminal_margin: float | None = None
    implied_expectations_gap: float | None = None
    valuation_compression_evidence: tuple[str, ...] | None = None
    terminal_value_dependence: float | None = None
    dilution_or_share_count_risk: str | None = None
    missing_valuation_fields: tuple[str, ...] | None = None

    @field_validator(
        "current_market_price",
        "reverse_dcf_conservative_value",
        "reverse_dcf_base_value",
        "reverse_dcf_optimistic_value",
        "implied_revenue_growth",
        "implied_terminal_margin",
        "implied_expectations_gap",
        "terminal_value_dependence",
    )
    @classmethod
    def validate_finite(cls, value: float | None) -> float | None:
        return _finite(value, "numeric value")

    @field_validator("valuation_compression_evidence", "missing_valuation_fields", mode="before")
    @classmethod
    def freeze_collections(cls, value: Iterable[Any] | None) -> tuple[Any, ...] | None:
        return _freeze_collection(value)


class CompanyIdentity(ImmutableModel):
    ticker: str
    name: str
    market_cap_usd: float | None = Field(default=None, ge=0)
    sector: str | None = None
    industry: str | None = None
    exchange: str | None = None
    country: str | None = None
    cik: str | None = None

    @field_validator("ticker")
    @classmethod
    def normalize_ticker(cls, value: str) -> str:
        normalized = value.upper().strip()
        if not normalized:
            raise ValueError("ticker must not be blank")
        return normalized

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("name must not be blank")
        return value

    @field_validator("market_cap_usd")
    @classmethod
    def validate_market_cap(cls, value: float | None) -> float | None:
        return _finite(value, "market_cap_usd")


class CompanyResearchReport(ImmutableModel):
    schema_version: str
    company: CompanyIdentity
    as_of: datetime
    universe_eligible: bool
    exclusion_reasons: tuple[str, ...] = ()
    rank: int | None = Field(default=None, gt=0)
    composite_score: float | None = Field(default=None, ge=0, le=100)
    composite_confidence: float | None = Field(default=None, ge=0, le=1)
    feature_completeness_percentage: float = Field(ge=0, le=100)
    executive_summary: str | None = None
    variant_perception: str | None = None
    signal_assessments: tuple[SignalAssessment, ...] = ()
    supporting_evidence: tuple[str, ...] = ()
    contradictory_evidence: tuple[str, ...] = ()
    contextual_evidence: tuple[str, ...] = ()
    key_risks: tuple[str, ...] = ()
    catalysts: tuple[str, ...] = ()
    invalidation_conditions: tuple[str, ...] = ()
    valuation: ValuationSummary | None = None
    missing_evidence: tuple[str, ...] = ()
    unavailable_signals: tuple[str, ...] = ()
    stale_evidence_warnings: tuple[str, ...] = ()
    point_in_time_warnings: tuple[str, ...] = ()
    provenance: FrozenDict = Field(default_factory=FrozenDict)
    source_timestamps: FrozenDict = Field(default_factory=FrozenDict)

    @field_validator("as_of")
    @classmethod
    def normalize_as_of(cls, value: datetime) -> datetime:
        return normalize_utc(value)

    @field_validator("composite_score", "composite_confidence", "feature_completeness_percentage")
    @classmethod
    def validate_finite(cls, value: float | None) -> float | None:
        return _finite(value, "numeric value")

    @field_validator(
        "exclusion_reasons",
        "signal_assessments",
        "supporting_evidence",
        "contradictory_evidence",
        "contextual_evidence",
        "key_risks",
        "catalysts",
        "invalidation_conditions",
        "missing_evidence",
        "unavailable_signals",
        "stale_evidence_warnings",
        "point_in_time_warnings",
        mode="before",
    )
    @classmethod
    def freeze_collections(cls, value: Iterable[Any] | None) -> tuple[Any, ...]:
        return _freeze_collection(value) or ()

    @field_validator("provenance", mode="before")
    @classmethod
    def freeze_provenance(cls, value: Mapping[str, Any] | None) -> FrozenDict:
        return _freeze_mapping(value) or FrozenDict()

    @field_validator("source_timestamps", mode="before")
    @classmethod
    def freeze_source_timestamps(cls, value: Mapping[str, datetime] | None) -> FrozenDict:
        if value is None:
            return FrozenDict()
        return FrozenDict({key: normalize_utc(timestamp) for key, timestamp in value.items()})

    @model_validator(mode="after")
    def validate_report(self) -> CompanyResearchReport:
        signal_ids = [assessment.signal_id for assessment in self.signal_assessments]
        if len(signal_ids) != len(set(signal_ids)):
            raise ValueError("signal_assessments must not contain duplicate signal IDs")
        unavailable = [signal_id.strip() for signal_id in self.unavailable_signals]
        if len(unavailable) != len(set(unavailable)):
            raise ValueError("unavailable_signals must not contain duplicate signal IDs")
        if not self.universe_eligible and not self.exclusion_reasons:
            raise ValueError("ineligible companies require at least one exclusion reason")
        sorted_assessments = tuple(sorted(self.signal_assessments, key=lambda item: item.signal_id))
        object.__setattr__(self, "signal_assessments", sorted_assessments)
        object.__setattr__(self, "unavailable_signals", tuple(unavailable))
        return self
