from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from smct_research.core.models import ResearchThesis, SignalDirection, ThesisStatus, normalize_utc

SCHEMA_VERSION = "research_report.v1"
THESIS_SCHEMA_VERSION = "thesis_record.v1"


def canonical_json(data: Any) -> str:
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def content_hash(data: Any) -> str:
    return hashlib.sha256(canonical_json(data).encode()).hexdigest()


class ImmutableModel(BaseModel):
    model_config = ConfigDict(frozen=True)


class SignalAssessment(ImmutableModel):
    signal_id: str
    signal_name: str | None = None
    score: float | None = None
    confidence: float | None = None
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

    @model_validator(mode="after")
    def normalize_time(self) -> SignalAssessment:
        if self.evaluated_at is not None:
            object.__setattr__(self, "evaluated_at", normalize_utc(self.evaluated_at))
        return self


class ValuationSummary(ImmutableModel):
    current_market_price: float | None = None
    reverse_dcf_conservative_value: float | None = None
    reverse_dcf_base_value: float | None = None
    reverse_dcf_optimistic_value: float | None = None
    implied_revenue_growth: float | None = None
    implied_terminal_margin: float | None = None
    implied_expectations_gap: float | None = None
    valuation_compression_evidence: tuple[str, ...] = ()
    terminal_value_dependence: float | None = None
    dilution_or_share_count_risk: str | None = None
    missing_valuation_fields: tuple[str, ...] = ()


class CompanyResearchReport(ImmutableModel):
    schema_version: str = SCHEMA_VERSION
    report_id: str
    ticker: str
    company_name: str
    universe_metadata: dict[str, Any]
    as_of: datetime
    universe_eligible: bool
    exclusion_reasons: tuple[str, ...] = ()
    rank: int | None = None
    composite_research_score: float | None = None
    composite_confidence: float | None = None
    feature_completeness: float
    executive_summary: str
    variant_perception: str
    signal_assessments: tuple[SignalAssessment, ...]
    supporting_evidence: tuple[str, ...] = ()
    contradictory_evidence: tuple[str, ...] = ()
    key_risks: tuple[str, ...] = ()
    catalysts: tuple[str, ...] = ()
    catalyst_summary: str
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

    @model_validator(mode="after")
    def normalize_report(self) -> CompanyResearchReport:
        object.__setattr__(self, "ticker", self.ticker.upper().strip())
        object.__setattr__(self, "as_of", normalize_utc(self.as_of))
        object.__setattr__(
            self,
            "source_timestamps",
            {k: normalize_utc(v) for k, v in self.source_timestamps.items()},
        )
        return self


class ThesisRecord(ImmutableModel):
    schema_version: str = THESIS_SCHEMA_VERSION
    thesis_id: str
    version: int = Field(ge=1)
    ticker: str
    thesis: ResearchThesis
    status: ThesisStatus
    effective_at: datetime
    known_at: datetime
    source_report_id: str
    source_report_as_of: datetime
    revision_reason: str
    prior_version: int | None = None
    canonical_content_hash: str
    created_at: datetime
    updated_at: datetime

    @model_validator(mode="after")
    def normalize_record(self) -> ThesisRecord:
        object.__setattr__(self, "ticker", self.ticker.upper().strip())
        for field in (
            "effective_at",
            "known_at",
            "source_report_as_of",
            "created_at",
            "updated_at",
        ):
            object.__setattr__(self, field, normalize_utc(getattr(self, field)))
        if not self.revision_reason.strip():
            raise ValueError("revision reason is required")
        if self.version == 1 and self.prior_version is not None:
            raise ValueError("version 1 cannot reference a prior version")
        if self.version > 1 and self.prior_version != self.version - 1:
            raise ValueError("prior version must equal version - 1")
        return self
