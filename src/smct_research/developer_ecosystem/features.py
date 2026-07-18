from __future__ import annotations

import json
import math
import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta

from smct_research.core.models import FeatureSnapshot, normalize_utc
from smct_research.developer_ecosystem.config import DeveloperEcosystemConfig
from smct_research.developer_ecosystem.models import (
    ContributorActivity,
    DeveloperDataQuality,
    DeveloperEcosystemFeatures,
    PackageObservation,
    RepositoryMapping,
    RepositoryObservation,
)


@dataclass(frozen=True)
class SelectedRepositoryObservation:
    observation: RepositoryObservation
    mapping: RepositoryMapping
    weight: float


@dataclass(frozen=True)
class SelectedPackageObservation:
    observation: PackageObservation
    mapping: RepositoryMapping


def safe_relative_change(current: float | None, prior: float | None) -> float | None:
    if current is None or prior is None:
        return None
    if abs(prior) >= 1e-9:
        value = (current - prior) / abs(prior)
    elif abs(current) + abs(prior) > 0:
        value = 2 * (current - prior) / (abs(current) + abs(prior))
    else:
        return 0.0
    return value if math.isfinite(value) else None


def _duration_days(start: datetime, end: datetime) -> float:
    return (normalize_utc(end) - normalize_utc(start)).total_seconds() / 86400


def _matches_duration(
    obs: RepositoryObservation | PackageObservation, days: int, tolerance: int
) -> bool:
    return (
        abs(_duration_days(obs.observation_window_start, obs.observation_window_end) - days)
        <= tolerance
    )


def _current_bounds(as_of: datetime, days: int) -> tuple[datetime, datetime]:
    return as_of - timedelta(days=days), as_of


def _prior_bounds(as_of: datetime, days: int) -> tuple[datetime, datetime]:
    return as_of - timedelta(days=2 * days), as_of - timedelta(days=days)


def _in_bounds(
    obs: RepositoryObservation | PackageObservation, start: datetime, end: datetime, tolerance: int
) -> bool:
    tol = timedelta(days=tolerance)
    return (
        start - tol <= obs.observation_window_start <= start + tol
        and end - tol <= obs.observation_window_end <= end + tol
    )


def _mapping_specificity(mapping: RepositoryMapping) -> int:
    if mapping.repository_id:
        return 3
    if mapping.owner and mapping.name:
        return 2
    if mapping.organization:
        return 1
    return 0


def _mapping_identity(mapping: RepositoryMapping) -> str:
    payload = mapping.model_dump(mode="json")
    return json.dumps(payload, sort_keys=True)


def _mapping_key(mapping: RepositoryMapping) -> str:
    return f"developer:mapping:{mapping.provider}:{abs(hash(_mapping_identity(mapping)))}"


def _resolve_repository_mapping(
    obs: RepositoryObservation,
    mappings: list[RepositoryMapping],
    ticker: str,
    as_of: datetime,
) -> tuple[RepositoryMapping | None, str | None]:
    active = [
        m for m in mappings if m.ticker == ticker and m.is_effective(as_of) and m.matches(obs)
    ]
    if not active:
        return None, "unmapped"
    exclusions = [m for m in active if not m.include]
    if exclusions:
        max_spec = max(_mapping_specificity(m) for m in exclusions)
        strongest = [m for m in exclusions if _mapping_specificity(m) == max_spec]
        if len({_mapping_identity(m) for m in strongest}) > 1:
            raise ValueError(f"ambiguous repository mapping for {obs.repo_key}")
        return strongest[0], "excluded"
    max_spec = max(_mapping_specificity(m) for m in active)
    strongest = [m for m in active if _mapping_specificity(m) == max_spec]
    if len({_mapping_identity(m) for m in strongest}) > 1:
        raise ValueError(f"ambiguous repository mapping for {obs.repo_key}")
    return strongest[0], None


def _status_exclusion(obs: RepositoryObservation, mapping: RepositoryMapping) -> str | None:
    if obs.is_archived:
        return "archived repository excluded"
    if obs.is_mirror:
        return "mirror repository excluded"
    if obs.is_fork and not obs.include_fork and mapping.is_first_party:
        return "fork repository excluded"
    return None


