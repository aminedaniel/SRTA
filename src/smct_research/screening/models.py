"""Models used by the offline, point-in-time ranked discovery workflow."""

from __future__ import annotations

from datetime import UTC, datetime

from pydantic import BaseModel, Field

from smct_research.core.models import Company, SignalResult


class UniverseEntry(Company):
    """Canonical row accepted by the universe JSON and CSV readers."""

    company_name: str | None = None

    @property
    def display_name(self) -> str:
        return self.company_name or self.name


class FeatureAssemblyInput(BaseModel):
    ticker: str
    as_of: datetime
    values: dict[str, float | int | str | bool | None] = Field(default_factory=dict)
    sources: dict[str, str] = Field(default_factory=dict)
    source_as_of: dict[str, datetime] = Field(default_factory=dict)


class RankedResult(BaseModel):
    rank: int | None = None
    ticker: str
    company_name: str
    composite_score: float | None = None
    composite_confidence: float | None = None
    positive_signals: list[str] = Field(default_factory=list)
    negative_signals: list[str] = Field(default_factory=list)
    unavailable_signals: list[str] = Field(default_factory=list)
    top_supporting_explanations: list[str] = Field(default_factory=list)
    universe_eligible: bool
    exclusion_reasons: list[str] = Field(default_factory=list)
    evaluation_timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    signals_evaluated: int = 0
    signals_unavailable: int = 0
    feature_completeness_percentage: float = Field(ge=0, le=100)
    stale_evidence_warnings: list[str] = Field(default_factory=list)
    point_in_time_eligibility_warnings: list[str] = Field(default_factory=list)
    signal_results: list[SignalResult] = Field(default_factory=list)
