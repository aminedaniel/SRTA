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
    assert "future-ending observation excluded" in " ".join(result.diagnostics)
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
    ambiguous = load_repository_mappings(EX / "ambiguous_mappings.json", "ACME")
    with pytest.raises(ValueError, match="ambiguous repository mapping"):
        calculate_developer_ecosystem_features(repos, ambiguous, "ACME", AS_OF, [])
    maps = load_repository_mappings(EX / "mappings.json", "ACME")
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


def test_missingness_true_zero_and_grain_policy() -> None:
    repos = OfflineDeveloperHistoryProvider(EX / "history.json").fetch_repository_history("ACME")
    maps = load_repository_mappings(EX / "mappings.json", "ACME")
    result = calculate_developer_ecosystem_features(repos, maps, "ACME", AS_OF, [])
    assert result.features["developer_active_contributors_30d"] is None
    assert result.features["developer_commit_velocity_180d"] is None
    assert "missing current repository window: 30d" in result.diagnostics
    assert "missing current repository window: 180d" in result.diagnostics

    zero = repos[1].model_copy(
        update={
            "provider_record_id": "zero-current",
            "repository_id": "zero",
            "owner": "acme",
            "name": "zero",
            "commit_count": 0,
            "active_contributor_count": 0,
            "new_contributor_count": 0,
            "returning_contributor_count": 0,
            "external_contributor_count": 0,
            "contributors": [],
        }
    )
    zero_map = maps[0].model_copy(update={"owner": "acme", "name": "zero", "organization": None})
    zero_result = calculate_developer_ecosystem_features([zero], [zero_map], "ACME", AS_OF, [])
    assert zero_result.features["developer_active_contributors_90d"] == 0
    assert zero_result.features["developer_commit_velocity_90d_raw"] == 0
    assert zero_result.features["developer_active_contributor_growth_90d"] is None


def test_missing_prior_and_package_download_missingness() -> None:
    repos = OfflineDeveloperHistoryProvider(EX / "history.json").fetch_repository_history("ACME")
    maps = load_repository_mappings(EX / "mappings.json", "ACME")
    current_only = [item for item in repos if item.provider_record_id == "acme-core-current"]
    result = calculate_developer_ecosystem_features(current_only, maps, "ACME", AS_OF, [])
    assert result.features["developer_active_contributors_90d"] is not None
    assert result.features["developer_active_contributor_growth_90d"] is None

    pkgs = OfflinePackageHistoryProvider(EX / "packages.json").fetch_package_history("ACME")
    missing_download = pkgs[1].model_copy(
        update={"provider_record_id": "missing-download", "download_count": None}
    )
    missing_result = calculate_developer_ecosystem_features(
        repos, maps, "ACME", AS_OF, [missing_download]
    )
    assert missing_result.features["developer_package_download_growth_90d"] is None
    zero_current = pkgs[1].model_copy(
        update={"provider_record_id": "zero-download", "download_count": 0}
    )
    zero_prior = pkgs[0].model_copy(
        update={"provider_record_id": "zero-download-prior", "download_count": 0}
    )
    zero_result = calculate_developer_ecosystem_features(
        repos, maps, "ACME", AS_OF, [zero_current, zero_prior]
    )
    assert zero_result.features["developer_package_download_growth_90d"] == 0


def test_mapping_specificity_exclusions_and_repository_status() -> None:
    repos = OfflineDeveloperHistoryProvider(EX / "history.json").fetch_repository_history("ACME")
    core = next(item for item in repos if item.provider_record_id == "acme-core-current")
    org_include = load_repository_mappings(EX / "mappings.json", "ACME")[0]
    repo_exclude = org_include.model_copy(
        update={"organization": None, "owner": "acme", "name": "core", "include": False}
    )
    excluded = calculate_developer_ecosystem_features(
        [core], [org_include, repo_exclude], "ACME", AS_OF, []
    )
    assert excluded.features["developer_active_contributors_90d"] is None
    assert not excluded.provenance
    assert "explicitly excluded repository" in " ".join(excluded.diagnostics)

    org_exclude = org_include.model_copy(update={"include": False})
    repo_include = org_include.model_copy(
        update={"organization": None, "owner": "acme", "name": "core"}
    )
    still_excluded = calculate_developer_ecosystem_features(
        [core], [org_exclude, repo_include], "ACME", AS_OF, []
    )
    assert still_excluded.features["developer_active_contributors_90d"] == pytest.approx(5.0)

    archived = core.model_copy(update={"provider_record_id": "archived", "is_archived": True})
    fork = core.model_copy(
        update={
            "provider_record_id": "fork",
            "repository_id": "fork",
            "name": "fork",
            "is_fork": True,
        }
    )
    mirror = core.model_copy(
        update={
            "provider_record_id": "mirror",
            "repository_id": "mirror",
            "name": "mirror",
            "is_mirror": True,
        }
    )
    status = calculate_developer_ecosystem_features(
        [archived, fork, mirror], [org_include], "ACME", AS_OF, []
    )
    joined = " ".join(status.diagnostics)
    assert "archived repository excluded" in joined
    assert "fork repository excluded" in joined
    assert "mirror repository excluded" in joined


