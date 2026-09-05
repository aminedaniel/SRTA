"""Deterministic research reports built from the same evidence as the ranked screen."""

from __future__ import annotations

import hashlib
import json
from math import isfinite

from smct_research.core.models import FeatureSnapshot, SignalDirection
from smct_research.core.signal import SignalRegistry
from smct_research.research.models import (
    CompanyIdentity,
    CompanyResearchReport,
    FrozenDict,
    SignalAssessment,
    SignalAvailability,
    ValuationSummary,
)
from smct_research.scoring.composite import CompositeResearchScorer
from smct_research.screening.models import RankedResult, UniverseEntry

SCHEMA_VERSION = "1.0"
MODEL_VERSION = "smct-0.3"


def canonical_json(payload: object) -> str:
    return json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    )


def report_id(report: CompanyResearchReport) -> str:
    return hashlib.sha256(canonical_json(report.model_dump(mode="json")).encode()).hexdigest()


def build_report(
    company: UniverseEntry,
    snapshot: FeatureSnapshot | None,
    ranked: RankedResult,
    registry: SignalRegistry,
    scorer: CompositeResearchScorer,
) -> CompanyResearchReport:
    if company.ticker != ranked.ticker or (snapshot and snapshot.ticker != company.ticker):
        raise ValueError("Company, snapshot and ranked result must have the same ticker")
    if (
        snapshot
        and (
            snapshot.as_of > ranked.evaluation_timestamp
            or any(stamp > ranked.evaluation_timestamp for stamp in snapshot.source_as_of.values())
        )
        and ranked.signal_results
    ):
        raise ValueError("Future evidence cannot produce evaluated signals")
    available = {item.signal_id: item for item in ranked.signal_results}
    total_weight = sum(
        item.confidence * scorer.weights.get(item.signal_id, 1.0) for item in available.values()
    )
    assessments: list[SignalAssessment] = []
    missing: list[str] = list(ranked.signal_diagnostics)
    supporting: list[str] = []
    contradictory: list[str] = []
    context: list[str] = []
    risks: list[str] = []
    for signal in registry.all():
        result = available.get(signal.id)
        if result is None:
            absent = [
                name
                for name in signal.required_features
                if snapshot is None or snapshot.values.get(name) is None
            ]
            reason = (
                f"{signal.id}: missing {', '.join(absent)}"
                if absent
                else (f"{signal.id}: unavailable; see data-quality diagnostics")
            )
            missing.append(reason)
            assessments.append(
                SignalAssessment(
                    signal_id=signal.id,
                    signal_name=signal.name,
                    availability=SignalAvailability.UNAVAILABLE,
                    thesis=reason,
                )
            )
            continue
        contribution = (
            (
                result.score
                * result.confidence
                * scorer.weights.get(signal.id, 1.0)
                / (2 * total_weight)
            )
            if total_weight
            else 0.0
        )
        assessments.append(
            SignalAssessment(
                signal_id=signal.id,
                signal_name=signal.name,
                score=result.score,
                confidence=result.confidence,
                direction=result.direction,
                weighted_contribution=contribution,
                thesis=result.thesis,
                evidence=tuple(result.evidence),
                risks=tuple(result.risks),
                metadata=FrozenDict(result.metadata),
                evaluated_at=result.evaluated_at,
                availability=SignalAvailability.AVAILABLE,
            )
        )
        target = (
            context
            if signal.id in {"M1", "I1", "E2", "E3"}
            else (
                supporting
                if result.direction == SignalDirection.POSITIVE
                else contradictory
                if result.direction == SignalDirection.NEGATIVE
                else context
            )
        )
        target.extend(f"{signal.id}: {value}" for value in [result.thesis, *result.evidence])
        risks.extend(result.risks)
    safe_snapshot = snapshot if not ranked.point_in_time_eligibility_warnings else None
    valuation = _valuation(safe_snapshot, available["A1"].evidence if "A1" in available else [])
    score_text = (
        f"Research priority {ranked.composite_score:.1f}/100"
        if ranked.composite_score is not None
        else "Research priority unavailable"
    )
    summary = (
        f"{score_text}; {ranked.signals_evaluated} of "
        f"{ranked.signals_evaluated + ranked.signals_unavailable} signals evaluated. "
        "Scores rank research effort; they are not return forecasts or buy recommendations."
    )
    if not ranked.universe_eligible:
        summary += " Outside the configured discovery universe."
    if ranked.stale_evidence_warnings:
        summary += " Evidence needs a freshness review."
    return CompanyResearchReport(
        schema_version=SCHEMA_VERSION,
        company=CompanyIdentity.model_validate(company.model_dump()),
        as_of=ranked.evaluation_timestamp,
        universe_eligible=ranked.universe_eligible,
        exclusion_reasons=tuple(ranked.exclusion_reasons),
        rank=ranked.rank,
        composite_score=ranked.composite_score,
        composite_confidence=ranked.composite_confidence,
        feature_completeness_percentage=ranked.feature_completeness_percentage,
        executive_summary=summary,
        signal_assessments=tuple(assessments),
        supporting_evidence=tuple(supporting),
        contradictory_evidence=tuple(contradictory),
        contextual_evidence=tuple(context),
        key_risks=tuple(dict.fromkeys(risks)),
        valuation=valuation,
        missing_evidence=tuple(missing),
        unavailable_signals=tuple(ranked.unavailable_signals),
        stale_evidence_warnings=tuple(ranked.stale_evidence_warnings),
        point_in_time_warnings=tuple(ranked.point_in_time_eligibility_warnings),
        provenance=FrozenDict(
            {
                "model_version": MODEL_VERSION,
                "signal_weights": scorer.weights,
                "sources": snapshot.sources if snapshot else {},
                "financial_periods": {
                    key: value
                    for key, value in snapshot.values.items()
                    if key.startswith("period:")
                }
                if snapshot
                else {},
                "snapshot_hash": hashlib.sha256(
                    canonical_json(snapshot.model_dump(mode="json")).encode()
                ).hexdigest()
                if snapshot
                else None,
                "feature_snapshot_as_of": snapshot.as_of.isoformat() if snapshot else None,
            }
        ),
        source_timestamps=FrozenDict(snapshot.source_as_of if snapshot else {}),
    )


