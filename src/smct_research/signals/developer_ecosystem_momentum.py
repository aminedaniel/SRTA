from __future__ import annotations

from smct_research.core.models import FeatureSnapshot, SignalDirection, SignalResult
from smct_research.core.signal import ResearchSignal


class DeveloperEcosystemMomentumSignal(ResearchSignal):
    id = "B1"
    name = "Developer Ecosystem Momentum"
    required_features = ("developer_momentum_quality_score", "developer_data_quality_score")

    def evaluate(self, snapshot: FeatureSnapshot) -> SignalResult:
        self.validate(snapshot)
        raw = snapshot.require_float("developer_momentum_quality_score")
        confidence = snapshot.require_float("developer_data_quality_score")
        contributor_growth = snapshot.values.get("developer_active_contributor_growth_90d")
        external_growth = snapshot.values.get("developer_external_contributor_growth_90d")
        package_growth = snapshot.values.get("developer_package_download_growth_90d")
        backlog_growth = snapshot.values.get("developer_issue_backlog_growth_90d")
        concentration = snapshot.values.get("developer_contributor_concentration")
        bot_share = snapshot.values.get("developer_bot_activity_share")
        breadth = snapshot.values.get("developer_ecosystem_breadth")
        score = max(-25.0, min(25.0, raw / 4))
        direction = (
            SignalDirection.POSITIVE
            if score > 2
            else SignalDirection.NEGATIVE
            if score < -2
            else SignalDirection.NEUTRAL
        )
        thesis = {
            SignalDirection.POSITIVE: "Developer ecosystem evidence is improving across durable adoption or contribution measures.",
            SignalDirection.NEGATIVE: "Developer ecosystem evidence is deteriorating, concentrated, stale, or weakly corroborated.",
            SignalDirection.NEUTRAL: "Developer ecosystem evidence is mixed or insufficiently decisive.",
        }[direction]
        evidence: list[str] = []
        if isinstance(contributor_growth, (float, int)):
            evidence.append(f"Active contributors changed {contributor_growth:.1%} over 90 days.")
        if isinstance(external_growth, (float, int)):
            evidence.append(f"External contributors changed {external_growth:.1%} over 90 days.")
        if isinstance(package_growth, (float, int)):
            evidence.append(f"Package downloads changed {package_growth:.1%} over 90 days.")
        if isinstance(backlog_growth, (float, int)):
            evidence.append(f"Issue backlog changed {backlog_growth:.1%} over 90 days.")
        risks = ["Developer evidence is research evidence, not a trade instruction."]
        if isinstance(concentration, (float, int)) and concentration > 0.4:
            risks.append(
                f"Top contributor share is {concentration:.0%}, indicating key-person dependency risk."
            )
        if isinstance(bot_share, (float, int)) and bot_share > 0.25:
            risks.append(
                f"Bot activity share is {bot_share:.0%}, reducing confidence in raw activity."
            )
        return SignalResult(
            signal_id=self.id,
            ticker=snapshot.ticker,
            score=score,
            confidence=max(0, min(1, confidence)),
            direction=direction,
            thesis=thesis,
            evidence=evidence,
            risks=risks,
            metadata={
                "raw_quality_score": raw,
                "ecosystem_breadth": breadth,
                "horizon_months": "6-24",
                "confidence_applied_by_composite": True,
            },
        )
