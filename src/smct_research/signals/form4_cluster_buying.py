"""Low-weight corroborating signal derived from public SEC Form 4 purchases."""

from __future__ import annotations

import math

from smct_research.core.models import FeatureSnapshot, SignalDirection, SignalResult
from smct_research.core.signal import ResearchSignal


class Form4ClusterBuyingSignal(ResearchSignal):
    """Score timely, material clusters of public insider open-market purchases.

    This is explicitly corroborating evidence and must not be used as a
    standalone trade instruction.
    """

    id = "E3"
    name = "Form 4 Cluster Buying"
    required_features = (
        "form4_unique_insiders_buying_7d",
        "form4_aggregate_purchase_value_7d",
        "form4_purchase_value_market_cap_ratio_7d",
        "form4_largest_individual_purchase_7d",
        "form4_officer_director_10pct_participants_7d",
        "form4_repeated_purchase_insiders_7d",
        "form4_filing_age_days",
        "form4_cluster_buying_7d",
    )

    def evaluate(self, snapshot: FeatureSnapshot) -> SignalResult:
        self.validate(snapshot)
        buyers = max(0.0, snapshot.require_float("form4_unique_insiders_buying_7d"))
        value = max(0.0, snapshot.require_float("form4_aggregate_purchase_value_7d"))
        cap_ratio = max(0.0, snapshot.require_float("form4_purchase_value_market_cap_ratio_7d"))
        largest = max(0.0, snapshot.require_float("form4_largest_individual_purchase_7d"))
        leadership = max(
            0.0, snapshot.require_float("form4_officer_director_10pct_participants_7d")
        )
        repeat = max(0.0, snapshot.require_float("form4_repeated_purchase_insiders_7d"))
        age = max(0.0, snapshot.require_float("form4_filing_age_days"))
        cluster = bool(snapshot.values["form4_cluster_buying_7d"]) and buyers >= 3
        material = cap_ratio >= 0.0001 and value >= 25_000
        if not cluster or not material:
            return self._result(
                snapshot,
                0.0,
                0.15,
                "No qualifying, material Form 4 buying cluster is currently public.",
                buyers,
                value,
                cap_ratio,
                age,
            )
        breadth = min(1.0, (buyers - 2) / 4)
        materiality = min(1.0, math.log10(1 + cap_ratio / 0.0001) / math.log10(101))
        leadership_strength = min(1.0, leadership / 3)
        individual_size = min(1.0, math.log10(1 + largest / 25_000) / math.log10(101))
        repeat_strength = min(1.0, repeat / 2)
        decay = math.exp(-age / 30)
        score = min(
            12.0,
            12.0
            * (
                0.30 * breadth
                + 0.35 * materiality
                + 0.20 * leadership_strength
                + 0.10 * individual_size
                + 0.05 * repeat_strength
            )
            * decay,
        )
        confidence = min(0.85, (0.35 + 0.30 * breadth + 0.25 * leadership_strength) * decay)
        return self._result(
            snapshot,
            score,
            confidence,
            "A recent public cluster of open-market insider purchases corroborates, "
            "but does not independently establish, the research thesis.",
            buyers,
            value,
            cap_ratio,
            age,
        )

    def _result(
        self,
        snapshot: FeatureSnapshot,
        score: float,
        confidence: float,
        thesis: str,
        buyers: float,
        value: float,
        cap_ratio: float,
        age: float,
    ) -> SignalResult:
        return SignalResult(
            signal_id=self.id,
            ticker=snapshot.ticker,
            score=max(0.0, min(12.0, score)),
            confidence=max(0.0, min(1.0, confidence)),
            direction=SignalDirection.POSITIVE if score > 0 else SignalDirection.NEUTRAL,
            thesis=thesis,
            evidence=[
                f"Unique qualifying buyers in seven days: {buyers:.0f}.",
                f"7-day triggered-cluster purchase value: ${value:,.0f} "
                f"({cap_ratio:.3%} of market capitalization).",
                f"Newest qualifying filing age: {age:.0f} days.",
            ],
            risks=[
                "Form 4 purchases are corroborating evidence, not standalone trade instructions.",
                "Insiders may buy for reasons unrelated to future returns.",
                "Only filings public by the evaluation date are included.",
            ],
            metadata={
                "unique_buyers_7d": buyers,
                "triggered_purchase_value_7d": value,
                "market_cap_ratio": cap_ratio,
                "filing_age_days": age,
            },
        )
