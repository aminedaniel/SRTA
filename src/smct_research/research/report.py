from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Any

from smct_research.core.models import (
    FeatureSnapshot,
    ResearchThesis,
    SignalDirection,
    SignalResult,
    normalize_utc,
)
from smct_research.core.signal import ResearchSignal
from smct_research.research.models import (
    CompanyResearchReport,
    SignalAssessment,
    ValuationSummary,
    content_hash,
)
from smct_research.scoring.composite import CompositeResearchScorer
from smct_research.screening.models import RankedResult, UniverseEntry


def signal_weighted_contribution(result: SignalResult, weight: float) -> float:
    return result.score * result.confidence * weight


def _sorted_unique(items: list[str] | tuple[str, ...]) -> tuple[str, ...]:
    return tuple(sorted(dict.fromkeys(str(i) for i in items if str(i).strip())))


class ResearchReportBuilder:
    def __init__(self, scorer: CompositeResearchScorer, signals: list[ResearchSignal]) -> None:
        self.scorer = scorer
        self.signals = {s.id: s for s in signals}

    def build(
        self,
        company: UniverseEntry,
        snapshot: FeatureSnapshot | None,
        ranked: RankedResult,
        as_of: datetime,
    ) -> CompanyResearchReport:
        ts = normalize_utc(as_of)
        by_id = {r.signal_id: r for r in ranked.signal_results}
        assessments: list[SignalAssessment] = []
        for sid in sorted(set(self.signals) | set(by_id) | set(ranked.unavailable_signals)):
            sig = self.signals.get(sid)
            result = by_id.get(sid)
            if result:
                assessments.append(
                    SignalAssessment(
                        signal_id=sid,
                        signal_name=getattr(sig, "name", None),
                        score=result.score,
                        confidence=result.confidence,
                        direction=result.direction,
                        weighted_contribution=signal_weighted_contribution(
                            result, self.scorer.weights.get(sid, 1.0)
                        ),
                        thesis=result.thesis,
                        evidence=tuple(result.evidence),
                        risks=tuple(result.risks),
                        metadata=dict(sorted(result.metadata.items())),
                        evaluated_at=result.evaluated_at,
                        stale_evidence_warnings=tuple(ranked.stale_evidence_warnings),
                        point_in_time_warnings=tuple(ranked.point_in_time_eligibility_warnings),
                    )
                )
            else:
                assessments.append(
                    SignalAssessment(
                        signal_id=sid,
                        signal_name=getattr(sig, "name", None),
                        availability="unavailable",
                        point_in_time_warnings=tuple(ranked.point_in_time_eligibility_warnings),
                    )
                )
        available = [a for a in assessments if a.availability == "available"]
        positives = sorted(
            [a for a in available if a.direction == SignalDirection.POSITIVE],
            key=lambda a: (-(a.weighted_contribution or 0), a.signal_id),
        )
        negatives = sorted(
            [a for a in available if a.direction == SignalDirection.NEGATIVE],
            key=lambda a: ((a.weighted_contribution or 0), a.signal_id),
        )
        support = tuple(f"{a.signal_id}: {e}" for a in positives for e in a.evidence)
        contra = tuple(f"{a.signal_id}: {e}" for a in negatives for e in a.evidence)
        risks = _sorted_unique(
            [f"{a.signal_id}: {r}" for a in available for r in a.risks]
            + list(ranked.exclusion_reasons)
        )
        catalysts = self._catalysts(available)
        missing = _sorted_unique(
            [f"{sid}: unavailable" for sid in ranked.unavailable_signals]
            + ranked.signal_diagnostics
        )
        valuation = self._valuation(snapshot, available)
        invalidations = self._invalidations(positives, valuation)
        variant = self._variant(positives, negatives, valuation, missing)
        summary = self._summary(ranked, positives, negatives, valuation, missing)
        thesis = ResearchThesis(
            ticker=company.ticker,
            title=f"{company.ticker} point-in-time research thesis",
            variant_perception=variant,
            supporting_evidence=list(support[:5]),
            key_risks=list(risks[:5]),
            invalidation_conditions=list(invalidations),
            valuation_low=valuation.reverse_dcf_conservative_value,
            valuation_base=valuation.reverse_dcf_base_value,
            valuation_high=valuation.reverse_dcf_optimistic_value,
            created_at=ts,
            updated_at=ts,
        )
        source_ts = dict(sorted((snapshot.source_as_of if snapshot else {}).items()))
        provenance = dict(sorted((snapshot.sources if snapshot else {}).items()))
        base: dict[str, Any] = {
            "ticker": company.ticker,
            "as_of": ts.isoformat(),
            "rank": ranked.rank,
            "score": ranked.composite_score,
            "confidence": ranked.composite_confidence,
            "signals": [a.model_dump(mode="json") for a in assessments],
            "valuation": valuation.model_dump(mode="json"),
            "eligible": ranked.universe_eligible,
            "exclusions": sorted(ranked.exclusion_reasons),
        }
        report_id = (
            "report_"
            + hashlib.sha256(
                __import__("json").dumps(base, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest()[:24]
        )
        payload_hash = content_hash(
            {**base, "report_id": report_id, "thesis": thesis.model_dump(mode="json")}
        )
        return CompanyResearchReport(
            report_id=report_id,
            ticker=company.ticker,
            company_name=company.display_name,
            universe_metadata={
                "sector": company.sector,
                "industry": company.industry,
                "exchange": company.exchange,
                "market_cap_usd": company.market_cap_usd,
            },
            as_of=ts,
            universe_eligible=ranked.universe_eligible,
            exclusion_reasons=tuple(sorted(ranked.exclusion_reasons)),
            rank=ranked.rank,
            composite_research_score=ranked.composite_score,
            composite_confidence=ranked.composite_confidence,
            feature_completeness=ranked.feature_completeness_percentage,
            executive_summary=summary,
            variant_perception=variant,
            signal_assessments=tuple(assessments),
            supporting_evidence=support,
            contradictory_evidence=contra,
            key_risks=risks,
            catalysts=catalysts,
            catalyst_summary="No evidence-backed catalysts are currently recorded."
            if not catalysts
            else "Evidence-backed catalysts are recorded.",
            invalidation_conditions=invalidations,
            valuation_summary=valuation,
            missing_evidence=missing,
            unavailable_signals=tuple(sorted(ranked.unavailable_signals)),
            stale_evidence_warnings=tuple(sorted(ranked.stale_evidence_warnings)),
            point_in_time_warnings=tuple(sorted(ranked.point_in_time_eligibility_warnings)),
            provenance=provenance,
            source_timestamps=source_ts,
            candidate_thesis=thesis,
            canonical_content_hash=payload_hash,
        )

    def _valuation(
        self, snapshot: FeatureSnapshot | None, assessments: list[SignalAssessment]
    ) -> ValuationSummary:
        values = snapshot.values if snapshot else {}
        fields = {
            "current_market_price": "market_price",
            "reverse_dcf_conservative_value": "dcf_conservative_value_per_share",
            "reverse_dcf_base_value": "dcf_base_value_per_share",
            "reverse_dcf_optimistic_value": "dcf_optimistic_value_per_share",
            "implied_revenue_growth": "reverse_dcf_implied_revenue_cagr",
            "implied_terminal_margin": "reverse_dcf_implied_terminal_fcf_margin",
            "implied_expectations_gap": "reverse_dcf_growth_gap",
            "terminal_value_dependence": "dcf_terminal_value_share",
        }
        data: dict[str, float | None] = {}
        for out, key in fields.items():
            value = values.get(key)
            data[out] = (
                float(value)
                if isinstance(value, (int, float)) and not isinstance(value, bool)
                else None
            )
        missing = tuple(sorted(k for k, v in fields.items() if values.get(v) is None))
        comp = tuple(e for a in assessments if a.signal_id == "A1" for e in a.evidence)
        dilution = values.get("dcf_annual_dilution")
        risk = (
            f"Annual dilution assumption: {float(dilution):.1%}."
            if isinstance(dilution, (int, float))
            else None
        )
        return ValuationSummary(
            **data,
            valuation_compression_evidence=comp,
            dilution_or_share_count_risk=risk,
            missing_valuation_fields=missing,
        )

    def _catalysts(self, assessments: list[SignalAssessment]) -> tuple[str, ...]:
        out: list[str] = []
        for a in assessments:
            for key in ("catalyst", "catalysts", "upcoming_events", "expected_event"):
                val = a.metadata.get(key)
                if isinstance(val, str):
                    out.append(f"{a.signal_id}: {val}")
                elif isinstance(val, list):
                    out += [f"{a.signal_id}: {x}" for x in val if isinstance(x, str)]
        return _sorted_unique(out)

    def _invalidations(
        self, positives: list[SignalAssessment], valuation: ValuationSummary
    ) -> tuple[str, ...]:
        out = [
            f"{a.signal_id}: principal positive evidence becomes stale, unavailable, or contradicted."
            for a in positives[:3]
        ]
        if valuation.reverse_dcf_base_value is not None:
            out.append(
                "A2: reverse-DCF expectations gap closes because price rises or operating assumptions weaken."
            )
        if valuation.dilution_or_share_count_risk:
            out.append(
                "A2: dilution or share-count risk exceeds the level recorded in valuation evidence."
            )
        return tuple(out) or (
            "Evidence base remains insufficient; reassess if primary signals become unavailable or contradicted.",
        )

    def _variant(
        self,
        positives: list[SignalAssessment],
        negatives: list[SignalAssessment],
        valuation: ValuationSummary,
        missing: tuple[str, ...],
    ) -> str:
        if not positives:
            return "Evidence is incomplete and does not yet support a differentiated thesis."
        if (
            valuation.implied_expectations_gap is not None
            and valuation.implied_expectations_gap <= 0
            and positives
        ):
            return (
                "The market appears to price weaker growth than current signal evidence supports."
            )
        if negatives:
            return "Operational evidence is constructive, but negative signals or demanding assumptions limit differentiation."
        if missing:
            return "Positive signal evidence exists, but missing evidence limits thesis confidence."
        return "Positive signal evidence supports a differentiated research thesis."

    def _summary(
        self,
        ranked: RankedResult,
        positives: list[SignalAssessment],
        negatives: list[SignalAssessment],
        valuation: ValuationSummary,
        missing: tuple[str, ...],
    ) -> str:
        priority = (
            "high"
            if (ranked.composite_score or 0) >= 60
            else "moderate"
            if ranked.composite_score is not None
            else "unscored"
        )
        parts = [
            f"The company ranks as a {priority} research priority with composite score {ranked.composite_score if ranked.composite_score is not None else 'Missing'} and confidence {ranked.composite_confidence if ranked.composite_confidence is not None else 'Missing'}."
        ]
        if positives:
            parts.append(
                "Top positive contributors: " + ", ".join(a.signal_id for a in positives[:3]) + "."
            )
        if negatives:
            parts.append(
                "Top negative contributors: " + ", ".join(a.signal_id for a in negatives[:3]) + "."
            )
        if valuation.missing_valuation_fields:
            parts.append("Some valuation fields are Missing.")
        if missing:
            parts.append("Unavailable signals materially constrain interpretation.")
        return " ".join(parts)
