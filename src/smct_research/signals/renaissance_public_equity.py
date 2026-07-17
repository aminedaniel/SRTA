"""Weak corroboration from Renaissance Technologies' public Form 13F activity."""

from __future__ import annotations

from smct_research.core.models import FeatureSnapshot, SignalDirection, SignalResult
from smct_research.core.signal import ResearchSignal


class RenaissancePublicEquityActivitySignal(ResearchSignal):
    id = "I1"
    name = "Renaissance Public Equity Activity"
    required_features = (
        "renaissance_13f_status",
        "renaissance_13f_share_change_percent",
        "renaissance_13f_consecutive_quarters_held",
        "renaissance_13f_position_size_percentile",
        "renaissance_13f_disclosure_age_days",
        "renaissance_13f_lag_decay",
    )

    def evaluate(self, snapshot: FeatureSnapshot) -> SignalResult:
        self.validate(snapshot)
        status = str(snapshot.values["renaissance_13f_status"])
        change = snapshot.require_float("renaissance_13f_share_change_percent")
        held = max(0.0, snapshot.require_float("renaissance_13f_consecutive_quarters_held"))
        percentile = max(
            0.0, min(100.0, snapshot.require_float("renaissance_13f_position_size_percentile"))
        )
        age = max(0.0, snapshot.require_float("renaissance_13f_disclosure_age_days"))
        decay = max(0.0, min(1.0, snapshot.require_float("renaissance_13f_lag_decay")))
        raw = 0.0
        if status == "new":
            raw = 3.5
        elif status == "increased" and change >= 20:
            raw = 3.0
        elif status == "increased":
            raw = 1.5
        elif status == "reduced":
            raw = -1.5
        elif status == "exited":
            raw = -2.0
        if held >= 2 and status not in {"exited", "reduced"}:
            raw += min(1.0, 0.3 * (held - 1))
        raw += 0.5 * (percentile / 100) if raw > 0 else 0.0
        score = max(-5.0, min(5.0, raw * decay))
        direction = (
            SignalDirection.POSITIVE
            if score >= 0.75
            else SignalDirection.NEGATIVE
            if score <= -0.75
            else SignalDirection.NEUTRAL
        )
        return SignalResult(
            signal_id=self.id,
            ticker=snapshot.ticker,
            score=score,
            confidence=min(0.45, 0.15 + 0.30 * decay),
            direction=direction,
            thesis="Renaissance public equity disclosure is weak, delayed corroboration only.",
            evidence=[
                f"13F activity: {status}; share-count change: {change:.1f}%.",
                f"Consecutive disclosed quarters held: {held:.0f}; position-size percentile: {percentile:.0f}.",  # noqa: E501
                f"Disclosure age: {age:.0f} days; lag-adjusted decay: {decay:.2f}.",
            ],
            risks=[
                "13F data cannot identify Medallion holdings.",
                "13F filings exclude shorts and many derivatives.",
                "This is delayed manager-level public disclosure, not a real-time position feed.",
            ],
            metadata={
                "status": status,
                "share_change_percent": change,
                "disclosure_age_days": age,
                "lag_decay": decay,
                "maximum_absolute_effect": 5,
            },
        )
