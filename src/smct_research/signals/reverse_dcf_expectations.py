"""Conservative expectations-gap signal based on deterministic reverse DCF features."""

from __future__ import annotations

from smct_research.core.models import FeatureSnapshot, SignalDirection, SignalResult
from smct_research.core.signal import ResearchSignal


class ReverseDCFExpectationsSignal(ResearchSignal):
    id = "A2"
    name = "Reverse DCF Expectations Gap"
    required_features = (
        "dcf_base_value_per_share",
        "dcf_base_upside_percent",
        "reverse_dcf_implied_revenue_cagr",
        "reverse_dcf_implied_terminal_fcf_margin",
        "dcf_terminal_value_share",
        "dcf_model_quality_score",
        "reverse_dcf_growth_gap",
        "reverse_dcf_margin_gap",
    )

    def evaluate(self, snapshot: FeatureSnapshot) -> SignalResult:
        self.validate(snapshot)
        upside = snapshot.require_float("dcf_base_upside_percent")
        growth_gap = snapshot.require_float("reverse_dcf_growth_gap")
        margin_gap = snapshot.require_float("reverse_dcf_margin_gap")
        terminal_share = snapshot.require_float("dcf_terminal_value_share")
        quality = snapshot.require_float("dcf_model_quality_score")
        age = snapshot.values.get("dcf_input_age_days")
        dilution = snapshot.values.get("dcf_annual_dilution")
        score = 25 * upside - 35 * max(growth_gap, 0) - 45 * max(margin_gap, 0)
        score -= 30 * max(terminal_share - 0.75, 0)
        if isinstance(dilution, (int, float)):
            score -= 20 * max(float(dilution) - 0.02, 0)
        if isinstance(age, (int, float)) and age > 30:
            score -= min(10, (float(age) - 30) / 10)
        score = max(-25.0, min(25.0, score * max(0, min(1, quality))))
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
            confidence=max(0, min(1, quality)),
            direction=direction,
            thesis="Scenario valuation suggests defensible upside; it is not a price target."
            if score > 2
            else "Market-implied DCF assumptions appear demanding or evidence quality is limited.",
            evidence=[
                f"Base scenario upside/downside: {upside:.0%}.",
                f"Implied growth gap: {growth_gap:.0%}; margin gap: {margin_gap:.0%}.",
                f"Terminal value represents {terminal_share:.0%} of enterprise value.",
                f"Annual dilution assumption: {float(dilution):.1%}."
                if isinstance(dilution, (int, float))
                else "Annual dilution assumption unavailable.",
            ],
            risks=[
                "DCF outputs are scenario estimates and materially depend on terminal assumptions."
            ],
            metadata={
                "terminal_value_share": terminal_share,
                "quality": quality,
                "input_age_days": age,
                "annual_dilution": dilution,
            },
        )