def _valuation(snapshot: FeatureSnapshot | None, evidence: list[str]) -> ValuationSummary:
    fields = {
        "current_market_price": "current_price",
        "reverse_dcf_conservative_value": "dcf_conservative_value_per_share",
        "reverse_dcf_base_value": "dcf_base_value_per_share",
        "reverse_dcf_optimistic_value": "dcf_optimistic_value_per_share",
        "implied_revenue_growth": "reverse_dcf_implied_revenue_cagr",
        "implied_terminal_margin": "reverse_dcf_implied_terminal_fcf_margin",
        "implied_expectations_gap": "reverse_dcf_growth_gap",
        "terminal_value_dependence": "dcf_terminal_value_share",
    }
    values: dict[str, object] = {}
    absent: list[str] = []
    for destination, source in fields.items():
        raw = snapshot.values.get(source) if snapshot else None
        numeric = isinstance(raw, (float, int)) and not isinstance(raw, bool)
        if numeric and isfinite(float(raw)):  # type: ignore[arg-type]
            if ("value" in destination or destination == "current_market_price") and raw < 0:  # type: ignore[operator]
                absent.append(source)
            else:
                values[destination] = raw
        else:
            absent.append(source)
    values["missing_valuation_fields"] = absent
    values["valuation_compression_evidence"] = evidence
    dilution = snapshot.values.get("dcf_annual_dilution") if snapshot else None
    if isinstance(dilution, (int, float)) and not isinstance(dilution, bool) and isfinite(dilution):
        values["dilution_or_share_count_risk"] = f"Annual dilution assumption: {dilution:.1%}."
    return ValuationSummary.model_validate(values)
