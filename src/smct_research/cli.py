from __future__ import annotations

import csv
import json
import re
from datetime import UTC, datetime
from importlib.resources import files
from pathlib import Path

import typer

from smct_research.core.models import FeatureSnapshot, ThesisStatus, normalize_utc
from smct_research.core.signal import SignalRegistry
from smct_research.developer_ecosystem import (
    OfflineDeveloperHistoryProvider,
    OfflinePackageHistoryProvider,
    calculate_developer_ecosystem_features,
    load_repository_mappings,
)
from smct_research.developer_ecosystem.config import load_developer_ecosystem_config
from smct_research.estimates.models import EstimateBasis, EstimateMetric, EstimatePeriod
from smct_research.estimates.service import calculate_features
from smct_research.providers.base import ProviderResponseError
from smct_research.providers.estimates import OfflineEstimateProvider
from smct_research.research.render import render_markdown
from smct_research.research.report import ResearchReportBuilder
from smct_research.research.thesis import create_initial_thesis_record, transition_thesis
from smct_research.scoring.composite import CompositeResearchScorer
from smct_research.screening.io import load_feature_snapshots, load_universe, write_csv, write_json
from smct_research.screening.service import BatchEvaluationService
from smct_research.screening.universe import UniversePolicy
from smct_research.signals.congressional_purchases import CongressionalPurchaseSignal
from smct_research.signals.developer_ecosystem_momentum import DeveloperEcosystemMomentumSignal
from smct_research.signals.estimate_revision_velocity import ConsensusEstimateRevisionSignal
from smct_research.signals.fed_regime import FederalReserveRegimeSignal
from smct_research.signals.form4_cluster_buying import Form4ClusterBuyingSignal
from smct_research.signals.reddit_awareness import RedditAwarenessSignal
from smct_research.signals.renaissance_public_equity import RenaissancePublicEquityActivitySignal
from smct_research.signals.reverse_dcf_expectations import ReverseDCFExpectationsSignal
from smct_research.signals.valuation_compression import ValuationCompressionSignal
from smct_research.storage.duckdb_store import LocalAnalyticalStore
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
    registry.register(DeveloperEcosystemMomentumSignal())
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
    provider: str | None = typer.Option(None),
    basis: EstimateBasis | None = typer.Option(None),  # noqa: B008
    period_type: EstimatePeriod | None = typer.Option(None),  # noqa: B008
    unit: str | None = typer.Option(None),
    currency: str | None = typer.Option(None),
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
            records,
            ticker,
            metric,
            timestamp,
            selected_period,
            lookback_tolerance_days,
            provider=provider,
            basis=basis,
            period_type=period_type,
            unit=unit,
            currency=currency,
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


@app.command("developer-velocity")
def developer_velocity(
    developer_history_file: Path,
    ticker: str = typer.Option(...),
    as_of: str = typer.Option(...),
    mapping_file: Path | None = typer.Option(None),  # noqa: B008
    package_history_file: Path | None = typer.Option(None),  # noqa: B008
    output_json: Path | None = typer.Option(None),  # noqa: B008
    output_csv: Path | None = typer.Option(None),  # noqa: B008
    include_repository: list[str] | None = typer.Option(None),  # noqa: B008
    exclude_repository: list[str] | None = typer.Option(None),  # noqa: B008
    config: Path | None = typer.Option(None),  # noqa: B008
) -> None:
    """Calculate deterministic offline developer ecosystem momentum features."""
    try:
        timestamp = datetime.fromisoformat(as_of.replace("Z", "+00:00"))
        if mapping_file is None:
            raise typer.BadParameter("--mapping-file is required")
        repos = OfflineDeveloperHistoryProvider(developer_history_file).fetch_repository_history(
            ticker
        )
        if include_repository:
            wanted = {item.lower() for item in include_repository}
            repos = [
                r
                for r in repos
                if f"{r.owner}/{r.name}".lower() in wanted or r.repository_id.lower() in wanted
            ]
        if exclude_repository:
            blocked = {item.lower() for item in exclude_repository}
            repos = [
                r
                for r in repos
                if f"{r.owner}/{r.name}".lower() not in blocked
                and r.repository_id.lower() not in blocked
            ]
        packages = (
            OfflinePackageHistoryProvider(package_history_file).fetch_package_history(ticker)
            if package_history_file
            else []
        )
        mappings = load_repository_mappings(mapping_file, ticker)
        dev_config = load_developer_ecosystem_config(config)
        result = calculate_developer_ecosystem_features(
            repos, mappings, ticker, timestamp, packages, config=dev_config
        )
    except (ProviderResponseError, ValueError, OSError, TypeError) as error:
        raise typer.BadParameter(str(error)) from error
    payload = result.model_dump(mode="json")
    rendered = json.dumps(payload, indent=2)
    if output_json:
        output_json.write_text(rendered + "\n")
    if output_csv:
        row = {"ticker": result.ticker, "quality_score": result.quality.score, **result.features}
        with output_csv.open("w", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(row))
            writer.writeheader()
            writer.writerow(row)
    typer.echo(rendered)