def test_package_mapping_and_provider_scoped_provenance() -> None:
    repos = OfflineDeveloperHistoryProvider(EX / "history.json").fetch_repository_history("ACME")
    maps = load_repository_mappings(EX / "mappings.json", "ACME")
    pkgs = OfflinePackageHistoryProvider(EX / "packages.json").fetch_package_history("ACME")
    result = calculate_developer_ecosystem_features(repos, maps, "ACME", AS_OF, pkgs)
    assert any(key.startswith("developer:repository:github:r1:") for key in result.provenance)
    assert any(key.startswith("developer:package:pypi:pypi:acme-sdk:") for key in result.provenance)
    assert set(result.provenance) == set(result.source_as_of)
    assert "future" not in " ".join(result.provenance)

    unmapped = pkgs[1].model_copy(
        update={"provider_record_id": "unmapped-pkg", "package_name": "other"}
    )
    unmapped_result = calculate_developer_ecosystem_features(repos, maps, "ACME", AS_OF, [unmapped])
    assert "unmapped package excluded" in " ".join(unmapped_result.diagnostics)
    assert not any("unmapped-pkg" in key for key in unmapped_result.provenance)
    wrong_repo = pkgs[1].model_copy(
        update={"provider_record_id": "wrong-repo", "repository_id": "not-selected"}
    )
    wrong = calculate_developer_ecosystem_features(repos, maps, "ACME", AS_OF, [wrong_repo])
    assert "linked package evidence rejected" in " ".join(wrong.diagnostics)


def test_custom_config_bot_noise_and_star_spike(tmp_path: Path) -> None:
    from smct_research.developer_ecosystem.config import (
        DeveloperEcosystemConfig,
        load_developer_ecosystem_config,
    )

    repos = OfflineDeveloperHistoryProvider(EX / "history.json").fetch_repository_history("ACME")
    maps = load_repository_mappings(EX / "mappings.json", "ACME")
    cfg = DeveloperEcosystemConfig.model_validate(
        {
            "bot_filtering": {"allowlist_logins": ["renovate"], "denylist_logins": ["bob"]},
            "activity_thresholds": {"meaningful_activity_events_90d": 200},
            "star_spike_diagnostics": {
                "spike_growth_threshold": 0.1,
                "corroborating_growth_threshold": 99,
            },
        }
    )
    result = calculate_developer_ecosystem_features(repos, maps, "ACME", AS_OF, [], config=cfg)
    assert result.features["developer_ecosystem_breadth"] == 0
    assert result.features["developer_bot_activity_share"] > 0.1
    assert "star spike lacks" in " ".join(result.diagnostics)

    noisy = repos[1].model_copy(
        update={
            "provider_record_id": "noisy",
            "generated_activity_share": 0.6,
            "mass_formatting_share": 0.6,
        }
    )
    noisy_result = calculate_developer_ecosystem_features(
        [noisy], maps, "ACME", AS_OF, [], config=cfg
    )
    assert noisy_result.features["developer_commit_velocity_90d_raw"] > 0
    assert noisy_result.features["developer_commit_velocity_90d_adjusted"] == 0

    config_file = tmp_path / "dev.yaml"
    config_file.write_text(
        "lookback_windows_days: [90]\nactivity_thresholds:\n  meaningful_activity_events_90d: 999\n"
    )
    assert load_developer_ecosystem_config(config_file).lookback_windows_days == [90]


def test_provider_duplicate_conflict_and_model_validation(tmp_path: Path) -> None:
    one = OfflineDeveloperHistoryProvider(EX / "history.json").fetch_repository_history("ACME")[0]
    duplicate_file = tmp_path / "dupes.json"
    duplicate_file.write_text("[" + one.model_dump_json() + "," + one.model_dump_json() + "]")
    assert (
        len(OfflineDeveloperHistoryProvider(duplicate_file).fetch_repository_history("ACME")) == 1
    )

    conflict = one.model_copy(update={"commit_count": one.commit_count + 1})
    conflict_file = tmp_path / "conflict.json"
    conflict_file.write_text("[" + one.model_dump_json() + "," + conflict.model_dump_json() + "]")
    with pytest.raises(ProviderResponseError, match="conflicting immutable"):
        OfflineDeveloperHistoryProvider(conflict_file).fetch_repository_history("ACME")

    invalid_file = tmp_path / "invalid.json"
    invalid = one.model_dump(mode="json")
    invalid["provider_record_id"] = ""
    invalid_file.write_text("[" + __import__("json").dumps(invalid) + "]")
    with pytest.raises(ProviderResponseError, match="invalid repository observation"):
        OfflineDeveloperHistoryProvider(invalid_file).fetch_repository_history("ACME")


