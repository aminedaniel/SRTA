from __future__ import annotations

from smct_research.core.models import FeatureSnapshot, SignalDirection, SignalResult
from smct_research.core.signal import ResearchSignal


class ValuationCompressionSignal(ResearchSignal):
    id = "A1"
    name = "Valuation Compression vs Fundamentals"
    required_features = (
        "ev_sales_current",
        "ev_sales_3y_median",
        "revenue_growth_current",
        "revenue_growth_3y_median",
    )

    def evaluate(self, snapshot: FeatureSnapshot) -> SignalResult:
        self.validate(snapshot)
        current_multiple = snapshot.require_float("ev_sales_current")
        median_multiple = snapshot.require_float("ev_sales_3y_median")
        current_growth = snapshot.require_float("revenue_growth_current")
        median_growth = snapshot.require_float("revenue_growth_3y_median")

        multiple_compression = 1 - (current_multiple / median_multiple)
        growth_deceleration = max(0.0, median_growth - current_growth)
        disconnect = multiple_compression - growth_deceleration
        score = max(-100.0, min(100.0, disconnect * 140))
        confidence = min(0.95, 0.45 + abs(disconnect))

        evidence = [
            f"EV/Sales is {multiple_compression:.0%} below its three-year median.",
            f"Revenue growth is {growth_deceleration:.0%} below its three-year median.",
        ]
        thesis = (
            "Valuation compression appears greater than the deterioration in revenue growth."
            if score > 20
            else "Valuation and growth deterioration are not meaningfully disconnected."
        )
        direction = SignalDirection.POSITIVE if score > 20 else SignalDirection.NEUTRAL

        return SignalResult(
            signal_id=self.id,
            ticker=snapshot.ticker,
            score=score,
            confidence=confidence,
            direction=direction,
            thesis=thesis,
            evidence=evidence,
            risks=["Historical multiples may have been structurally excessive."],
            metadata={
                "multiple_compression": multiple_compression,
                "growth_deceleration": growth_deceleration,
                "disconnect": disconnect,
            },
        )
