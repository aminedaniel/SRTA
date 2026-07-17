"""Feature assembly boundary: providers supply data; signals only consume it."""

from __future__ import annotations

from datetime import datetime

from smct_research.core.models import FeatureSnapshot, normalize_utc
from smct_research.screening.models import FeatureAssemblyInput
from smct_research.valuation.reverse_dcf import DCFScenario, ReverseDCFInputs


class FeatureSnapshotAssembler:
    def assemble(
        self, evidence: FeatureAssemblyInput, as_of: datetime | None = None
    ) -> FeatureSnapshot:
        """Construct a snapshot without network access or provider side effects."""
        requested_as_of = normalize_utc(as_of) if as_of else evidence.as_of
        availability = [evidence.as_of, *evidence.source_as_of.values()]
        latest_available = max(availability)
        if requested_as_of < latest_available:
            raise ValueError("Feature snapshot as_of cannot precede evidence availability")
        return FeatureSnapshot(
            ticker=evidence.ticker,
            as_of=requested_as_of,
            values=evidence.values,
            sources=evidence.sources,
            source_as_of=evidence.source_as_of,
        )

    def add_reverse_dcf_features(
        self,
        evidence: FeatureAssemblyInput,
        inputs: ReverseDCFInputs,
        scenarios: list[DCFScenario],
    ) -> FeatureAssemblyInput:
        """Add deterministic valuation features while preserving upstream evidence provenance."""
        from smct_research.valuation.reverse_dcf import solve_reverse_dcf, value_dcf

        values = dict(evidence.values)
        sources = dict(evidence.sources)
        source_as_of = dict(evidence.source_as_of)
        for field, timestamp in inputs.available_at.items():
            source_as_of[f"dcf:{field}"] = timestamp
            sources[f"dcf:{field}"] = inputs.provenance.get(field, "unspecified")
        by_name = {scenario.name: value_dcf(inputs, scenario) for scenario in scenarios}
        for name, result in by_name.items():
            if result.valid:
                values[f"dcf_{name}_value_per_share"] = result.diluted_value_per_share
        base = by_name.get("base")
        base_scenario = next((scenario for scenario in scenarios if scenario.name == "base"), None)
        if base and base.valid and base_scenario:
            reverse = solve_reverse_dcf(inputs, base_scenario)
            input_age_days = max(
                (inputs.valuation_date - timestamp).days
                for timestamp in inputs.available_at.values()
            )
            annual_dilution = (
                base_scenario.annual_dilution
                if base_scenario.annual_dilution is not None
                else inputs.expected_annual_dilution
            )
            age_penalty = min(0.40, max(0, input_age_days - 30) / 365)
            dilution_penalty = min(0.30, max(0.0, annual_dilution - 0.02) * 5)
            quality = max(
                0.0,
                min(
                    1.0,
                    1.0
                    - max(0.0, base.terminal_value_share - 0.65)
                    - 0.25 * len(reverse.diagnostics)
                    - age_penalty
                    - dilution_penalty,
                ),
            )
            values.update(
                {
                    "dcf_base_upside_percent": base.upside_downside_percent,
                    "dcf_terminal_value_share": base.terminal_value_share,
                    "reverse_dcf_implied_revenue_cagr": reverse.implied_revenue_cagr,
                    "reverse_dcf_implied_terminal_fcf_margin": reverse.implied_terminal_fcf_margin,
                    "reverse_dcf_growth_gap": reverse.growth_gap,
                    "reverse_dcf_margin_gap": reverse.margin_gap,
                    "dcf_model_quality_score": quality,
                    "dcf_annual_dilution": annual_dilution,
                    "dcf_input_age_days": input_age_days,
                }
            )
            conservative = by_name.get("conservative")
            if conservative and conservative.valid:
                values["dcf_conservative_downside_percent"] = conservative.upside_downside_percent
        return evidence.model_copy(
            update={"values": values, "sources": sources, "source_as_of": source_as_of}
        )

    def add_estimate_revision_features(
        self, evidence: FeatureAssemblyInput, features: object
    ) -> FeatureAssemblyInput:
        """Attach revision evidence; every selected snapshot retains provenance."""
        from smct_research.estimates.models import EstimateRevisionFeatures

        if not isinstance(features, EstimateRevisionFeatures):
            raise TypeError("features must be EstimateRevisionFeatures")
        if features.as_of > evidence.as_of:
            raise ValueError("estimate evidence cannot be newer than feature snapshot")
        prefix = features.metric.value
        values, sources, source_as_of = (
            dict(evidence.values),
            dict(evidence.sources),
            dict(evidence.source_as_of),
        )
        values.update(
            {
                f"{prefix}_consensus_current": features.current.consensus,
                f"{prefix}_revision_acceleration": features.acceleration,
                f"{prefix}_revision_streak": features.streak,
                f"{prefix}_analyst_count": features.current.analyst_count,
                f"{prefix}_analyst_count_change_30d": features.analyst_count_change_30d,
                f"{prefix}_high_change_30d": features.high_change_30d,
                f"{prefix}_low_change_30d": features.low_change_30d,
                f"{prefix}_dispersion_current": features.current.standard_deviation,
                f"{prefix}_dispersion_change_30d": features.dispersion_change_30d,
                f"{prefix}_estimate_breadth_change_30d": features.breadth_change_30d,
                f"{prefix}_consensus_age_days": features.days_since_latest_update,
                f"{prefix}_sign_transition": features.sign_transition,
                f"{prefix}_revision_quality_score": features.quality.score,
                f"{prefix}_revision_coverage_percentage": features.quality.coverage_percentage,
                f"{prefix}_target_period_end": features.current.target_period_end.isoformat(),
                f"{prefix}_target_period_rollover": "target_period_rollover"
                in features.diagnostics,
                f"{prefix}_revision_diagnostics": ";".join(features.diagnostics),
            }
        )
        for days, revision in features.revisions.items():
            values[f"{prefix}_revision_{days}d"] = revision.value
            if revision.prior:
                key = (
                    f"estimates:{prefix}:{days}d:{revision.prior.provider}:"
                    f"{revision.prior.provider_record_id}"
                )
                sources[key] = revision.prior.source_identifier
                source_as_of[key] = revision.prior.available_at
        key = (
            f"estimates:{prefix}:current:{features.current.provider}:"
            f"{features.current.provider_record_id}"
        )
        sources[key] = features.current.source_identifier
        source_as_of[key] = features.current.available_at
        # EPS is the required A3 core; generic aliases are therefore written only once.
        if prefix == "eps":
            values["estimate_revision_quality_score"] = features.quality.score
        return evidence.model_copy(
            update={"values": values, "sources": sources, "source_as_of": source_as_of}
        )