def test_matched_series_growth_and_provider_identity() -> None:
    repos = OfflineDeveloperHistoryProvider(EX / "history.json").fetch_repository_history("ACME")
    maps = load_repository_mappings(EX / "mappings.json", "ACME")
    current = next(item for item in repos if item.provider_record_id == "acme-core-current")
    prior = next(item for item in repos if item.provider_record_id == "acme-core-prior")
    current_only = current.model_copy(
        update={"provider_record_id": "new-current", "repository_id": "new", "name": "new"}
    )
    prior_only = prior.model_copy(
        update={"provider_record_id": "old-prior", "repository_id": "old", "name": "old"}
    )
    result = calculate_developer_ecosystem_features(
        [current, prior, current_only, prior_only], maps, "ACME", AS_OF, []
    )
    assert result.features["developer_active_contributor_growth_90d"] == pytest.approx(2 / 3)
    assert "unmatched-current repository series" in " ".join(result.diagnostics)
    assert "unmatched-prior repository series" in " ".join(result.diagnostics)

    other_provider_prior = prior.model_copy(
        update={"provider": "gitlab", "provider_record_id": "gitlab-prior"}
    )
    no_match = calculate_developer_ecosystem_features(
        [current, other_provider_prior], maps, "ACME", AS_OF, []
    )
    assert no_match.features["developer_active_contributor_growth_90d"] is None

    dup_provider = current.model_copy(
        update={"provider": "gitlab", "provider_record_id": "gitlab-current"}
    )
    gitlab_map = maps[0].model_copy(update={"provider": "gitlab"})
    with pytest.raises(ValueError, match="ambiguous multi-provider"):
        calculate_developer_ecosystem_features(
            [current, dup_provider], maps + [gitlab_map], "ACME", AS_OF, []
        )


def test_historical_mapping_interval_and_stable_mapping_key() -> None:
    repos = OfflineDeveloperHistoryProvider(EX / "history.json").fetch_repository_history("ACME")
    current = next(item for item in repos if item.provider_record_id == "acme-core-current")
    prior = next(item for item in repos if item.provider_record_id == "acme-core-prior")
    mapping = load_repository_mappings(EX / "mappings.json", "ACME")[0]
    after_prior = mapping.model_copy(update={"effective_from": current.observation_window_start})
    result = calculate_developer_ecosystem_features(
        [current, prior], [after_prior], "ACME", AS_OF, []
    )
    assert result.features["developer_active_contributor_growth_90d"] is None
    expired = mapping.model_copy(update={"effective_to": prior.observation_window_end})
    expired_result = calculate_developer_ecosystem_features([current], [expired], "ACME", AS_OF, [])
    assert expired_result.features["developer_active_contributors_90d"] is None
    future_known = mapping.model_copy(update={"known_at": AS_OF.replace(year=2027)})
    future_result = calculate_developer_ecosystem_features(
        [current], [future_known], "ACME", AS_OF, []
    )
    assert future_result.features["developer_active_contributors_90d"] is None

    stable = calculate_developer_ecosystem_features([current], [mapping], "ACME", AS_OF, [])
    keys = [key for key in stable.provenance if key.startswith("developer:mapping:github:")]
    assert keys and len(keys[0].rsplit(":", 1)[1]) == 64
    assert (
        keys[0]
        == [key for key in stable.provenance if key.startswith("developer:mapping:github:")][0]
    )


def test_package_identity_ecosystem_and_revision_semantics(tmp_path: Path) -> None:
    import json

    repos = OfflineDeveloperHistoryProvider(EX / "history.json").fetch_repository_history("ACME")
    maps = load_repository_mappings(EX / "mappings.json", "ACME")
    pypi_current = OfflinePackageHistoryProvider(EX / "packages.json").fetch_package_history(
        "ACME"
    )[1]
    from smct_research.developer_ecosystem.models import PackageObservation

    npm_data = pypi_current.model_dump(mode="json")
    npm_data.update({"provider_record_id": "npm-current", "ecosystem": "npm", "provider": "npm"})
    npm_current = PackageObservation.model_validate(npm_data)
    from smct_research.developer_ecosystem.models import PackageSelector

    npm_map = maps[0].model_copy(
        update={
            "package_names": (),
            "package_selectors": (
                PackageSelector.model_validate(
                    {"ecosystem": "npm", "package_name": "acme-sdk", "provider": "npm"}
                ),
            ),
        }
    )
    npm = calculate_developer_ecosystem_features(repos, [npm_map], "ACME", AS_OF, [npm_current])
    assert "unmapped package" not in " ".join(npm.diagnostics)
    assert any("developer:package:npm:npm:acme-sdk" in key for key in npm.provenance)
    pypi_unmapped = calculate_developer_ecosystem_features(
        repos, [npm_map], "ACME", AS_OF, [pypi_current]
    )
    assert "unmapped package excluded" in " ".join(pypi_unmapped.diagnostics)

    correction = pypi_current.model_copy(
        update={
            "provider_record_id": "pkg-correction",
            "available_at": AS_OF.replace(day=18),
            "download_count": 9999,
        }
    )
    path = tmp_path / "pkg-revision.json"
    path.write_text(
        json.dumps([pypi_current.model_dump(mode="json"), correction.model_dump(mode="json")])
    )
    assert len(OfflinePackageHistoryProvider(path).fetch_package_history("ACME")) == 2


