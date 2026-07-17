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
        by_name = {scenario.name: value_dcf(inputs, scenario) for scenario in scenarios}
        for name, result in by_name.items():
            if result.valid:
                values[f"dcf_{name}_value_per_share"] = result.diluted_value_per_share
        base = by_name.get("base")
        base_scenario = next((scenario for scenario in scenarios if scenario.name == "base"), None)
        if base and base.valid and base_scenario:
            reverse = solve_reverse_dcf(inputs, base_scenario)
            values.update(
                {
                    "dcf_base_upside_percent": base.upside_downside_percent,
                    "dcf_terminal_value_share": base.terminal_value_share,
                    "reverse_dcf_implied_revenue_cagr": reverse.implied_revenue_cagr,
                    "reverse_dcf_implied_terminal_fcf_margin": reverse.implied_terminal_fcf_margin,
                    "reverse_dcf_growth_gap": reverse.growth_gap,
                    "reverse_dcf_margin_gap": reverse.margin_gap,
                    "dcf_model_quality_score": max(
                        0.0,
                        1.0
                        - max(0.0, base.terminal_value_share - 0.65)
                        - 0.25 * len(reverse.diagnostics),
                    ),
                    "dcf_input_age_days": max(
                        (inputs.valuation_date - date).days for date in inputs.available_at.values()
                    )
                    if inputs.available_at
                    else 0,
                }
            )
            conservative = by_name.get("conservative")
            if conservative and conservative.valid:
                values["dcf_conservative_downside_percent"] = conservative.upside_downside_percent
        return evidence.model_copy(update={"values": values})
