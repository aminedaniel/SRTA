"""User-authored theses, explicit review rules and catalyst tracking."""

from __future__ import annotations

from datetime import UTC, date, datetime
from enum import StrEnum
from math import isfinite

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from smct_research.core.models import FeatureSnapshot, ResearchThesis, ThesisStatus, normalize_utc
from smct_research.research.builder import report_id
from smct_research.research.models import CompanyResearchReport


class Catalyst(BaseModel):
    model_config = ConfigDict(extra="forbid")
    description: str = Field(min_length=1)
    due_date: date | None = None
    status: str = Field(default="pending", pattern="^(pending|occurred|cancelled)$")
    outcome: str = ""


class RuleOperator(StrEnum):
    LT = "lt"
    LE = "le"
    GT = "gt"
    GE = "ge"


class InvalidationRule(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    feature: str = Field(min_length=1)
    operator: RuleOperator
    threshold: float
    description: str = Field(min_length=1)


class ThesisDocument(ResearchThesis):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    revision: int = Field(default=0, ge=0)
    report_id: str | None = None
    catalysts: list[Catalyst] = Field(default_factory=list)
    rules: list[InvalidationRule] = Field(default_factory=list)
    change_note: str = "Initial draft"

    @field_validator("ticker")
    @classmethod
    def ticker_valid(cls, value: str) -> str:
        value = value.upper().strip()
        if not value or len(value) > 12 or not all(c.isalnum() or c in ".-^" for c in value):
            raise ValueError("Ticker must be 1–12 letters, digits, dots, hyphens or carets")
        return value

    @field_validator("created_at", "updated_at")
    @classmethod
    def utc_times(cls, value: datetime) -> datetime:
        return normalize_utc(value)

    @model_validator(mode="after")
    def validate_thesis(self) -> ThesisDocument:
        values = [
            x
            for x in (self.valuation_low, self.valuation_base, self.valuation_high)
            if x is not None
        ]
        if values != sorted(values):
            raise ValueError("Valuation scenarios must be ordered low <= base <= high")
        if not self.title.strip() or not self.change_note.strip():
            raise ValueError("Title and change_note cannot be blank")
        if self.status != ThesisStatus.DRAFT:
            if not self.variant_perception.strip() or not any(
                s.strip() for s in self.supporting_evidence
            ):
                raise ValueError(
                    "A non-draft thesis requires variant perception and supporting evidence"
                )
            if not any(s.strip() for s in self.key_risks):
                raise ValueError("A non-draft thesis requires key risks")
            if not self.rules and not any(s.strip() for s in self.invalidation_conditions):
                raise ValueError("A non-draft thesis requires invalidation conditions or rules")
        return self


def draft_thesis(report: CompanyResearchReport) -> ThesisDocument:
    valuation = report.valuation
    return ThesisDocument(
        ticker=report.company.ticker,
        title=f"{report.company.name}: 30-month research thesis",
        variant_perception="",
        supporting_evidence=list(report.supporting_evidence),
        key_risks=list(report.key_risks),
        invalidation_conditions=[],
        valuation_low=valuation.reverse_dcf_conservative_value if valuation else None,
        valuation_base=valuation.reverse_dcf_base_value if valuation else None,
        valuation_high=valuation.reverse_dcf_optimistic_value if valuation else None,
        report_id=report_id(report),
    )


def review_thesis(
    thesis: ThesisDocument, snapshot: FeatureSnapshot | None, as_of: datetime
) -> dict[str, object]:
    """Produce review alerts; never silently mutate a user's thesis status."""
    as_of = normalize_utc(as_of)
    if snapshot and snapshot.ticker != thesis.ticker:
        raise ValueError("Thesis and snapshot ticker must match")
    alerts: list[dict[str, object]] = []
    stale_or_future = (
        snapshot is None
        or snapshot.as_of > as_of
        or ((as_of - snapshot.as_of).days > 90)
        or any(
            stamp > as_of or (as_of - stamp).days > 90 for stamp in snapshot.source_as_of.values()
        )
    )
    for rule in thesis.rules:
        value = snapshot.values.get(rule.feature) if snapshot else None
        valid = isinstance(value, (int, float)) and not isinstance(value, bool) and isfinite(value)
        if not valid or stale_or_future:
            state = "unavailable"
        else:
            assert isinstance(value, (int, float))
            ops = {
                RuleOperator.LT: value < rule.threshold,
                RuleOperator.LE: value <= rule.threshold,
                RuleOperator.GT: value > rule.threshold,
                RuleOperator.GE: value >= rule.threshold,
            }
            state = "triggered" if ops[rule.operator] else "not_triggered"
        alerts.append(
            {
                "feature": rule.feature,
                "condition": f"{rule.operator} {rule.threshold}",
                "value": value if valid and not stale_or_future else None,
                "description": rule.description,
                "state": state,
            }
        )
    catalysts = [
        item.model_dump(mode="json")
        for item in thesis.catalysts
        if item.status == "pending" and item.due_date and item.due_date <= as_of.date()
    ]
    return {
        "ticker": thesis.ticker,
        "as_of": as_of.isoformat(),
        "status": thesis.status.value,
        "revision": thesis.revision,
        "rule_alerts": alerts,
        "due_catalysts": catalysts,
        "manual_review_conditions": thesis.invalidation_conditions,
        "reviewed_at": datetime.now(UTC).isoformat(),
    }
