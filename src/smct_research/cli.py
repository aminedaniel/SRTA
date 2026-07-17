from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import typer

from smct_research.core.models import FeatureSnapshot
from smct_research.core.signal import SignalRegistry
from smct_research.scoring.composite import CompositeResearchScorer
from smct_research.screening.io import load_feature_snapshots, load_universe, write_csv, write_json
from smct_research.screening.service import BatchEvaluationService
from smct_research.screening.universe import UniversePolicy
from smct_research.signals.congressional_purchases import CongressionalPurchaseSignal
from smct_research.signals.fed_regime import FederalReserveRegimeSignal
from smct_research.signals.form4_cluster_buying import Form4ClusterBuyingSignal
from smct_research.signals.reddit_awareness import RedditAwarenessSignal
from smct_research.signals.renaissance_public_equity import RenaissancePublicEquityActivitySignal
from smct_research.signals.reverse_dcf_expectations import ReverseDCFExpectationsSignal
from smct_research.signals.valuation_compression import ValuationCompressionSignal
from smct_research.valuation.reverse_dcf import (
    DCFScenario,
    ReverseDCFInputs,
    sensitivity,
    solve_reverse_dcf,
    value_dcf,
)

app = typer.Typer(no_args_is_help=True)


def default_registry() -> SignalRegistry:
    registry = SignalRegistry()
    registry.register(ValuationCompressionSignal())
    registry.register(ReverseDCFExpectationsSignal())
    registry.register(RedditAwarenessSignal())
    registry.register(CongressionalPurchaseSignal())
    registry.register(Form4ClusterBuyingSignal())
    registry.register(FederalReserveRegimeSignal())
    registry.register(RenaissancePublicEquityActivitySignal())
    return registry


def _load_universe_policy_data(path: Path) -> dict[str, object]:
    """Read JSON or the small YAML mapping format used by the bundled config."""
    text = path.read_text()
    if path.suffix.lower() == ".json":
        return json.loads(text).get("universe", {})
    values: dict[str, object] = {}
    in_universe = False
    for raw_line in text.splitlines():
        if raw_line and not raw_line.startswith((" ", "\t")):
            in_universe = raw_line.rstrip() == "universe:"
            continue
        if not in_universe or ":" not in raw_line:
            continue
        key, raw_value = raw_line.strip().split(":", 1)
        value = raw_value.strip()
        if value.startswith("[") and value.endswith("]"):
            values[key] = [item.strip() for item in value[1:-1].split(",") if item.strip()]
        else:
            try:
                values[key] = int(value)
            except ValueError:
                try:
                    values[key] = float(value)
                except ValueError:
                    values[key] = value
    return values


@app.command()
def evaluate(path: Path) -> None:
    """Evaluate a JSON feature snapshot and print a research-priority score."""
    snapshot = FeatureSnapshot.model_validate_json(path.read_text())
    results = default_registry().evaluate_all(snapshot)
    composite = CompositeResearchScorer().score(results)
    typer.echo(json.dumps(composite.model_dump(), indent=2))


@app.command()
def screen(
    universe_file: Path,
    features_directory: Path,
    as_of: str | None = typer.Option(None),  # noqa: B008
    top: int | None = typer.Option(None, min=1),  # noqa: B008,
    min_score: float | None = typer.Option(None),
    output_json: Path | None = typer.Option(None),  # noqa: B008
    output_csv: Path | None = typer.Option(None),  # noqa: B008
    include_ineligible: bool = typer.Option(False),
    config: Path | None = typer.Option(None),  # noqa: B008  # noqa: B008
) -> None:
    """Rank an offline universe using one JSON feature snapshot per ticker."""
    policy_data: dict[str, object] = {}
    if config:
        try:
            policy_data = _load_universe_policy_data(config)
        except (OSError, ValueError, json.JSONDecodeError) as error:
            raise typer.BadParameter("--config must contain a universe mapping") from error
    try:
        evaluation_as_of = datetime.fromisoformat(as_of.replace("Z", "+00:00")) if as_of else None
        if evaluation_as_of and evaluation_as_of.tzinfo is None:
            evaluation_as_of = evaluation_as_of.replace(tzinfo=UTC)
    except ValueError as error:
        raise typer.BadParameter("--as-of must be an ISO-8601 timestamp") from error
    policy = UniversePolicy.model_validate(policy_data)
    companies = load_universe(universe_file)
    snapshots = load_feature_snapshots(features_directory)
    results = BatchEvaluationService(default_registry(), CompositeResearchScorer()).evaluate(
        companies, snapshots, policy, evaluation_as_of, include_ineligible
    )
    if min_score is not None:
        results = [
            item
            for item in results
            if item.composite_score is not None and item.composite_score >= min_score
        ]
    if top is not None:
        results = results[:top]
    if output_json:
        write_json(output_json, results)
    if output_csv:
        write_csv(output_csv, results)
    if not results:
        typer.echo("No companies matched the screen.")
        return
    for item in results:
        score = "unavailable" if item.composite_score is None else f"{item.composite_score:.1f}"
        rank = "-" if item.rank is None else str(item.rank)
        typer.echo(f"{rank:>3}  {item.ticker:<8} {score:>11}  {item.company_name}")


if __name__ == "__main__":
    app()


@app.command()
def dcf(
    input_file: Path,
    as_of: str | None = typer.Option(None),
    config: Path | None = typer.Option(None),  # noqa: B008
    output_json: Path | None = typer.Option(None),  # noqa: B008
    sensitivity_output: bool = typer.Option(False, "--sensitivity"),
    scenario: str = typer.Option("all"),
) -> None:
    """Run an offline deterministic scenario and reverse-DCF valuation."""
    del config  # Input is self-contained; configuration may be merged by callers.
    payload = json.loads(input_file.read_text())
    inputs = ReverseDCFInputs.model_validate(payload.get("inputs", payload))
    if as_of:
        inputs.valuation_date = datetime.fromisoformat(as_of.replace("Z", "+00:00"))
    scenarios = [DCFScenario.model_validate(item) for item in payload.get("scenarios", [])]
    if not scenarios:
        raise typer.BadParameter("input must include scenarios")
    selected = (
        scenarios if scenario == "all" else [item for item in scenarios if item.name == scenario]
    )
    if not selected:
        raise typer.BadParameter("--scenario must match an input scenario")
    output = []
    for item in selected:
        valuation = value_dcf(inputs, item)
        record: dict[str, object] = {
            "valuation": valuation.model_dump(mode="json"),
            "reverse": solve_reverse_dcf(inputs, item).model_dump(mode="json"),
        }
        if sensitivity_output:
            record["sensitivity"] = [
                x.model_dump(mode="json")
                for x in sensitivity(
                    inputs,
                    item,
                    [item.discount_rate - 0.01, item.discount_rate, item.discount_rate + 0.01],
                    [
                        item.terminal_growth_rate - 0.005,
                        item.terminal_growth_rate,
                        item.terminal_growth_rate + 0.005,
                    ],
                    [
                        item.terminal_fcf_margin - 0.05,
                        item.terminal_fcf_margin,
                        item.terminal_fcf_margin + 0.05,
                    ],
                    [
                        item.initial_revenue_growth - 0.05,
                        item.initial_revenue_growth,
                        item.initial_revenue_growth + 0.05,
                    ],
                )
            ]
        output.append(record)
    rendered = json.dumps(output, indent=2)
    if output_json:
        output_json.write_text(rendered + "\n")
    typer.echo(rendered)
