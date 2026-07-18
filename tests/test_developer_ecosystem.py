from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest
from typer.testing import CliRunner

from smct_research.cli import app, default_registry
from smct_research.core.models import FeatureSnapshot
from smct_research.developer_ecosystem import (
    OfflineDeveloperHistoryProvider,
    OfflinePackageHistoryProvider,
    attach_to_snapshot,
    calculate_developer_ecosystem_features,
    load_repository_mappings,
)
from smct_research.developer_ecosystem.providers import GitHubProviderContract
from smct_research.developer_ecosystem.providers import load_repository_mappings as load_maps
from smct_research.providers.base import ProviderResponseError
from smct_research.scoring.composite import CompositeResearchScorer
from smct_research.signals.developer_ecosystem_momentum import DeveloperEcosystemMomentumSignal
from smct_research.storage.duckdb_store import LocalAnalyticalStore

EX = Path("examples/developer_ecosystem")
AS_OF = datetime.fromisoformat("2026-07-17T00:00:00+00:00")


def _features(packages: bool = True):
    repos = OfflineDeveloperHistoryProvider(EX / "history.json").fetch_repository_history("ACME")
    maps = load_repository_mappings(EX / "mappings.json", "ACME")
    pkgs = (
        OfflinePackageHistoryProvider(EX / "packages.json").fetch_package_history("ACME")
        if packages
        else []
    )
    return calculate_developer_ecosystem_features(repos, maps, "ACME", AS_OF, pkgs)


def test_point_in_time_mapping_growth_bot_concentration_and_adoption() -> None:
    result = _features()
    assert "future observation excluded" in " ".join(result.diagnostics)
    assert result.features["developer_active_contributor_growth_90d"] is not None
    assert result.features["developer_external_contributor_growth_90d"] is not None
    assert result.features["developer_package_download_growth_90d"] == pytest.approx(0.35)
    assert result.features["developer_contributor_concentration"] < 0.5
    assert result.features["developer_bot_activity_share"] > 0
    assert result.quality.score > 0.5


def test_missing_package_history_remains_missing_but_repo_evidence_scores() -> None:
    result = _features(False)
    assert result.features["developer_package_download_growth_90d"] is None
    assert result.features["developer_momentum_quality_score"] is not None
    assert result.quality.score < _features(True).quality.score


def test_mapping_effective_dates_and_ambiguous_mapping_rejection() -> None:
    repos = OfflineDeveloperHistoryProvider(EX / "history.json").fetch_repository_history("ACME")
    maps = load_repository_mappings(EX / "ambiguous_mappings.json", "ACME")
    with pytest.raises(ValueError, match="ambiguous repository mapping"):
        calculate_developer_ecosystem_features(repos, maps, "ACME", AS_OF, [])
    old = AS_OF.replace(year=2024)
    result = calculate_developer_ecosystem_features(repos, maps[:1], "ACME", old, [])
    assert result.features["developer_active_contributors_90d"] is None


def test_feature_attachment_signal_and_screening_unavailable() -> None:
    dev = _features()
    snapshot = FeatureSnapshot(ticker="ACME", as_of=AS_OF)
    attached = attach_to_snapshot(snapshot, dev)
    result = DeveloperEcosystemMomentumSignal().evaluate(attached)
    assert result.signal_id == "B1"
    assert result.confidence == pytest.approx(dev.quality.score)
    assert default_registry().get("B1").id == "B1"
    assert CompositeResearchScorer().weights["B1"] == pytest.approx(0.85)
    assert default_registry().evaluate_all(FeatureSnapshot(ticker="NOPE", as_of=AS_OF)) == []
    with pytest.raises(ValueError, match="ticker mismatch"):
        attach_to_snapshot(FeatureSnapshot(ticker="BOLT", as_of=AS_OF), dev)


def test_offline_providers_json_jsonl_malformed_and_github_contract(tmp_path: Path) -> None:
    (EX / "history.json").read_text()
    one = OfflineDeveloperHistoryProvider(EX / "history.json").fetch_repository_history("ACME")[0]
    jsonl = tmp_path / "history.jsonl"
    jsonl.write_text(one.model_dump_json() + "\n")
    assert OfflineDeveloperHistoryProvider(jsonl).fetch_repository_history("ACME")[0] == one
    bad = tmp_path / "bad.json"
    bad.write_text("1")
    with pytest.raises(ProviderResponseError):
        load_maps(bad)
    with pytest.raises(ProviderResponseError):
        GitHubProviderContract().fetch_repository_history("ACME")


def test_duckdb_round_trip_idempotent_and_conflict(tmp_path: Path) -> None:
    store = LocalAnalyticalStore(tmp_path / "dev.duckdb")
    repos = OfflineDeveloperHistoryProvider(EX / "history.json").fetch_repository_history("ACME")[
        :1
    ]
    pkgs = OfflinePackageHistoryProvider(EX / "packages.json").fetch_package_history("ACME")[:1]
    maps = load_repository_mappings(EX / "mappings.json", "ACME")[:1]
    store.store_repository_observations(repos)
    store.store_repository_observations(repos)
    store.store_package_observations(pkgs)
    store.store_repository_mappings(maps)
    assert store.load_repository_observations() == repos
    assert store.load_package_observations() == pkgs
    assert store.load_repository_mappings() == maps
    conflict = repos[0].model_copy(update={"commit_count": repos[0].commit_count + 1})
    with pytest.raises(ValueError, match="conflicting"):
        store.store_repository_observations([conflict])
    store.close()


def test_cli_json_csv_ambiguous_and_optional_package(tmp_path: Path) -> None:
    runner = CliRunner()
    out_json = tmp_path / "out.json"
    out_csv = tmp_path / "out.csv"
    ok = runner.invoke(
        app,
        [
            "developer-velocity",
            str(EX / "history.json"),
            "--mapping-file",
            str(EX / "mappings.json"),
            "--package-history-file",
            str(EX / "packages.json"),
            "--ticker",
            "ACME",
            "--as-of",
            "2026-07-17T00:00:00Z",
            "--output-json",
            str(out_json),
            "--output-csv",
            str(out_csv),
        ],
    )
    assert ok.exit_code == 0, ok.output
    assert out_json.exists() and out_csv.exists()
    no_pkg = runner.invoke(
        app,
        [
            "developer-velocity",
            str(EX / "history.json"),
            "--mapping-file",
            str(EX / "mappings.json"),
            "--ticker",
            "ACME",
            "--as-of",
            "2026-07-17T00:00:00Z",
        ],
    )
    assert no_pkg.exit_code == 0
    bad = runner.invoke(
        app,
        [
            "developer-velocity",
            str(EX / "history.json"),
            "--mapping-file",
            str(EX / "ambiguous_mappings.json"),
            "--ticker",
            "ACME",
            "--as-of",
            "2026-07-17T00:00:00Z",
        ],
    )
    assert bad.exit_code != 0
    assert "Traceback" not in bad.output
