from __future__ import annotations

from smct_research.core.models import FeatureSnapshot, SignalDirection, SignalResult
from smct_research.core.signal import ResearchSignal


class RedditAwarenessSignal(ResearchSignal):
    """Measures underfollowed improvement and speculative crowding, not raw bullishness."""

    id = "F1"
    name = "Reddit Awareness and Crowding"
    required_features = (
        "reddit_mentions_30d",
        "reddit_mentions_percentile",
        "operating_momentum_score",
        "promotional_language_share",
    )

    def evaluate(self, snapshot: FeatureSnapshot) -> SignalResult:
        self.validate(snapshot)
        mentions = snapshot.require_float("reddit_mentions_30d")
        attention = snapshot.require_float("reddit_mentions_percentile")
        operating_momentum = snapshot.require_float("operating_momentum_score")
        promotion = snapshot.require_float("promotional_language_share")

        underfollowed = max(0.0, operating_momentum - attention)
        crowding = max(0.0, attention + promotion - operating_momentum)
        raw_score = (underfollowed * 0.9) - (crowding * 1.1)
        score = max(-100.0, min(100.0, raw_score))
        confidence = min(0.9, 0.4 + min(mentions, 500) / 1000)

        if score >= 20:
            direction = SignalDirection.POSITIVE
            thesis = (
                "Operating momentum is stronger than Reddit awareness, suggesting "
                "an underfollowed setup."
            )
        elif score <= -20:
            direction = SignalDirection.NEGATIVE
            thesis = "Reddit attention and promotional intensity exceed operating momentum."
        else:
            direction = SignalDirection.NEUTRAL
            thesis = "Reddit awareness is broadly consistent with operating momentum."

        return SignalResult(
            signal_id=self.id,
            ticker=snapshot.ticker,
            score=score,
            confidence=confidence,
            direction=direction,
            thesis=thesis,
            evidence=[
                f"Thirty-day Reddit mentions: {mentions:.0f}.",
                (
                    f"Attention percentile: {attention:.0f}; operating momentum: "
                    f"{operating_momentum:.0f}."
                ),
                f"Promotional-language share: {promotion:.0f}.",
            ],
            risks=["Reddit data may be sparse or vulnerable to coordinated promotion."],
            metadata={"underfollowed": underfollowed, "crowding": crowding},
        )