def test_invalid_config_and_fork_policy(tmp_path: Path) -> None:
    from smct_research.developer_ecosystem.config import load_developer_ecosystem_config

    bad = tmp_path / "bad.yaml"
    bad.write_text("lookback_windows_days: [30]\n")
    with pytest.raises(ValueError, match="include 90"):
        load_developer_ecosystem_config(bad)
    bad_regex = tmp_path / "regex.yaml"
    bad_regex.write_text("bot_filtering:\n  bot_login_patterns: ['[']\n")
    with pytest.raises(ValueError, match="invalid developer ecosystem config"):
        load_developer_ecosystem_config(bad_regex)

    repos = OfflineDeveloperHistoryProvider(EX / "history.json").fetch_repository_history("ACME")
    fork = next(
        item for item in repos if item.provider_record_id == "acme-core-current"
    ).model_copy(update={"provider_record_id": "fork-policy", "is_fork": True})
    mapping = load_repository_mappings(EX / "mappings.json", "ACME")[0]
    excluded = calculate_developer_ecosystem_features([fork], [mapping], "ACME", AS_OF, [])
    assert excluded.features["developer_active_contributors_90d"] is None
    included = calculate_developer_ecosystem_features(
        [fork], [mapping.model_copy(update={"include_forks": True})], "ACME", AS_OF, []
    )
    assert included.features["developer_active_contributors_90d"] is not None
    assert "fork repository included by explicit mapping" in " ".join(included.diagnostics)


def test_unmatched_prior_extreme_values_do_not_affect_growth_or_provenance() -> None:
    repos = OfflineDeveloperHistoryProvider(EX / "history.json").fetch_repository_history("ACME")
    maps = load_repository_mappings(EX / "mappings.json", "ACME")
    current = next(item for item in repos if item.provider_record_id == "acme-core-current")
    prior = next(item for item in repos if item.provider_record_id == "acme-core-prior")
    huge_prior = prior.model_copy(
        update={
            "provider_record_id": "huge-unmatched-prior",
            "repository_id": "huge",
            "name": "huge",
            "releases": prior.releases.model_copy(update={"release_count": 9999}),
            "issues": prior.issues.model_copy(update={"open_count": 9999}),
            "popularity": prior.popularity.model_copy(
                update={"stargazer_count": 9999, "fork_count": 9999}
            ),
        }
    )
    result = calculate_developer_ecosystem_features(
        [current, prior, huge_prior], maps, "ACME", AS_OF, []
    )
    assert result.features["developer_release_growth_90d"] == pytest.approx(2.0)
    assert result.features["developer_stargazer_growth_90d"] == pytest.approx(0.4)
    assert result.features["developer_fork_growth_90d"] == pytest.approx(0.6)
    assert result.features["developer_issue_backlog_growth_90d"] == pytest.approx(-0.2)
    assert "huge-unmatched-prior" not in " ".join(result.provenance)
    assert "huge-unmatched-prior" not in " ".join(result.source_as_of)


def test_package_only_mapping_and_provider_ambiguity(tmp_path: Path) -> None:
    import json

    from smct_research.developer_ecosystem.models import PackageObservation, PackageSelector

    repos = OfflineDeveloperHistoryProvider(EX / "history.json").fetch_repository_history("ACME")
    pkg = OfflinePackageHistoryProvider(EX / "packages.json").fetch_package_history("ACME")[1]
    package_only = load_repository_mappings(EX / "mappings.json", "ACME")[0].model_copy(
        update={
            "organization": None,
            "package_selectors": (
                PackageSelector.model_validate({"ecosystem": "pypi", "package_name": "acme-sdk"}),
            ),
        }
    )
    independent_pkg = pkg.model_copy(update={"repository_id": None})
    result = calculate_developer_ecosystem_features(
        repos, [package_only], "ACME", AS_OF, [independent_pkg]
    )
    assert result.features["developer_active_contributors_90d"] is None
    assert result.metadata["eligible_repository_count"] == 0
    assert any(key.startswith("developer:package:pypi:pypi:acme-sdk") for key in result.provenance)

    other = PackageObservation.model_validate(
        {
            **independent_pkg.model_dump(mode="json"),
            "provider": "other",
            "provider_record_id": "other-pkg",
        }
    )
    with pytest.raises(ValueError, match="ambiguous multi-provider package"):
        calculate_developer_ecosystem_features(
            repos, [package_only], "ACME", AS_OF, [independent_pkg, other]
        )
    provider_specific = package_only.model_copy(
        update={
            "package_selectors": (
                PackageSelector.model_validate(
                    {"ecosystem": "pypi", "package_name": "acme-sdk", "provider": "pypi"}
                ),
            )
        }
    )
    selected = calculate_developer_ecosystem_features(
        repos, [provider_specific], "ACME", AS_OF, [independent_pkg, other]
    )
    assert not any("other-pkg" in key for key in selected.provenance)

    maps_file = tmp_path / "maps.json"
    second_pkg = package_only.model_copy(
        update={
            "package_selectors": (
                PackageSelector.model_validate({"ecosystem": "npm", "package_name": "acme-sdk"}),
            )
        }
    )
    maps_file.write_text(
        json.dumps([package_only.model_dump(mode="json"), second_pkg.model_dump(mode="json")])
    )
    assert len(load_repository_mappings(maps_file, "ACME")) == 2


