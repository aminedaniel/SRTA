from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from smct_research.core.models import ResearchThesis, SignalDirection, ThesisStatus, normalize_utc

SCHEMA_VERSION = "research_report.v1"
THESIS_SCHEMA_VERSION = "thesis_record.v1"


def canonicalize(value: Any, *, sort_lists: bool = True) -> Any:
    """Return a JSON-compatible recursively canonical value.

    Dictionaries are sorted by key. Lists, tuples, and sets are recursively canonicalized; lists are
    sorted when their order is semantically unordered. Datetimes are UTC ISO strings and enums use
    their values. Ranked fields should pass ``sort_lists=False`` at their immediate list boundary.
    """
    if isinstance(value, BaseModel):
        return canonicalize(value.model_dump(mode="python"), sort_lists=sort_lists)
    if isinstance(value, datetime):
        return normalize_utc(value).isoformat().replace("+00:00", "Z")
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {
            str(k): canonicalize(v, sort_lists=sort_lists)
            for k, v in sorted(value.items(), key=lambda i: str(i[0]))
        }
    if isinstance(value, (list, tuple, set, frozenset)):
        items = [canonicalize(v, sort_lists=sort_lists) for v in value]
        if sort_lists:
            return sorted(
                items,
                key=lambda item: (
                    type(item).__name__,
                    json.dumps(item, sort_keys=True, separators=(",", ":"), ensure_ascii=False),
                ),
            )
        return items
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("numeric values must be finite")
        return value
    return value


