"""A transparent quality check on normalized, period-aligned operating evidence."""

from smct_research.core.models import FeatureSnapshot, SignalDirection, SignalResult
from smct_research.core.signal import ResearchSignal


class FinancialQualitySignal(ResearchSignal):
    id = "Q1"
    name = "Financial quality and dilution"
    required_features = ("yoy_revenue_growth", "operating_margin")

    def evaluate(self, snapshot: FeatureSnapshot) -> SignalResult:
        growth = snapshot.require_float("yoy_revenue_growth")
        margin = snapshot.require_float("operating_margin")
        components = [max(-1.0, min(1.0, growth / 0.25)), max(-1.0, min(1.0, margin / 0.20))]
        evidence = [
            f"Comparable-period revenue growth: {growth:.1%}.",
            f"Operating margin: {margin:.1%}.",
        ]
        risks = [
            "Accounting periods may differ across companies; inspect the financial evidence periods."
        ]
        for key, label, neutral, scale, sign in (
            ("free_cash_flow_margin", "Free cash flow margin", 0.0, 0.20, 1),
            ("yoy_diluted_share_growth", "Diluted share growth", 0.02, 0.10, -1),
            (
                "stock_based_compensation_pct_revenue",
                "Stock-based compensation / revenue",
                0.10,
                0.20,
                -1,
            ),
        ):
            if snapshot.values.get(key) is not None:
                value = snapshot.require_float(key)
                components.append(sign * max(-1.0, min(1.0, (value - neutral) / scale)))
                evidence.append(f"{label}: {value:.1%}.")
            else:
                risks.append(f"{label} unavailable; no assumption substituted.")
        score = 50 * sum(components) / len(components)
        return SignalResult(
            signal_id=self.id,
            ticker=snapshot.ticker,
            score=score,
            confidence=0.4 + 0.1 * len(components),
            direction=SignalDirection.POSITIVE
            if score > 10
            else SignalDirection.NEGATIVE
            if score < -10
            else SignalDirection.NEUTRAL,
            thesis="Operating evidence shows profitable growth and shareholder discipline."
            if score > 10
            else "Operating performance or shareholder dilution warrants further review.",
            evidence=evidence,
            risks=risks,
            metadata={"observed_components": len(components), "possible_components": 5},
        )
