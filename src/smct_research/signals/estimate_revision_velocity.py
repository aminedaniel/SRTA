from __future__ import annotations

from smct_research.core.models import FeatureSnapshot, SignalDirection, SignalResult
from smct_research.core.signal import ResearchSignal


class ConsensusEstimateRevisionSignal(ResearchSignal):
    id = "A3"
    name = "Consensus Estimate Revision Velocity"
    required_features = ("eps_revision_30d", "eps_revision_quality_score")

    def evaluate(self, snapshot: FeatureSnapshot) -> SignalResult:
        self.validate(snapshot)
        rev = snapshot.require_float("eps_revision_30d")
        quality = snapshot.require_float("eps_revision_quality_score")
        accel = snapshot.values.get("eps_revision_acceleration", 0)
        revenue = snapshot.values.get("revenue_revision_30d")
        raw_streak = snapshot.values.get("eps_revision_streak", 0)
        streak = float(raw_streak) if isinstance(raw_streak, (int, float)) else 0.0
        dispersion = snapshot.values.get("eps_dispersion_change_ratio_30d", 0)
        age = snapshot.values.get("eps_consensus_age_days", 0)
        score = (
            15 * rev
            + 6 * float(accel if isinstance(accel, (float, int)) else 0)
            + 4 * float(revenue if isinstance(revenue, (float, int)) else 0)
            + min(3, abs(int(streak))) * (1 if streak > 0 else -1)
            - 4 * max(float(dispersion or 0), 0)
            - min(5, max(float(age or 0) - 30, 0) / 10)
        )
        score = max(-25.0, min(25.0, score))
        direction = (
            SignalDirection.POSITIVE
            if score > 2
            else SignalDirection.NEGATIVE
            if score < -2
            else SignalDirection.NEUTRAL
        )
        return SignalResult(
            signal_id=self.id,
            ticker=snapshot.ticker,
            score=score,
            confidence=quality,
            direction=direction,
            thesis="Consensus estimates are improving."
            if score > 2
            else "Consensus estimates are deteriorating or stale.",
            evidence=[f"30-day EPS revision: {rev:.1%}.", f"Quality score: {quality:.0%}."],
            risks=["Consensus estimates are evidence, not trade instructions."],
            metadata={
                "raw_score": score,
                "quality": quality,
                "confidence_applied_by_composite": True,
            },
        )