def _select_repositories(
    repo_obs: list[RepositoryObservation],
    mappings: list[RepositoryMapping],
    ticker: str,
    as_of: datetime,
    config: DeveloperEcosystemConfig,
) -> tuple[list[SelectedRepositoryObservation], list[str]]:
    diagnostics: list[str] = []
    selected: list[SelectedRepositoryObservation] = []
    for obs in repo_obs:
        if obs.available_at > as_of:
            diagnostics.append(f"future observation excluded: {obs.repo_key}")
            continue
        if obs.company_ticker != ticker:
            raise ValueError("ticker mismatch in repository observation")
        mapping, reason = _resolve_repository_mapping(obs, mappings, ticker, as_of)
        if mapping is None:
            diagnostics.append(f"unmapped repository excluded: {obs.repo_key}")
            continue
        if reason == "excluded":
            diagnostics.append(f"explicitly excluded repository: {obs.repo_key}")
            continue
        status = _status_exclusion(obs, mapping)
        if status:
            diagnostics.append(f"{status}: {obs.repo_key}")
            continue
        role_weight = config.repository_role_weights.get(mapping.role, 0.5)
        selected.append(SelectedRepositoryObservation(obs, mapping, mapping.weight * role_weight))
    return selected, diagnostics


def _canonical_repo_window(
    selected: list[SelectedRepositoryObservation],
    as_of: datetime,
    days: int,
    prior: bool,
    config: DeveloperEcosystemConfig,
) -> list[SelectedRepositoryObservation]:
    start, end = _prior_bounds(as_of, days) if prior else _current_bounds(as_of, days)
    grouped: dict[tuple[str, str, int], list[SelectedRepositoryObservation]] = defaultdict(list)
    for item in selected:
        obs = item.observation
        if obs.available_at > as_of:
            continue
        if not _matches_duration(obs, days, config.observation_window_tolerance_days):
            continue
        if not _in_bounds(obs, start, end, config.observation_window_tolerance_days):
            continue
        grouped[(obs.provider, obs.repository_id, days)].append(item)
    canonical: list[SelectedRepositoryObservation] = []
    for items in grouped.values():
        canonical.append(
            sorted(
                items,
                key=lambda item: (
                    item.observation.observation_window_end,
                    item.observation.available_at,
                    item.observation.provider_record_id,
                ),
                reverse=True,
            )[0]
        )
    return canonical


def _package_matches_mapping(pkg: PackageObservation, mapping: RepositoryMapping) -> bool:
    return pkg.package_name in mapping.package_names


def _select_packages(
    package_obs: list[PackageObservation],
    mappings: list[RepositoryMapping],
    selected_repo_ids: set[str],
    ticker: str,
    as_of: datetime,
) -> tuple[list[SelectedPackageObservation], list[str]]:
    diagnostics: list[str] = []
    active_mappings = [
        m for m in mappings if m.ticker == ticker and m.include and m.is_effective(as_of)
    ]
    selected: list[SelectedPackageObservation] = []
    for pkg in package_obs:
        if pkg.available_at > as_of:
            diagnostics.append(f"future package observation excluded: {pkg.package_name}")
            continue
        if pkg.company_ticker != ticker:
            diagnostics.append(f"ticker-mismatched package excluded: {pkg.package_name}")
            continue
        matches = [m for m in active_mappings if _package_matches_mapping(pkg, m)]
        if not matches:
            diagnostics.append(f"unmapped package excluded: {pkg.ecosystem}:{pkg.package_name}")
            continue
        identities = {_mapping_identity(m) for m in matches}
        if len(identities) > 1:
            raise ValueError(f"ambiguous package mapping for {pkg.ecosystem}:{pkg.package_name}")
        if pkg.repository_id and pkg.repository_id not in selected_repo_ids:
            diagnostics.append(
                f"package linked to excluded or wrong repository: {pkg.package_name}"
            )
            continue
        selected.append(SelectedPackageObservation(pkg, matches[0]))
    return selected, diagnostics


