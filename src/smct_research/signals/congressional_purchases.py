from __future__ import annotations

import math

from smct_research.core.models import FeatureSnapshot, SignalDirection, SignalResult
from smct_research.core.signal import ResearchSignal


class CongressionalPurchaseSignal(ResearchSignal):
    """Scores disclosed congressional purchasing as an alignment signal.

    The signal is intentionally conservative. It discounts old transactions and
    delayed disclosures, uses buyer breadth rather than a single filer, and
    treats committee relevance as supporting context rather than proof of an edge.
    """

    id = "E2"
    name = "Congressional Disclosed Purchasing"
    required_features = (
        "congress_purchase_count_90d",
        "congress_sale_count_90d",
        "congress_unique_buyers_90d",
        "congress_estimated_purchase_usd_90d",
        "congress_latest_purchase_age_days",
        "congress_median_disclosure_lag_days",
        "congress_committee_relevance_score",
        "congress_repeat_buyer_score",
    )

    def evaluate(self, snapshot: FeatureSnapshot) -> SignalResult:
        self.validate(snapshot)
        purchases = max(0.0, snapshot.require_float("congress_purchase_count_90d"))
        sales = max(0.0, snapshot.require_float("congress_sale_count_90d"))
        unique_buyers = max(0.0, snapshot.require_float("congress_unique_buyers_90d"))
        estimated_purchase_usd = max(
            0.0, snapshot.require_float("congress_estimated_purchase_usd_90d")
        )
        latest_age_days = max(
            0.0, snapshot.require_float("congress_latest_purchase_age_days")
        )
        disclosure_lag_days = max(
            0.0, snapshot.require_float("congress_median_disclosure_lag_days")
        )
        committee_relevance = self._bounded_percent(
            snapshot.require_float("congress_committee_relevance_score")
        )
        repeat_buyer = self._bounded_percent(
            snapshot.require_float("congress_repeat_buyer_score")
        )

        total_transactions = purchases + sales
        net_flow = (
            (purchases - sales) / total_transactions if total_transactions > 0 else 0.0
        )
        buyer_breadth = min(unique_buyers / 4.0, 1.0)
        purchase_size = min(math.log10(1.0 + estimated_purchase_usd) / 6.0, 1.0)

        # The market cannot react before disclosure. Both stale transactions and
        # slow disclosure reduce the usefulness of the observation.
        timeliness = math.exp(-latest_age_days / 120.0) * math.exp(
            -disclosure_lag_days / 90.0
        )

        positive_alignment = (
            0.40 * buyer_breadth
            + 0.20 * purchase_size
            + 0.20 * committee_relevance
            + 0.20 * repeat_buyer
        )
        raw_score = (80.0 * positive_alignment + 30.0 * net_flow) * timeliness
        score = max(-100.0, min(100.0, raw_score))

        observation_depth = min(1.0, unique_buyers / 4.0 + total_transactions / 20.0)
        confidence = (0.30 + 0.60 * observation_depth) * math.exp(
            -disclosure_lag_days / 180.0
        )
        confidence = max(0.15, min(0.90, confidence))

        if score >= 25:
            direction = SignalDirection.POSITIVE
            thesis = (
                "Recent disclosed purchases by multiple congressional households "
                "provide a supporting alignment signal."
            )
        elif score <= -20:
            direction = SignalDirection.NEGATIVE
            thesis = (
                "Recent disclosed congressional activity is dominated by sales rather "
                "than purchases."
            )
        else:
            direction = SignalDirection.NEUTRAL
            thesis = (
                "Congressional transaction disclosures do not provide a strong current "
                "signal."
            )

        return SignalResult(
            signal_id=self.id,
            ticker=snapshot.ticker,
            score=score,
            confidence=confidence,
            direction=direction,
            thesis=thesis,
            evidence=[
                f"Purchases/sales disclosed in 90 days: {purchases:.0f}/{sales:.0f}.",
                f"Unique purchasing households: {unique_buyers:.0f}.",
                f"Estimated disclosed purchase value: ${estimated_purchase_usd:,.0f}.",
                (
                    "Median disclosure lag: "
                    f"{disclosure_lag_days:.0f} days; latest purchase age: "
                    f"{latest_age_days:.0f} days."
                ),
                (
                    "Committee relevance: "
                    f"{committee_relevance * 100:.0f}/100; repeat-buyer score: "
                    f"{repeat_buyer * 100:.0f}/100."
                ),
            ],
            risks=[
                "Disclosures can arrive well after the transaction date.",
                "Reported transaction values are ranges rather than exact amounts.",
                "A disclosed purchase does not establish possession or use of inside information.",
                "Committee relevance is contextual and must not be treated as causal evidence.",
            ],
            metadata={
                "net_flow": net_flow,
                "buyer_breadth": buyer_breadth,
                "purchase_size": purchase_size,
                "timeliness": timeliness,
                "committee_relevance": committee_relevance,
                "repeat_buyer": repeat_buyer,
            },
        )

    @staticmethod
    def _bounded_percent(value: float) -> float:
        return max(0.0, min(100.0, value)) / 100.0
