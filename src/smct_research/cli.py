from __future__ import annotations

import csv
import json
import re
from datetime import UTC, datetime
from importlib.resources import files
from pathlib import Path

import typer

from smct_research.core.models import FeatureSnapshot
from smct_research.core.signal import SignalRegistry
from smct_research.estimates.models import EstimateMetric
from smct_research.estimates.service import calculate_features
from smct_research.providers.base import ProviderResponseError
from smct_research.providers.estimates import OfflineEstimateProvider
from smct_research.scoring.composite import CompositeResearchScorer
from smct_research.screening.io import load_feature_snapshots, load_universe, write_csv, write_json
from smct_research.screening.service import BatchEvaluationService
from smct_research.screening.universe import UniversePolicy
from smct_research.signals.congressional_purchases import CongressionalPurchaseSignal
from smct_research.signals.estimate_revision_velocity import ConsensusEstimateRevisionSignal
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
    registry.register(ConsensusEstimateRevisionSignal())
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


def _load_reverse_dcf_config(path: Path) -> dict[str, dict[str, object]]:
    """Load JSON or the bundled small YAML-compatible scenario mapping."""
    text = path.read_text()
    if path.suffix.lower() == ".json":
        raw: object = json.loads(text)
    else:
        yaml_values: dict[str, object] = {}
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith("#") or ":" not in line:
                continue
            name, value = line.split(":", 1)
            if value.strip().startswith("{"):
                normalized = re.sub(r"([A-Za-z_][A-Za-z0-9_]*):", r'"\1":', value.strip())
                yaml_values[name] = json.loads(normalized)
        raw = yaml_values
    if isinstance(raw, dict) and "scenarios" in raw:
        raw = raw["scenarios"]
    if not isinstance(raw, dict):
        raise ValueError("reverse DCF config must be a scenario mapping")
    return {str(name): dict(value) for name, value in raw.items() if isinstance(value, dict)}


def _merged_scenarios(payload: dict[str, object], config: Path | None) -> list[DCFScenario]:
    if config is not None:
        config_data = _load_reverse_dcf_config(config)
    else:
        packaged = files("smct_research.config").joinpath("reverse_dcf.yaml")
        # importlib.resources resolves installed package data deterministically.
        config_data = _load_reverse_dcf_config(Path(str(packaged)))
    explicit = payload.get("scenarios", [])
    if isinstance(explicit, dict):
        explicit = [
            {"name": name, **value} for name, value in explicit.items() if isinstance(value, dict)
        ]
    merged = {name: {"name": name, **values} for name, values in config_data.items()}
    for value in explicit if isinstance(explicit, list) else []:
        if not isinstance(value, dict):
            raise ValueError("scenarios must be objects")
        name = str(value.get("name", "base"))
        merged[name] = {**merged.get(name, {"name": name}), **value}
    return [DCFScenario.model_validate(merged[name]) for name in sorted(merged)]


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
    payload = json.loads(input_file.read_text())
    input_data = payload.get("inputs", payload)
    if not isinstance(input_data, dict):
        raise typer.BadParameter("inputs must be an object")
    if as_of:
        try:
            overridden = datetime.fromisoformat(as_of.replace("Z", "+00:00"))
        except ValueError as error:
            raise typer.BadParameter("--as-of must be an ISO-8601 timestamp") from error
        input_data = {**input_data, "valuation_date": overridden}
    try:
        inputs = ReverseDCFInputs.model_validate(input_data)
        scenarios = _merged_scenarios(payload, config)
    except (OSError, ValueError, json.JSONDecodeError) as error:
        raise typer.BadParameter(str(error)) from error
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
            try:
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
            except ValueError as error:
                raise typer.BadParameter(str(error)) from error
        output.append(record)
    rendered = json.dumps(output, indent=2)
    if output_json:
        output_json.write_text(rendered + "\n")
    typer.echo(rendered)


@app.command()
def revisions(
    estimate_history_file: Path,
    ticker: str = typer.Option(...),
    as_of: str = typer.Option(...),
    metric: EstimateMetric = typer.Option(EstimateMetric.EPS),  # noqa: B008
    period_end: str | None = typer.Option(None),
    output_json: Path | None = typer.Option(None),  # noqa: B008
    output_csv: Path | None = typer.Option(None),  # noqa: B008
    lookback_tolerance_days: int = typer.Option(7, min=0),
) -> None:
    """Calculate deterministic point-in-time consensus estimate revisions from a local file."""
    try:
        timestamp = datetime.fromisoformat(as_of.replace("Z", "+00:00"))
        selected_period = datetime.fromisoformat(period_end).date() if period_end else None
        records = OfflineEstimateProvider(estimate_history_file).fetch_estimate_history(ticker)
        result = calculate_features(
            records, ticker, metric, timestamp, selected_period, lookback_tolerance_days
        )
    except (ProviderResponseError, ValueError, OSError, TypeError) as error:
        raise typer.BadParameter(str(error)) from error
    payload = result.model_dump(mode="json")
    rendered = json.dumps(payload, indent=2)
    if output_json:
        output_json.write_text(rendered + "\n")
    if output_csv:
        row = {
            "ticker": result.ticker,
            "metric": result.metric.value,
            "current": result.current.consensus,
            "quality_score": result.quality.score,
            **{f"revision_{days}d": item.value for days, item in result.revisions.items()},
        }
        with output_csv.open("w", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(row))
            writer.writeheader()
            writer.writerow(row)
    typer.echo(rendered)


if __name__ == "__main__":
    app()