def _canonical_package_window(
    selected: list[SelectedPackageObservation],
    as_of: datetime,
    days: int,
    prior: bool,
    config: DeveloperEcosystemConfig,
) -> list[SelectedPackageObservation]:
    start, end = _prior_bounds(as_of, days) if prior else _current_bounds(as_of, days)
    grouped: dict[tuple[str, str, str, int], list[SelectedPackageObservation]] = defaultdict(list)
    for item in selected:
        obs = item.observation
        if not _matches_duration(obs, days, config.observation_window_tolerance_days):
            continue
        if not _in_bounds(obs, start, end, config.observation_window_tolerance_days):
            continue
        grouped[(obs.provider, obs.ecosystem.value, obs.package_name, days)].append(item)
    return [
        sorted(
            items,
            key=lambda item: (
                item.observation.observation_window_end,
                item.observation.available_at,
                item.observation.provider_record_id,
            ),
            reverse=True,
        )[0]
        for items in grouped.values()
    ]


def _noise_share(obs: RepositoryObservation) -> float:
    if obs.imported_history:
        return 1.0
    return max(
        0.0,
        min(
            1.0,
            obs.generated_activity_share
            + obs.mass_formatting_share
            + obs.lockfile_only_share
            + obs.dependency_update_share
            + obs.bot_activity_share,
        ),
    )


def _weighted_sum(
    items: list[SelectedRepositoryObservation], attr: str, adjusted: bool = False
) -> float:
    total = 0.0
    for item in items:
        value = float(getattr(item.observation, attr)) * item.weight
        total += value * (1 - _noise_share(item.observation)) if adjusted else value
    return total


def _package_sum(items: list[SelectedPackageObservation], attr: str) -> float | None:
    total = 0.0
    found = False
    for item in items:
        value = getattr(item.observation, attr)
        if value is None:
            continue
        total += float(value)
        found = True
    return total if found else None


def _classify_bot(contributor: ContributorActivity, config: DeveloperEcosystemConfig) -> bool:
    login = contributor.login.lower().strip()
    allow = {item.lower() for item in config.bot_filtering.allowlist_logins}
    deny = {item.lower() for item in config.bot_filtering.denylist_logins}
    if login in allow:
        return False
    if login in deny:
        return True
    if contributor.is_bot:
        return True
    return any(
        re.search(pattern.lower(), login) for pattern in config.bot_filtering.bot_login_patterns
    )


def _repo_provenance_key(obs: RepositoryObservation) -> str:
    return f"developer:repository:{obs.provider}:{obs.repository_id}:{obs.provider_record_id}"


def _package_provenance_key(obs: PackageObservation) -> str:
    return f"developer:package:{obs.provider}:{obs.ecosystem.value}:{obs.package_name}:{obs.provider_record_id}"


def _provenance_payload(obs: RepositoryObservation | PackageObservation) -> str:
    if isinstance(obs, RepositoryObservation):
        return json.dumps(
            {
                "provider": obs.provider,
                "provider_record_id": obs.provider_record_id,
                "source": obs.source_provenance,
                "available_at": obs.available_at.isoformat(),
                "observation_window_start": obs.observation_window_start.isoformat(),
                "observation_window_end": obs.observation_window_end.isoformat(),
                "repository_id": obs.repository_id,
                "owner": obs.owner,
                "name": obs.name,
            },
            sort_keys=True,
        )
    return json.dumps(
        {
            "provider": obs.provider,
            "provider_record_id": obs.provider_record_id,
            "source": obs.source_identifier,
            "available_at": obs.available_at.isoformat(),
            "observation_window_start": obs.observation_window_start.isoformat(),
            "observation_window_end": obs.observation_window_end.isoformat(),
            "ecosystem": obs.ecosystem.value,
            "package_name": obs.package_name,
            "repository_id": obs.repository_id,
        },
        sort_keys=True,
    )


def _agreement(first: object, second: object) -> float:
    if not isinstance(first, (float, int)) or not isinstance(second, (float, int)):
        return 0.0
    return 1.0 if first * second > 0 else 0.25