def test_mapping_known_after_observation_before_evaluation_and_permutation() -> None:
    import itertools

    repos = OfflineDeveloperHistoryProvider(EX / "history.json").fetch_repository_history("ACME")
    current = next(item for item in repos if item.provider_record_id == "acme-core-current")
    mapping = load_repository_mappings(EX / "mappings.json", "ACME")[0].model_copy(
        update={"known_at": AS_OF}
    )
    eligible = calculate_developer_ecosystem_features([current], [mapping], "ACME", AS_OF, [])
    assert eligible.features["developer_active_contributors_90d"] is not None
    too_early = calculate_developer_ecosystem_features(
        [current],
        [mapping.model_copy(update={"known_at": AS_OF.replace(day=18)})],
        "ACME",
        AS_OF,
        [],
    )
    assert too_early.features["developer_active_contributors_90d"] is None

    exact_include = mapping.model_copy(
        update={"organization": None, "owner": "acme", "name": "core"}
    )
    org_exclude = mapping.model_copy(update={"include": False})
    outcomes = []
    for ordering in itertools.permutations([exact_include, org_exclude]):
        out = calculate_developer_ecosystem_features([current], list(ordering), "ACME", AS_OF, [])
        outcomes.append(out.features["developer_active_contributors_90d"])
    assert outcomes == [outcomes[0]] * len(outcomes)


def test_nonfinite_config_and_signal_unavailable_without_directional_history(
    tmp_path: Path,
) -> None:
    bad = tmp_path / "nan.yaml"
    bad.write_text("b1_scoring_weights:\n  bot_penalty: .nan\n")
    result = CliRunner().invoke(
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
            "--config",
            str(bad),
        ],
    )
    assert result.exit_code != 0
    assert "Traceback" not in result.output

    repos = OfflineDeveloperHistoryProvider(EX / "history.json").fetch_repository_history("ACME")
    current_only = [item for item in repos if item.provider_record_id == "acme-core-current"]
    features = calculate_developer_ecosystem_features(
        current_only, load_repository_mappings(EX / "mappings.json", "ACME"), "ACME", AS_OF, []
    )
    assert features.features["developer_momentum_quality_score"] is None
    assert "insufficient matched directional history" in " ".join(features.diagnostics)


def test_linked_package_evidence_respects_repository_exclusions() -> None:
    from smct_research.developer_ecosystem.models import PackageSelector

    repos = OfflineDeveloperHistoryProvider(EX / "history.json").fetch_repository_history("ACME")
    current_repo = next(item for item in repos if item.provider_record_id == "acme-sdk-current")
    pkg = OfflinePackageHistoryProvider(EX / "packages.json").fetch_package_history("ACME")[1]
    base_map = load_repository_mappings(EX / "mappings.json", "ACME")[0]
    package_only = base_map.model_copy(
        update={
            "organization": None,
            "package_selectors": (
                PackageSelector.model_validate(
                    {"ecosystem": "pypi", "package_name": "acme-sdk", "repository_id": "r2"}
                ),
            ),
        }
    )
    excluded_repo_mapping = base_map.model_copy(
        update={"organization": None, "repository_id": "r2", "include": False}
    )
    result = calculate_developer_ecosystem_features(
        [current_repo], [package_only, excluded_repo_mapping], "ACME", AS_OF, [pkg]
    )
    assert "linked package evidence rejected" in " ".join(result.diagnostics)
    assert not any("pkg-current" in key for key in result.provenance)

    linked_by_name = pkg.model_copy(
        update={"repository_id": None, "repository_owner": "acme", "repository_name": "sdk"}
    )
    name_result = calculate_developer_ecosystem_features(
        [current_repo.model_copy(update={"is_archived": True})],
        [
            package_only.model_copy(
                update={
                    "package_selectors": (
                        PackageSelector.model_validate(
                            {
                                "ecosystem": "pypi",
                                "package_name": "acme-sdk",
                                "repository_owner": "acme",
                                "repository_name": "sdk",
                            }
                        ),
                    )
                }
            )
        ],
        "ACME",
        AS_OF,
        [linked_by_name],
    )
    assert "linked package evidence rejected" in " ".join(name_result.diagnostics)

    repo_status_map = base_map.model_copy(
        update={"organization": None, "repository_id": "r2", "package_selectors": ()}
    )
    for field in ("is_archived", "is_mirror", "is_fork"):
        repo = current_repo.model_copy(update={field: True})
        out = calculate_developer_ecosystem_features(
            [repo], [repo_status_map, package_only], "ACME", AS_OF, [pkg]
        )
        assert "linked package evidence rejected" in " ".join(out.diagnostics)

    independent = pkg.model_copy(update={"repository_id": None})
    independent_selector = package_only.model_copy(
        update={
            "package_selectors": (
                PackageSelector.model_validate({"ecosystem": "pypi", "package_name": "acme-sdk"}),
            )
        }
    )
    valid = calculate_developer_ecosystem_features(
        [], [independent_selector], "ACME", AS_OF, [independent]
    )
    assert any("pkg-current" in key for key in valid.provenance)


