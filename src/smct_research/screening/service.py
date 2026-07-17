from __future__ import annotations

from datetime import UTC, datetime

from smct_research.core.models import FeatureSnapshot
from smct_research.core.signal import SignalRegistry
from smct_research.scoring.composite import CompositeResearchScorer
from smct_research.screening.models import RankedResult, UniverseEntry
from smct_research.screening.universe import UniversePolicy


class BatchEvaluationService:
    """Evaluate an offline universe deterministically, retaining coverage diagnostics."""

    def __init__(self, registry: SignalRegistry, scorer: CompositeResearchScorer) -> None:
        self.registry = registry
        self.scorer = scorer

    def evaluate(
        self,
        companies: list[UniverseEntry],
        snapshots: dict[str, FeatureSnapshot],
        policy: UniversePolicy,
        as_of: datetime | None = None,
        include_ineligible: bool = False,
    ) -> list[RankedResult]:
        evaluated_at = as_of or datetime.now(UTC)
        output: list[RankedResult] = []
        signals = list(self.registry.all())
        for company in companies:
            reasons = policy.exclusion_reasons(company)
            eligible = not reasons
            if not eligible and not include_ineligible:
                continue
            snapshot = snapshots.get(company.ticker)
            results = []
            unavailable = [signal.id for signal in signals]
            stale: list[str] = []
            point_in_time: list[str] = []
            if snapshot is None:
                point_in_time.append("missing_feature_snapshot")
            else:
                if snapshot.as_of > evaluated_at:
                    point_in_time.append("snapshot_as_of_after_evaluation")
                for source, source_time in snapshot.source_as_of.items():
                    if source_time > evaluated_at:
                        point_in_time.append(f"source_after_evaluation:{source}")
                    elif (evaluated_at - source_time).days > 90:
                        stale.append(f"stale_evidence:{source}")
                results = self.registry.evaluate_all(snapshot)
                unavailable = [s.id for s in signals if s.id not in {r.signal_id for r in results}]
            completeness = (100 * len(results) / len(signals)) if signals else 100.0
            score = self.scorer.score(results) if results else None
            output.append(
                RankedResult(
                    ticker=company.ticker,
                    company_name=company.display_name,
                    composite_score=score.score if score else None,
                    composite_confidence=score.confidence if score else None,
                    positive_signals=score.positive_signals if score else [],
                    negative_signals=score.negative_signals if score else [],
                    unavailable_signals=unavailable,
                    top_supporting_explanations=(score.explanations[:3] if score else []),
                    universe_eligible=eligible,
                    exclusion_reasons=reasons,
                    evaluation_timestamp=evaluated_at,
                    signals_evaluated=len(results),
                    signals_unavailable=len(unavailable),
                    feature_completeness_percentage=completeness,
                    stale_evidence_warnings=stale,
                    point_in_time_eligibility_warnings=point_in_time,
                    signal_results=results,
                )
            )
        ranked = sorted(
            output,
            key=lambda item: (
                not item.universe_eligible,
                item.composite_score is None,
                -(item.composite_score or 0),
                item.ticker,
            ),
        )
        rank = 0
        for result in ranked:
            if result.universe_eligible and result.composite_score is not None:
                rank += 1
                result.rank = rank
        return ranked
