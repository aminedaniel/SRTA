from __future__ import annotations

from smct_research.core.models import FeatureSnapshot, SignalDirection, SignalResult
from smct_research.core.signal import ResearchSignal
from smct_research.macro.regime import company_macro_sensitivity


class FederalReserveRegimeSignal(ResearchSignal):
    """Research context only; its score is explicitly capped at +/-15."""

    id, name = "M1", "Federal Reserve and liquidity regime"
    required_features = ("liquidity_regime", "monetary_policy_regime", "regime_confidence")
    max_absolute_effect = 15.0

    def evaluate(self, snapshot: FeatureSnapshot) -> SignalResult:
        liquidity = str(snapshot.values["liquidity_regime"])
        policy = str(snapshot.values["monetary_policy_regime"])
        confidence = min(1.0, max(0.0, snapshot.require_float("regime_confidence")))
        sensitivity = company_macro_sensitivity(snapshot.values)
        favorable = (liquidity == "expansion") + (policy == "easing")
        adverse = (liquidity == "contraction") + (policy in {"tightening", "restrictive_stable"})
        score = min(
            self.max_absolute_effect,
            max(-self.max_absolute_effect, (favorable - adverse) * sensitivity / 10),
        )
        direction = (
            SignalDirection.POSITIVE
            if score > 1
            else SignalDirection.NEGATIVE
            if score < -1
            else SignalDirection.NEUTRAL
        )
        thesis = (
            f"Macro context is {liquidity}/{policy}; "
            "bounded modifier reflects company rate sensitivity."
        )
        return SignalResult(
            signal_id=self.id,
            ticker=snapshot.ticker,
            score=score,
            confidence=confidence,
            direction=direction,
            thesis=thesis,
            evidence=[
                f"Liquidity regime: {liquidity}",
                f"Policy regime: {policy}",
                f"Sensitivity score: {sensitivity:.1f}/100",
            ],
            risks=["Macro classifications are research context, not trade instructions."],
            metadata={"max_absolute_effect": self.max_absolute_effect},
        )