def test_directional_release_and_fork_growth_are_scored_and_window_order_stable() -> None:
    from smct_research.developer_ecosystem.config import DeveloperEcosystemConfig

    repos = OfflineDeveloperHistoryProvider(EX / "history.json").fetch_repository_history("ACME")
    current = next(item for item in repos if item.provider_record_id == "acme-core-current")
    prior = next(item for item in repos if item.provider_record_id == "acme-core-prior")
    maps = load_repository_mappings(EX / "mappings.json", "ACME")

    release_positive_current = current.model_copy(
        update={
            "active_contributor_count": prior.active_contributor_count,
            "external_contributor_count": prior.external_contributor_count,
            "issues": prior.issues,
            "popularity": prior.popularity,
            "releases": current.releases.model_copy(update={"release_count": 6}),
        }
    )
    positive = calculate_developer_ecosystem_features(
        [release_positive_current, prior], maps, "ACME", AS_OF, []
    )
    assert positive.features["developer_release_growth_90d"] > 0
    assert positive.features["developer_momentum_quality_score"] > 0

    release_negative_current = release_positive_current.model_copy(
        update={"releases": current.releases.model_copy(update={"release_count": 0})}
    )
    negative = calculate_developer_ecosystem_features(
        [release_negative_current, prior], maps, "ACME", AS_OF, []
    )
    assert negative.features["developer_release_growth_90d"] < 0
    assert negative.features["developer_momentum_quality_score"] < 0

    fork_current = release_positive_current.model_copy(
        update={
            "releases": prior.releases,
            "popularity": prior.popularity.model_copy(update={"fork_count": 99}),
        }
    )
    forked = calculate_developer_ecosystem_features([fork_current, prior], maps, "ACME", AS_OF, [])
    assert forked.features["developer_fork_growth_90d"] > 0
    assert forked.features["developer_momentum_quality_score"] > 0

    metadata_keys = (
        "matched_repository_count",
        "matched_package_count",
        "repository_coverage_ratio",
    )
    baseline = calculate_developer_ecosystem_features([current, prior], maps, "ACME", AS_OF, [])
    reordered_cfg = DeveloperEcosystemConfig.model_validate(
        {
            "lookback_windows_days": [180, 30, 90],
            "minimum_history_requirements": {"preferred_windows_days": 90},
        }
    )
    reordered = calculate_developer_ecosystem_features(
        [current, prior], maps, "ACME", AS_OF, [], config=reordered_cfg
    )
    only90 = calculate_developer_ecosystem_features(
        [current, prior], maps, "ACME", AS_OF, [], windows=(90,)
    )
    assert {key: baseline.metadata[key] for key in metadata_keys} == {
        key: reordered.metadata[key] for key in metadata_keys
    }
    assert {key: baseline.metadata[key] for key in metadata_keys} == {
        key: only90.metadata[key] for key in metadata_keys
    }