def canonical_json(data: Any) -> str:
    return json.dumps(canonicalize(data), sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def content_hash(data: Any) -> str:
    return hashlib.sha256(canonical_json(data).encode()).hexdigest()


def _canonical_tuple(values: list[str] | tuple[str, ...]) -> tuple[str, ...]:
    return tuple(canonicalize(tuple(values)))


def _canonical_mapping(value: dict[str, Any]) -> dict[str, Any]:
    return dict(canonicalize(value))


class ImmutableModel(BaseModel):
    model_config = ConfigDict(frozen=True, validate_assignment=True)


class SignalAssessment(ImmutableModel):
    signal_id: str = Field(min_length=1)
    signal_name: str | None = None
    score: float | None = Field(default=None, ge=-100, le=100)
    confidence: float | None = Field(default=None, ge=0, le=1)
    direction: SignalDirection | None = None
    weighted_contribution: float | None = None
    thesis: str | None = None
    evidence: tuple[str, ...] = ()
    risks: tuple[str, ...] = ()
    metadata: dict[str, Any] = Field(default_factory=dict)
    evaluated_at: datetime | None = None
    availability: Literal["available", "unavailable", "excluded"] = "available"
    stale_evidence_warnings: tuple[str, ...] = ()
    point_in_time_warnings: tuple[str, ...] = ()

    @field_validator("signal_id")
    @classmethod
    def nonblank_id(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("signal_id is required")
        return value

    @field_validator("weighted_contribution")
    @classmethod
    def finite_weighted(cls, value: float | None) -> float | None:
        if value is not None and not math.isfinite(value):
            raise ValueError("weighted contribution must be finite")
        return value

    @model_validator(mode="after")
    def normalize_time(self) -> SignalAssessment:
        if self.evaluated_at is not None:
            object.__setattr__(self, "evaluated_at", normalize_utc(self.evaluated_at))
        object.__setattr__(self, "evidence", _canonical_tuple(self.evidence))
        object.__setattr__(self, "risks", _canonical_tuple(self.risks))
        object.__setattr__(self, "metadata", _canonical_mapping(self.metadata))
        object.__setattr__(
            self, "stale_evidence_warnings", _canonical_tuple(self.stale_evidence_warnings)
        )
        object.__setattr__(
            self, "point_in_time_warnings", _canonical_tuple(self.point_in_time_warnings)
        )
        return self


class ValuationSummary(ImmutableModel):
    current_market_price: float | None = Field(default=None, ge=0)
    reverse_dcf_conservative_value: float | None = Field(default=None, ge=0)
    reverse_dcf_base_value: float | None = Field(default=None, ge=0)
    reverse_dcf_optimistic_value: float | None = Field(default=None, ge=0)
    implied_revenue_growth: float | None = None
    implied_terminal_margin: float | None = None
    implied_expectations_gap: float | None = None
    valuation_compression_evidence: tuple[str, ...] = ()
    terminal_value_dependence: float | None = None
    dilution_or_share_count_risk: str | None = None
    missing_valuation_fields: tuple[str, ...] = ()

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
    def finite_optional(cls, value: float | None) -> float | None:
        if value is not None and not math.isfinite(value):
            raise ValueError("valuation numeric fields must be finite")
        return value

    @model_validator(mode="after")
    def canonical_lists(self) -> ValuationSummary:
        object.__setattr__(
            self,
            "valuation_compression_evidence",
            _canonical_tuple(self.valuation_compression_evidence),
        )
        object.__setattr__(
            self, "missing_valuation_fields", _canonical_tuple(self.missing_valuation_fields)
        )
        return self


class CompanyResearchReport(ImmutableModel):
    schema_version: str = SCHEMA_VERSION
    report_id: str
    ticker: str = Field(min_length=1)
    company_name: str = Field(min_length=1)
    universe_metadata: dict[str, Any]
    as_of: datetime
    universe_eligible: bool
    exclusion_reasons: tuple[str, ...] = ()
    rank: int | None = Field(default=None, ge=1)
    composite_research_score: float | None = Field(default=None, ge=0, le=100)
    composite_confidence: float | None = Field(default=None, ge=0, le=1)
    feature_completeness: float = Field(ge=0, le=100)
    executive_summary: str = Field(min_length=1)
    variant_perception: str = Field(min_length=1)
    signal_assessments: tuple[SignalAssessment, ...]
    supporting_evidence: tuple[str, ...] = ()
    contradictory_evidence: tuple[str, ...] = ()
    contextual_evidence: tuple[str, ...] = ()
    key_risks: tuple[str, ...] = ()
    catalysts: tuple[str, ...] = ()
    catalyst_summary: str = Field(min_length=1)
    invalidation_conditions: tuple[str, ...] = ()
    valuation_summary: ValuationSummary
    missing_evidence: tuple[str, ...] = ()
    unavailable_signals: tuple[str, ...] = ()
    stale_evidence_warnings: tuple[str, ...] = ()
    point_in_time_warnings: tuple[str, ...] = ()
    provenance: dict[str, str]
    source_timestamps: dict[str, datetime]
    candidate_thesis: ResearchThesis
    canonical_content_hash: str

    @field_validator(
        "report_id",
        "ticker",
        "company_name",
        "executive_summary",
        "variant_perception",
        "catalyst_summary",
    )
    @classmethod
    def nonblank_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("required text fields cannot be blank")
        return value

    @field_validator("composite_research_score", "composite_confidence", "feature_completeness")
    @classmethod
    def finite_scores(cls, value: float | None) -> float | None:
        if value is not None and not math.isfinite(value):
            raise ValueError("score fields must be finite")
        return value

    @model_validator(mode="after")
    def normalize_report(self) -> CompanyResearchReport:
        object.__setattr__(self, "ticker", self.ticker.upper().strip())
        object.__setattr__(self, "as_of", normalize_utc(self.as_of))
        list_fields = (
            "exclusion_reasons",
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
        )
        for field in list_fields:
            object.__setattr__(self, field, _canonical_tuple(getattr(self, field)))
        object.__setattr__(
            self,
            "signal_assessments",
            tuple(sorted(self.signal_assessments, key=lambda a: a.signal_id)),
        )
        signal_ids = [a.signal_id for a in self.signal_assessments]
        if len(signal_ids) != len(set(signal_ids)):
            raise ValueError("duplicate signal assessments are not allowed")
        if len(self.unavailable_signals) != len(set(self.unavailable_signals)):
            raise ValueError("duplicate unavailable signal ids are not allowed")
        if not self.universe_eligible and not self.exclusion_reasons:
            raise ValueError("ineligible companies require at least one exclusion reason")
        object.__setattr__(self, "universe_metadata", _canonical_mapping(self.universe_metadata))
        object.__setattr__(
            self,
            "provenance",
            dict(sorted({str(k): str(v) for k, v in self.provenance.items()}.items())),
        )
        object.__setattr__(
            self,
            "source_timestamps",
            {k: normalize_utc(v) for k, v in sorted(self.source_timestamps.items())},
        )
        return self


REPORT_ID_PREFIX = "report_"


def report_canonical_payload(
    report_or_data: CompanyResearchReport | dict[str, Any],
) -> dict[str, Any]:
    data = (
        report_or_data.model_dump(mode="python")
        if isinstance(report_or_data, CompanyResearchReport)
        else dict(report_or_data)
    )
    data.pop("report_id", None)
    data.pop("canonical_content_hash", None)
    return canonicalize(data)


def report_content_hash(report_or_data: CompanyResearchReport | dict[str, Any]) -> str:
    return content_hash(report_canonical_payload(report_or_data))


def report_id_for_payload(report_or_data: CompanyResearchReport | dict[str, Any]) -> str:
    return REPORT_ID_PREFIX + report_content_hash(report_or_data)[:24]


def validate_report_identity(report: CompanyResearchReport) -> None:
    expected_hash = report_content_hash(report)
    expected_id = report_id_for_payload(report)
    if report.canonical_content_hash != expected_hash:
        raise ValueError("research report content hash mismatch")
    if report.report_id != expected_id:
        raise ValueError("research report id mismatch")


class ThesisRecord(ImmutableModel):
    schema_version: str = THESIS_SCHEMA_VERSION
    thesis_id: str = Field(min_length=1)
    version: int = Field(ge=1)
    ticker: str = Field(min_length=1)
    thesis: ResearchThesis
    status: ThesisStatus
    effective_at: datetime
    known_at: datetime
    source_report_id: str = Field(min_length=1)
    source_report_as_of: datetime
    revision_reason: str = Field(min_length=1)
    prior_version: int | None = None
    canonical_content_hash: str
    created_at: datetime
    updated_at: datetime

    @model_validator(mode="after")
    def normalize_record(self) -> ThesisRecord:
        object.__setattr__(self, "ticker", self.ticker.upper().strip())
        object.__setattr__(self, "thesis_id", self.thesis_id.strip())
        object.__setattr__(self, "source_report_id", self.source_report_id.strip())
        for field in (
            "effective_at",
            "known_at",
            "source_report_as_of",
            "created_at",
            "updated_at",
        ):
            object.__setattr__(self, field, normalize_utc(getattr(self, field)))
        object.__setattr__(self, "revision_reason", self.revision_reason.strip())
        if not self.revision_reason:
            raise ValueError("revision reason is required")
        if self.known_at < self.effective_at:
            raise ValueError("known_at must be greater than or equal to effective_at")
        if self.version == 1 and self.prior_version is not None:
            raise ValueError("version 1 cannot reference a prior version")
        if self.version > 1 and self.prior_version != self.version - 1:
            raise ValueError("prior version must equal version - 1")
        return self