def calculate_developer_ecosystem_features(
    repo_observations: list[RepositoryObservation],
    mappings: list[RepositoryMapping],
    ticker: str,
    as_of: datetime,
    package_observations: list[PackageObservation] | None = None,
    windows: tuple[int, ...] | None = None,
    config: DeveloperEcosystemConfig | None = None,
) -> DeveloperEcosystemFeatures:
    config = config or DeveloperEcosystemConfig()
    window_values = tuple(windows or tuple(config.lookback_windows_days))
    as_of = normalize_utc(as_of)
    ticker = ticker.upper().strip()
    selected, diagnostics = _select_repositories(repo_observations, mappings, ticker, as_of, config)
    selected_repo_ids = {item.observation.repository_id for item in selected}
    packages, package_diags = _select_packages(
        package_observations or [], mappings, selected_repo_ids, ticker, as_of
    )
    diagnostics.extend(package_diags)
    features: dict[str, float | int | str | bool | None] = {}
    provenance: dict[str, str] = {}
    source_as_of: dict[str, datetime] = {}
    current90: list[SelectedRepositoryObservation] = []
    current90_packages: list[SelectedPackageObservation] = []
    used_repo: list[SelectedRepositoryObservation] = []
    used_pkg: list[SelectedPackageObservation] = []
    for days in window_values:
        cur = _canonical_repo_window(selected, as_of, days, False, config)
        prior = _canonical_repo_window(selected, as_of, days, True, config)
        pkg_cur = _canonical_package_window(packages, as_of, days, False, config)
        pkg_prior = _canonical_package_window(packages, as_of, days, True, config)
        if days == 90:
            current90 = cur
            current90_packages = pkg_cur
        if cur:
            used_repo.extend(cur + prior)
            active = _weighted_sum(cur, "active_contributor_count")
            prior_active = _weighted_sum(prior, "active_contributor_count") if prior else None
            external = _weighted_sum(cur, "external_contributor_count")
            prior_external = _weighted_sum(prior, "external_contributor_count") if prior else None
            commits_raw = _weighted_sum(cur, "commit_count")
            commits_adj = _weighted_sum(cur, "commit_count", adjusted=True)
            releases = sum(o.observation.releases.release_count * o.weight for o in cur)
            prior_releases = (
                sum(o.observation.releases.release_count * o.weight for o in prior)
                if prior
                else None
            )
            issues_open = sum(o.observation.issues.opened_count * o.weight for o in cur)
            issues_closed = sum(o.observation.issues.closed_count * o.weight for o in cur)
            prior_backlog = (
                sum(o.observation.issues.open_count * o.weight for o in prior) if prior else None
            )
            backlog = sum(o.observation.issues.open_count * o.weight for o in cur)
            stars = sum(o.observation.popularity.stargazer_count * o.weight for o in cur)
            prior_stars = (
                sum(o.observation.popularity.stargazer_count * o.weight for o in prior)
                if prior
                else None
            )
            forks = sum(o.observation.popularity.fork_count * o.weight for o in cur)
            prior_forks = (
                sum(o.observation.popularity.fork_count * o.weight for o in prior)
                if prior
                else None
            )
            features.update(
                {
                    f"developer_active_contributors_{days}d": active,
                    f"developer_active_contributor_growth_{days}d": safe_relative_change(
                        active, prior_active
                    ),
                    f"developer_external_contributor_growth_{days}d": safe_relative_change(
                        external, prior_external
                    ),
                    f"developer_commit_velocity_{days}d": commits_adj / days,
                    f"developer_commit_velocity_{days}d_raw": commits_raw / days,
                    f"developer_commit_velocity_{days}d_adjusted": commits_adj / days,
                    f"developer_release_velocity_{days}d": releases / days,
                    f"developer_release_growth_{days}d": safe_relative_change(
                        releases, prior_releases
                    ),
                    f"developer_issue_backlog_growth_{days}d": safe_relative_change(
                        backlog, prior_backlog
                    ),
                    f"developer_issue_open_velocity_{days}d": issues_open / days,
                    f"developer_issue_close_velocity_{days}d": issues_closed / days,
                    f"developer_stargazer_growth_{days}d": safe_relative_change(stars, prior_stars),
                    f"developer_fork_growth_{days}d": safe_relative_change(forks, prior_forks),
                }
            )
        else:
            diagnostics.append(f"missing current repository window: {days}d")
            for key in (
                "active_contributors",
                "active_contributor_growth",
                "external_contributor_growth",
                "commit_velocity",
                "commit_velocity_raw",
                "commit_velocity_adjusted",
                "release_velocity",
                "release_growth",
                "issue_backlog_growth",
                "issue_open_velocity",
                "issue_close_velocity",
                "stargazer_growth",
                "fork_growth",
            ):
                features[f"developer_{key}_{days}d"] = None
        if pkg_cur:
            used_pkg.extend(pkg_cur + pkg_prior)
            downloads = _package_sum(pkg_cur, "download_count")
            prior_downloads = _package_sum(pkg_prior, "download_count") if pkg_prior else None
            features[f"developer_package_download_growth_{days}d"] = safe_relative_change(
                downloads, prior_downloads
            )
        else:
            diagnostics.append(f"missing current package window: {days}d")
            features[f"developer_package_download_growth_{days}d"] = None
    contrib_counts: defaultdict[str, float] = defaultdict(float)
    bot = 0.0
    nonbot = 0.0
    provider_bot_weighted = 0.0
    provider_total_weighted = 0.0
    for item in current90:
        obs = item.observation
        provider_bot_weighted += obs.bot_activity_share * obs.commit_count * item.weight
        provider_total_weighted += obs.commit_count * item.weight
        for c in obs.contributors:
            amount = c.commit_count * item.weight
            if _classify_bot(c, config):
                bot += amount
            else:
                contrib_counts[c.login] += amount
                nonbot += amount
    contributor_bot_share = bot / (bot + nonbot) if bot + nonbot else 0.0
    provider_bot_share = (
        provider_bot_weighted / provider_total_weighted if provider_total_weighted else 0.0
    )
    bot_share = max(contributor_bot_share, provider_bot_share)
    shares = sorted((v / nonbot for v in contrib_counts.values()), reverse=True) if nonbot else []
    top1 = shares[0] if shares else None
    top5 = sum(shares[:5]) if shares else None
    breadth = len(
        {
            item.observation.repository_id
            for item in current90
            if item.observation.commit_count
            + item.observation.releases.release_count
            + item.observation.pull_requests.merged_count
            >= config.activity_thresholds.meaningful_activity_events_90d
        }
    )
    contributor_growth = features.get("developer_active_contributor_growth_90d")
    external_growth = features.get("developer_external_contributor_growth_90d")
    package_growth = features.get("developer_package_download_growth_90d")
    backlog_growth = features.get("developer_issue_backlog_growth_90d")
    fork_growth = features.get("developer_fork_growth_90d")
    release_growth = features.get("developer_release_growth_90d")
    release_velocity = features.get("developer_release_velocity_90d")
    star_growth = features.get("developer_stargazer_growth_90d")
    weights = config.b1_scoring_weights
    quality_score = 0.0
    for value, weight in (
        (contributor_growth, weights.contributor_growth * 100),
        (external_growth, weights.external_contributor_growth * 100),
        (package_growth, weights.package_download_growth * 100),
    ):
        if isinstance(value, (float, int)):
            quality_score += max(-1, min(1, value)) * weight
    if isinstance(backlog_growth, (float, int)):
        quality_score -= max(-1, min(1, backlog_growth)) * weights.maintenance_backlog_penalty * 100
    if top1 is not None:
        warning = config.contributor_concentration_penalties.top_one_warning_share
        quality_score -= max(0.0, top1 - warning) * 30
    quality_score += (
        min(weights.breadth_bonus_cap * 100, breadth * 3) - bot_share * weights.bot_penalty * 100
    )
    corroborated = any(
        isinstance(v, (float, int))
        and v > config.star_spike_diagnostics.corroborating_growth_threshold
        for v in (
            contributor_growth,
            external_growth,
            fork_growth,
            release_growth,
            release_velocity,
            package_growth,
        )
    )
    if (
        isinstance(star_growth, (float, int))
        and star_growth > config.star_spike_diagnostics.spike_growth_threshold
        and not corroborated
    ):
        diagnostics.append("star spike lacks contributor, fork, release, or package corroboration")
        quality_score -= 12
    features.update(
        {
            "developer_contributor_concentration": top1,
            "developer_top_one_contributor_share": top1,
            "developer_top_five_contributor_share": top5,
            "developer_bus_factor_proxy": 1 / top1 if top1 else None,
            "developer_bot_activity_share": bot_share,
            "developer_provider_bot_activity_share": provider_bot_share,
            "developer_contributor_bot_activity_share": contributor_bot_share,
            "developer_ecosystem_breadth": breadth,
            "developer_activity_concentration": top1,
            "developer_momentum_quality_score": max(-100.0, min(100.0, quality_score))
            if current90
            else None,
            "developer_cross_source_agreement": _agreement(package_growth, contributor_growth),
        }
    )
    for selected_item in used_repo:
        obs = selected_item.observation
        key = _repo_provenance_key(obs)
        provenance[key] = _provenance_payload(obs)
        source_as_of[key] = obs.available_at
        map_key = _mapping_key(selected_item.mapping)
        provenance[map_key] = _mapping_identity(selected_item.mapping)
        source_as_of[map_key] = selected_item.mapping.known_at
    for selected_package in used_pkg:
        pkg_obs = selected_package.observation
        key = _package_provenance_key(pkg_obs)
        provenance[key] = _provenance_payload(pkg_obs)
        source_as_of[key] = pkg_obs.available_at
    for item in current90:
        obs = item.observation
        if obs.imported_history:
            diagnostics.append("repository import history flagged")
        if _noise_share(obs) >= 0.5:
            diagnostics.append("generated or automated activity is elevated")
    current_repo_ids = {item.observation.repository_id for item in current90}
    repo_denominator = max(
        1,
        len(
            {
                (m.provider, m.organization or m.owner or "", m.name or m.repository_id or "*")
                for m in mappings
                if m.ticker == ticker and m.include and m.is_effective(as_of)
            }
        ),
    )
    completeness = min(1.0, len(current_repo_ids) / repo_denominator)
    used_freshness_values = [(as_of - ts).days for ts in source_as_of.values() if ts <= as_of]
    freshness = max(used_freshness_values) if used_freshness_values else 999
    freshness_score = max(0.0, 1 - freshness / config.staleness_thresholds.stale_days)
    has_current_package = bool(current90_packages)
    confidence = max(
        0.0,
        min(
            1.0,
            0.10
            + 0.25 * bool(current90)
            + 0.15 * min(1, breadth / 2)
            + 0.10 * has_current_package
            + 0.15 * completeness
            + 0.10 * freshness_score
            + 0.05
            * (
                float(features["developer_cross_source_agreement"] or 0)
                if has_current_package and current90
                else 0.0
            )
            - bot_share * 0.2,
        ),
    )
    quality = DeveloperDataQuality(
        score=confidence,
        data_freshness_days=freshness,
        coverage_percentage=completeness,
        completeness=completeness,
        diagnostics=tuple(diagnostics),
    )
    return DeveloperEcosystemFeatures(
        ticker=ticker,
        as_of=as_of,
        features=features,
        quality=quality,
        provenance=provenance,
        source_as_of=source_as_of,
        diagnostics=tuple(diagnostics),
        metadata={
            "windows": list(window_values),
            "mapped_repositories": len(current_repo_ids),
            "package_observations": len(current90_packages),
            "observation_grain_tolerance_days": config.observation_window_tolerance_days,
        },
    )


def attach_to_snapshot(
    snapshot: FeatureSnapshot, developer_features: DeveloperEcosystemFeatures
) -> FeatureSnapshot:
    if snapshot.ticker != developer_features.ticker:
        raise ValueError("ticker mismatch while attaching developer features")
    if (
        any(ts > snapshot.as_of for ts in developer_features.source_as_of.values())
        or developer_features.as_of > snapshot.as_of
    ):
        raise ValueError("future developer evidence rejected")
    return snapshot.model_copy(
        update={
            "values": {
                **snapshot.values,
                **developer_features.features,
                "developer_data_quality_score": developer_features.quality.score,
            },
            "sources": {**snapshot.sources, **developer_features.provenance},
            "source_as_of": {**snapshot.source_as_of, **developer_features.source_as_of},
        }
    )