def test_interval_contract_config_validation_and_canonical_mapping_identity(tmp_path: Path) -> None:
    import json
    import subprocess
    import sys

    from smct_research.developer_ecosystem.config import DeveloperEcosystemConfig
    from smct_research.developer_ecosystem.models import (
        PackageSelector,
        RepositoryObservation,
        canonical_mapping_identity,
    )
    from smct_research.developer_ecosystem.providers import (
        load_repository_mappings as provider_load,
    )

    repos = OfflineDeveloperHistoryProvider(EX / "history.json").fetch_repository_history("ACME")
    current = next(item for item in repos if item.provider_record_id == "acme-core-current")
    with pytest.raises(ValueError, match="available_at cannot be before"):
        RepositoryObservation.model_validate(
            {
                **current.model_dump(mode="json"),
                "provider_record_id": "bad-available",
                "available_at": "2026-07-15T00:00:00Z",
            }
        )
    future = current.model_copy(
        update={
            "provider_record_id": "future-ending",
            "observation_window_start": AS_OF.replace(month=4, day=19),
            "observation_window_end": AS_OF.replace(day=18),
            "available_at": AS_OF.replace(day=18),
        }
    )
    maps = load_repository_mappings(EX / "mappings.json", "ACME")
    out = calculate_developer_ecosystem_features([future], maps, "ACME", AS_OF, [])
    assert "future-ending observation excluded" in " ".join(out.diagnostics)

    exactly = current.model_copy(update={"observation_window_end": AS_OF, "available_at": AS_OF})
    exact_out = calculate_developer_ecosystem_features([exactly], maps, "ACME", AS_OF, [])
    assert exact_out.features["developer_active_contributors_90d"] is not None

    with pytest.raises(ValueError, match="meaningful_activity_events_90d"):
        DeveloperEcosystemConfig.model_validate(
            {"activity_thresholds": {"meaningful_activity_events_90d": -1}}
        )
    with pytest.raises(ValueError, match="top_one_warning_share"):
        DeveloperEcosystemConfig.model_validate(
            {
                "contributor_concentration_penalties": {
                    "top_one_warning_share": 0.8,
                    "top_one_high_risk_share": 0.4,
                }
            }
        )

    selector_a = PackageSelector.model_validate({"ecosystem": "pypi", "package_name": "a"})
    selector_b = PackageSelector.model_validate({"ecosystem": "npm", "package_name": "b"})
    mapping = maps[0].model_copy(update={"package_selectors": (selector_a, selector_b)})
    reversed_mapping = maps[0].model_copy(update={"package_selectors": (selector_b, selector_a)})
    assert canonical_mapping_identity(mapping) == canonical_mapping_identity(reversed_mapping)
    different = maps[0].model_copy(update={"package_selectors": (selector_a,)})
    assert canonical_mapping_identity(mapping) != canonical_mapping_identity(different)

    mapping_file = tmp_path / "maps.json"
    mapping_file.write_text(
        json.dumps([mapping.model_dump(mode="json"), reversed_mapping.model_dump(mode="json")])
    )
    assert len(provider_load(mapping_file, "ACME")) == 1
    store = LocalAnalyticalStore(tmp_path / "canon.duckdb")
    store.store_repository_mappings([mapping, reversed_mapping])
    stored_mapping = store.load_repository_mappings()[0]
    assert canonical_mapping_identity(stored_mapping) == canonical_mapping_identity(mapping)
    store.close()

    script = (
        "from smct_research.developer_ecosystem.models import PackageSelector,RepositoryMapping,canonical_mapping_identity;"
        f"import json; data={json.dumps(mapping.model_dump(mode='json'))!r};"
        "print(canonical_mapping_identity(RepositoryMapping.model_validate(json.loads(data))))"
    )
    first = subprocess.check_output(
        [sys.executable, "-c", script], text=True, env={"PYTHONPATH": "src"}
    )
    second = subprocess.check_output(
        [sys.executable, "-c", script], text=True, env={"PYTHONPATH": "src"}
    )
    assert first == second