def _parse_timestamp(value: str, label: str = "timestamp") -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise typer.BadParameter(f"{label} must be an ISO-8601 timestamp") from error
    return normalize_utc(parsed)


@app.command("report")
def report_command(
    universe_file: Path,
    features_directory: Path,
    ticker: str = typer.Option(...),
    as_of: str = typer.Option(...),
    output_json: Path | None = typer.Option(None),  # noqa: B008
    output_markdown: Path | None = typer.Option(None),  # noqa: B008
    database: Path | None = typer.Option(None),  # noqa: B008
    save_report: bool = typer.Option(False),
    create_thesis: bool = typer.Option(False),
) -> None:
    """Build a deterministic point-in-time company research report."""
    try:
        evaluation_as_of = _parse_timestamp(as_of, "--as-of")
        companies = load_universe(universe_file)
        wanted = ticker.upper().strip()
        matches = [c for c in companies if c.ticker == wanted]
        if not matches:
            raise ValueError(f"Missing ticker: {wanted}")
        snapshots = load_feature_snapshots(features_directory)
        if wanted not in snapshots:
            raise ValueError(f"Missing feature snapshot: {wanted}")
        registry = default_registry()
        scorer = CompositeResearchScorer()
        ranked = BatchEvaluationService(registry, scorer).evaluate(
            companies, snapshots, UniversePolicy(), evaluation_as_of, include_ineligible=True
        )
        selected = next(item for item in ranked if item.ticker == wanted)
        report = ResearchReportBuilder(scorer, list(registry.all())).build(
            matches[0], snapshots[wanted], selected, evaluation_as_of
        )
        if output_json:
            output_json.write_text(report.model_dump_json(indent=2) + "\n")
        if output_markdown:
            output_markdown.write_text(render_markdown(report))
        if database and (save_report or create_thesis):
            store = LocalAnalyticalStore(database)
            try:
                if save_report:
                    store.store_research_report(  # type: ignore[attr-defined]
                        report
                    )
                if create_thesis:
                    store.store_thesis_record(  # type: ignore[attr-defined]
                        create_initial_thesis_record(report)
                    )
            finally:
                store.close()
    except (OSError, ValueError, RuntimeError, json.JSONDecodeError) as error:
        raise typer.BadParameter(str(error)) from error
    typer.echo(f"Built research report {report.report_id} for {report.ticker}")


@app.command("thesis-list")
def thesis_list(database: Path, ticker: str | None = typer.Option(None)) -> None:
    """List append-only thesis versions."""
    try:
        store = LocalAnalyticalStore(database)
        try:
            rows = store.load_thesis_history(  # type: ignore[attr-defined]
                ticker=ticker.upper().strip() if ticker else None
            )
        finally:
            store.close()
    except (OSError, ValueError, RuntimeError) as error:
        raise typer.BadParameter(str(error)) from error
    for row in rows:
        typer.echo(
            f"{row.ticker} {row.thesis_id} v{row.version} {row.status.value} known_at={row.known_at.isoformat()}"
        )


@app.command("thesis-show")
def thesis_show(
    database: Path, ticker: str = typer.Option(...), as_of: str | None = typer.Option(None)
) -> None:
    """Show latest or point-in-time visible thesis."""
    try:
        store = LocalAnalyticalStore(database)
        try:
            row = (
                store.load_thesis_as_of(  # type: ignore[attr-defined]
                    _parse_timestamp(as_of, "--as-of"), ticker=ticker
                )
                if as_of
                else store.load_latest_thesis(  # type: ignore[attr-defined]
                    ticker=ticker
                )
            )
        finally:
            store.close()
        if row is None:
            raise ValueError("No thesis visible for query")
    except (OSError, ValueError, RuntimeError) as error:
        raise typer.BadParameter(str(error)) from error
    typer.echo(row.model_dump_json(indent=2))


@app.command("thesis-transition")
def thesis_transition(
    database: Path,
    ticker: str = typer.Option(...),
    status: ThesisStatus = typer.Option(...),  # noqa: B008
    reason: str = typer.Option(...),
    effective_at: str = typer.Option(...),
    known_at: str = typer.Option(...),
) -> None:
    """Append a thesis lifecycle transition version."""
    try:
        store = LocalAnalyticalStore(database)
        try:
            latest = store.load_latest_thesis(  # type: ignore[attr-defined]
                ticker=ticker
            )
            if latest is None:
                raise ValueError("No thesis found for ticker")
            new = transition_thesis(
                latest,
                status,
                reason,
                _parse_timestamp(effective_at, "--effective-at"),
                _parse_timestamp(known_at, "--known-at"),
            )
            store.store_thesis_record(  # type: ignore[attr-defined]
                new
            )
        finally:
            store.close()
    except (OSError, ValueError, RuntimeError) as error:
        raise typer.BadParameter(str(error)) from error
    typer.echo(f"Stored thesis {new.thesis_id} v{new.version} {new.status.value}")


if __name__ == "__main__":
    app()