def test_package_series_identity_scopes_repository_provider_and_linkage(tmp_path: Path) -> None:
    import json

    from smct_research.developer_ecosystem.models import (
        PackageSelector,
        canonical_package_series_identity,
    )
    from smct_research.developer_ecosystem.providers import OfflinePackageHistoryProvider

    repos = OfflineDeveloperHistoryProvider(EX / "history.json").fetch_repository_history("ACME")
    core = next(item for item in repos if item.provider_record_id == "acme-core-current")
    sdk = next(item for item in repos if item.provider_record_id == "acme-sdk-current")
    base_map = load_repository_mappings(EX / "mappings.json", "ACME")[0]
    repo_core = base_map.model_copy(
        update={"organization": None, "repository_id": "r1", "package_selectors": ()}
    )
    repo_sdk = base_map.model_copy(
        update={"organization": None, "repository_id": "r2", "package_selectors": ()}
    )
    package_map = base_map.model_copy(
        update={
            "organization": None,
            "package_selectors": (
                PackageSelector.model_validate({"ecosystem": "pypi", "package_name": "acme-sdk"}),
            ),
        }
    )
    pkg = OfflinePackageHistoryProvider(EX / "packages.json").fetch_package_history("ACME")[1]

    provider_collision_map = package_map.model_copy(update={"provider": "gitlab"})
    provider_collision = calculate_developer_ecosystem_features(
        [sdk], [repo_sdk, provider_collision_map], "ACME", AS_OF, [pkg]
    )
    assert "linked package evidence rejected" in " ".join(provider_collision.diagnostics)

    contradictory = pkg.model_copy(
        update={"repository_id": "r2", "repository_owner": "acme", "repository_name": "core"}
    )
    contradictory_out = calculate_developer_ecosystem_features(
        [core, sdk], [repo_core, repo_sdk, package_map], "ACME", AS_OF, [contradictory]
    )
    assert "contradictory repository identity" in " ".join(contradictory_out.diagnostics)

    current_repo_a = pkg.model_copy(update={"repository_owner": "acme", "repository_name": "sdk"})
    prior_repo_b = pkg.model_copy(
        update={
            "provider_record_id": "pkg-prior-other-repo",
            "repository_id": "r1",
            "repository_owner": "acme",
            "repository_name": "core",
            "download_count": 9999,
            "observation_window_start": datetime.fromisoformat("2026-01-18T00:00:00+00:00"),
            "observation_window_end": datetime.fromisoformat("2026-04-17T00:00:00+00:00"),
            "available_at": datetime.fromisoformat("2026-04-18T00:00:00+00:00"),
        }
    )
    mismatch = calculate_developer_ecosystem_features(
        [core, sdk],
        [repo_core, repo_sdk, package_map],
        "ACME",
        AS_OF,
        [current_repo_a, prior_repo_b],
    )
    assert mismatch.features["developer_package_download_growth_90d"] is None
    assert "unmatched-prior package series" in " ".join(mismatch.diagnostics)

    other_repo_pkg = current_repo_a.model_copy(
        update={
            "provider_record_id": "same-name-other-repo",
            "repository_id": "r1",
            "repository_name": "core",
        }
    )
    assert canonical_package_series_identity(
        current_repo_a, "github", grain_days=90
    ) != canonical_package_series_identity(other_repo_pkg, "github", grain_days=90)

    pkg_file = tmp_path / "packages.json"
    pkg_file.write_text(
        json.dumps([current_repo_a.model_dump(mode="json"), other_repo_pkg.model_dump(mode="json")])
    )
    loaded = OfflinePackageHistoryProvider(pkg_file).fetch_package_history("ACME")
    assert len(loaded) == 2
    store = LocalAnalyticalStore(tmp_path / "pkg_identity.duckdb")
    store.store_package_observations(loaded)
    round_trip = store.load_package_observations()
    store.close()
    assert {
        canonical_package_series_identity(item, include_interval=True) for item in round_trip
    } == {canonical_package_series_identity(item, include_interval=True) for item in loaded}


def test_repository_mapping_deduplication_and_normalized_identity() -> None:
    from smct_research.developer_ecosystem.models import PackageSelector, canonical_mapping_identity

    repos = OfflineDeveloperHistoryProvider(EX / "history.json").fetch_repository_history("ACME")
    core = next(item for item in repos if item.provider_record_id == "acme-core-current")
    base = load_repository_mappings(EX / "mappings.json", "ACME")[0]
    exact = base.model_copy(
        update={"organization": None, "owner": "ACME", "name": " Core ", "package_selectors": ()}
    )
    duplicate = base.model_copy(
        update={"organization": None, "owner": "acme", "name": "core", "package_selectors": ()}
    )
    assert canonical_mapping_identity(exact) == canonical_mapping_identity(duplicate)
    duplicate_result = calculate_developer_ecosystem_features(
        [core], [exact, duplicate], "ACME", AS_OF, []
    )
    assert duplicate_result.features["developer_active_contributors_90d"] is not None

    exclusion = exact.model_copy(update={"include": False})
    exclusion_duplicate = duplicate.model_copy(update={"include": False})
    excluded = calculate_developer_ecosystem_features(
        [core], [exclusion, exclusion_duplicate], "ACME", AS_OF, []
    )
    assert excluded.features["developer_active_contributors_90d"] is None

    selector_ws = PackageSelector.model_validate(
        {
            "ecosystem": "pypi",
            "package_name": " Acme-SDK ",
            "repository_owner": " ACME ",
            "repository_name": " SDK ",
        }
    )
    selector_norm = PackageSelector.model_validate(
        {
            "ecosystem": "pypi",
            "package_name": "acme-sdk",
            "repository_owner": "acme",
            "repository_name": "sdk",
        }
    )
    map_ws = base.model_copy(update={"package_selectors": (selector_ws,)})
    map_norm = base.model_copy(update={"package_selectors": (selector_norm,)})
    assert canonical_mapping_identity(map_ws) == canonical_mapping_identity(map_norm)

    conflicting = duplicate.model_copy(update={"weight": duplicate.weight + 0.1})
    with pytest.raises(ValueError, match="ambiguous repository mapping"):
        calculate_developer_ecosystem_features([core], [duplicate, conflicting], "ACME", AS_OF, [])
