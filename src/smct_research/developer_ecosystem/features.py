from __future__ import annotations

import hashlib
import json
import math
import re
from collections import defaultdict
from collections.abc import Callable
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
    canonical_mapping_identity,
    canonical_package_series_identity,
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


def _interval_available_by_as_of(
    obs: RepositoryObservation | PackageObservation, as_of: datetime
) -> bool:
    return obs.observation_window_end <= as_of and obs.available_at <= as_of


def _mapping_specificity(mapping: RepositoryMapping) -> int:
    if mapping.repository_id:
        return 3
    if mapping.owner and mapping.name:
        return 2
    if mapping.organization:
        return 1
    return 0


def _mapping_identity(mapping: RepositoryMapping) -> str:
    return canonical_mapping_identity(mapping)


def _mapping_key(mapping: RepositoryMapping) -> str:
    return f"developer:mapping:{mapping.provider}:{hashlib.sha256(_mapping_identity(mapping).encode()).hexdigest()}"


def _mapping_covers_interval(
    mapping: RepositoryMapping, obs: RepositoryObservation | PackageObservation
) -> bool:
    return mapping.effective_from <= obs.observation_window_start and (
        mapping.effective_to is None or mapping.effective_to >= obs.observation_window_end
    )


def _resolve_repository_mapping(
    obs: RepositoryObservation,
    mappings: list[RepositoryMapping],
    ticker: str,
    as_of: datetime,
) -> tuple[RepositoryMapping | None, str | None]:
    active = [
        m
        for m in mappings
        if m.ticker == ticker
        and m.known_at <= as_of
        and _mapping_covers_interval(m, obs)
        and m.matches(obs)
    ]
    if not active:
        return None, "unmapped"
    max_spec = max(_mapping_specificity(m) for m in active)
    strongest_by_identity = {
        _mapping_identity(m): m for m in active if _mapping_specificity(m) == max_spec
    }
    strongest = list(strongest_by_identity.values())
    includes = [m for m in strongest if m.include]
    exclusions = [m for m in strongest if not m.include]
    if len(includes) > 1 or len(exclusions) > 1:
        raise ValueError(f"ambiguous repository mapping for {obs.repo_key}")
    if includes and exclusions:
        return exclusions[0], "excluded"
    if exclusions:
        return exclusions[0], "excluded"
    return includes[0], None


def _status_exclusion(obs: RepositoryObservation, mapping: RepositoryMapping) -> str | None:
    if obs.is_archived:
        return "archived repository excluded"
    if obs.is_mirror:
        return "mirror repository excluded"
    if obs.is_fork and not mapping.include_forks:
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
        if obs.observation_window_end > as_of:
            diagnostics.append(f"future-ending observation excluded: {obs.repo_key}")
            continue
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
        if obs.is_fork and mapping.include_forks:
            diagnostics.append(f"fork repository included by explicit mapping: {obs.repo_key}")
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
        if not _interval_available_by_as_of(obs, as_of):
            continue
        if not _matches_duration(obs, days, config.observation_window_tolerance_days):
            continue
        if not _in_bounds(obs, start, end, config.observation_window_tolerance_days):
            continue
        grouped[(obs.provider, obs.repository_id, days)].append(item)
    repo_to_providers: dict[str, set[str]] = defaultdict(set)
    for provider, repository_id, _days in grouped:
        repo_to_providers[repository_id].add(provider)
    ambiguous = [repo for repo, providers in repo_to_providers.items() if len(providers) > 1]
    if ambiguous:
        raise ValueError(
            f"ambiguous multi-provider repository series: {', '.join(sorted(ambiguous))}"
        )
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


def _normalize_package_name(pkg: PackageObservation) -> str:
    return pkg.package_name.strip().lower()


def _package_matches_mapping(pkg: PackageObservation, mapping: RepositoryMapping) -> bool:
    normalized = _normalize_package_name(pkg)
    selectors = [
        selector
        for selector in mapping.package_selectors
        if selector.ecosystem == pkg.ecosystem
        and selector.package_name == normalized
        and (selector.provider is None or selector.provider == pkg.provider)
        and (selector.repository_id is None or selector.repository_id == pkg.repository_id)
        and (
            selector.repository_owner is None
            or selector.repository_owner.lower() == (pkg.repository_owner or "").lower()
        )
        and (
            selector.repository_name is None
            or selector.repository_name.lower() == (pkg.repository_name or "").lower()
        )
    ]
    return bool(selectors)


def _same_observation_interval(
    first: RepositoryObservation | PackageObservation,
    second: RepositoryObservation | PackageObservation,
    tolerance_days: int,
) -> bool:
    tolerance = timedelta(days=tolerance_days)
    return (
        abs(first.observation_window_start - second.observation_window_start) <= tolerance
        and abs(first.observation_window_end - second.observation_window_end) <= tolerance
    )


def _resolve_linked_package_repository(
    pkg: PackageObservation,
    package_mapping: RepositoryMapping,
    repo_observations: list[RepositoryObservation],
    mappings: list[RepositoryMapping],
    ticker: str,
    as_of: datetime,
    config: DeveloperEcosystemConfig,
) -> str | None:
    if not (pkg.repository_id or (pkg.repository_owner and pkg.repository_name)):
        return None
    repository_provider = package_mapping.provider
    interval_candidates = [
        repo
        for repo in repo_observations
        if repo.company_ticker == ticker
        and repo.provider == repository_provider
        and _interval_available_by_as_of(repo, as_of)
        and _same_observation_interval(repo, pkg, config.observation_window_tolerance_days)
    ]
    id_candidates = {repo.repository_id for repo in interval_candidates}
    name_candidates = {
        (repo.owner.lower(), repo.name.lower(), repo.repository_id) for repo in interval_candidates
    }
    if pkg.repository_id:
        interval_candidates = [
            repo for repo in interval_candidates if repo.repository_id == pkg.repository_id
        ]
    if pkg.repository_owner and pkg.repository_name:
        wanted_name = (pkg.repository_owner.lower(), pkg.repository_name.lower())
        interval_candidates = [
            repo
            for repo in interval_candidates
            if (repo.owner.lower(), repo.name.lower()) == wanted_name
        ]
        if (
            pkg.repository_id
            and pkg.repository_id in id_candidates
            and any((owner, name) == wanted_name for owner, name, _ in name_candidates)
            and not interval_candidates
        ):
            return (
                "linked package evidence rejected due to contradictory repository identity: "
                f"{pkg.package_name}"
            )
    for repo in interval_candidates:
        mapping, reason = _resolve_repository_mapping(repo, mappings, ticker, as_of)
        if mapping is None or reason == "excluded":
            continue
        if _status_exclusion(repo, mapping):
            continue
        return None
    return (
        "linked package evidence rejected due to excluded or unmapped repository for package "
        f"interval: {pkg.package_name}"
    )


def _select_packages(
    package_obs: list[PackageObservation],
    repo_observations: list[RepositoryObservation],
    mappings: list[RepositoryMapping],
    ticker: str,
    as_of: datetime,
    config: DeveloperEcosystemConfig,
) -> tuple[list[SelectedPackageObservation], list[str]]:
    diagnostics: list[str] = []
    active_mappings = [
        m for m in mappings if m.ticker == ticker and m.include and m.known_at <= as_of
    ]
    selected: list[SelectedPackageObservation] = []
    for pkg in package_obs:
        if pkg.observation_window_end > as_of:
            diagnostics.append(f"future-ending package observation excluded: {pkg.package_name}")
            continue
        if pkg.available_at > as_of:
            diagnostics.append(f"future package observation excluded: {pkg.package_name}")
            continue
        if pkg.company_ticker != ticker:
            diagnostics.append(f"ticker-mismatched package excluded: {pkg.package_name}")
            continue
        matches = [
            m
            for m in active_mappings
            if _mapping_covers_interval(m, pkg) and _package_matches_mapping(pkg, m)
        ]
        if not matches:
            diagnostics.append(f"unmapped package excluded: {pkg.ecosystem}:{pkg.package_name}")
            continue
        identities = {_mapping_identity(m) for m in matches}
        if len(identities) > 1:
            raise ValueError(f"ambiguous package mapping for {pkg.ecosystem}:{pkg.package_name}")
        linkage_diagnostic = _resolve_linked_package_repository(
            pkg, matches[0], repo_observations, mappings, ticker, as_of, config
        )
        if linkage_diagnostic is not None:
            diagnostics.append(linkage_diagnostic)
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
    grouped: dict[tuple[str, ...], list[SelectedPackageObservation]] = defaultdict(list)
    for item in selected:
        obs = item.observation
        if not _interval_available_by_as_of(obs, as_of):
            continue
        if not _matches_duration(obs, days, config.observation_window_tolerance_days):
            continue
        if not _in_bounds(obs, start, end, config.observation_window_tolerance_days):
            continue
        grouped[
            canonical_package_series_identity(obs, item.mapping.provider, grain_days=days)
        ].append(item)
    package_to_providers: dict[tuple[str, ...], set[str]] = defaultdict(set)
    for key in grouped:
        package_to_providers[key[1:]].add(key[0])
    ambiguous = [key for key, providers in package_to_providers.items() if len(providers) > 1]
    if ambiguous:
        label = ", ".join(":".join(key) for key in sorted(ambiguous))
        raise ValueError(f"ambiguous multi-provider package series: {label}")
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


def _package_provenance_key(obs: PackageObservation, repository_provider: str | None) -> str:
    package_identity = ":".join(canonical_package_series_identity(obs, repository_provider))
    return f"developer:package:{package_identity}:{obs.provider_record_id}"


def _provenance_payload(
    obs: RepositoryObservation | PackageObservation, repository_provider: str | None = None
) -> str:
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
            "repository_provider": repository_provider,
            "repository_id": obs.repository_id,
            "repository_owner": obs.repository_owner,
            "repository_name": obs.repository_name,
        },
        sort_keys=True,
    )


def _agreement(first: object, second: object) -> float:
    if not isinstance(first, (float, int)) or not isinstance(second, (float, int)):
        return 0.0
    return 1.0 if first * second > 0 else 0.25


def _repo_series_key(item: SelectedRepositoryObservation, days: int) -> tuple[str, str, int]:
    obs = item.observation
    return obs.provider, obs.repository_id, days


def _package_series_key(item: SelectedPackageObservation, days: int) -> tuple[str, ...]:
    return canonical_package_series_identity(
        item.observation, item.mapping.provider, grain_days=days
    )


def _matched_repo_pairs(
    cur: list[SelectedRepositoryObservation], prior: list[SelectedRepositoryObservation], days: int
) -> tuple[list[SelectedRepositoryObservation], list[SelectedRepositoryObservation], list[str]]:
    cur_by_key = {_repo_series_key(item, days): item for item in cur}
    prior_by_key = {_repo_series_key(item, days): item for item in prior}
    matched = sorted(set(cur_by_key) & set(prior_by_key))
    diagnostics = [
        f"unmatched-current repository series: {key[0]}:{key[1]}:{days}d"
        for key in sorted(set(cur_by_key) - set(prior_by_key))
    ]
    diagnostics.extend(
        f"unmatched-prior repository series: {key[0]}:{key[1]}:{days}d"
        for key in sorted(set(prior_by_key) - set(cur_by_key))
    )
    return [cur_by_key[key] for key in matched], [prior_by_key[key] for key in matched], diagnostics


def _matched_package_pairs(
    cur: list[SelectedPackageObservation], prior: list[SelectedPackageObservation], days: int
) -> tuple[list[SelectedPackageObservation], list[SelectedPackageObservation], list[str]]:
    cur_by_key = {_package_series_key(item, days): item for item in cur}
    prior_by_key = {_package_series_key(item, days): item for item in prior}
    matched = sorted(set(cur_by_key) & set(prior_by_key))
    diagnostics = [
        f"unmatched-current package series: {key[0]}:{key[1]}:{key[2]}:{days}d"
        for key in sorted(set(cur_by_key) - set(prior_by_key))
    ]
    diagnostics.extend(
        f"unmatched-prior package series: {key[0]}:{key[1]}:{key[2]}:{days}d"
        for key in sorted(set(prior_by_key) - set(cur_by_key))
    )
    return [cur_by_key[key] for key in matched], [prior_by_key[key] for key in matched], diagnostics


def _matched_repo_growth(
    current: list[SelectedRepositoryObservation],
    prior: list[SelectedRepositoryObservation],
    days: int,
    value: Callable[[SelectedRepositoryObservation], float],
) -> float | None:
    matched_current, matched_prior, _ = _matched_repo_pairs(current, prior, days)
    if not matched_current or not matched_prior:
        return None
    return safe_relative_change(
        sum(value(item) for item in matched_current), sum(value(item) for item in matched_prior)
    )


def _matched_package_growth(
    current: list[SelectedPackageObservation],
    prior: list[SelectedPackageObservation],
    days: int,
    attr: str,
) -> float | None:
    matched_current, matched_prior, _ = _matched_package_pairs(current, prior, days)
    current_value = _package_sum(matched_current, attr)
    prior_value = _package_sum(matched_prior, attr)
    return safe_relative_change(current_value, prior_value)


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
    packages, package_diags = _select_packages(
        package_observations or [],
        repo_observations,
        mappings,
        ticker,
        as_of,
        config,
    )
    diagnostics.extend(package_diags)
    features: dict[str, float | int | str | bool | None] = {}
    provenance: dict[str, str] = {}
    source_as_of: dict[str, datetime] = {}
    current90: list[SelectedRepositoryObservation] = []
    current90_packages: list[SelectedPackageObservation] = []
    matched_current_90: list[SelectedRepositoryObservation] = []
    matched_prior_90: list[SelectedRepositoryObservation] = []
    matched_package_current_90: list[SelectedPackageObservation] = []
    matched_package_prior_90: list[SelectedPackageObservation] = []
    used_repo: list[SelectedRepositoryObservation] = []
    used_pkg: list[SelectedPackageObservation] = []
    for days in window_values:
        cur = _canonical_repo_window(selected, as_of, days, False, config)
        prior = _canonical_repo_window(selected, as_of, days, True, config)
        pkg_cur = _canonical_package_window(packages, as_of, days, False, config)
        pkg_prior = _canonical_package_window(packages, as_of, days, True, config)
        matched_cur, matched_prior, match_diags = _matched_repo_pairs(cur, prior, days)
        matched_pkg_cur, matched_pkg_prior, pkg_match_diags = _matched_package_pairs(
            pkg_cur, pkg_prior, days
        )
        diagnostics.extend(match_diags)
        diagnostics.extend(pkg_match_diags)
        if days == 90:
            current90 = cur
            current90_packages = pkg_cur
            matched_current_90 = matched_cur
            matched_prior_90 = matched_prior
            matched_package_current_90 = matched_pkg_cur
            matched_package_prior_90 = matched_pkg_prior
        if cur:
            used_repo.extend(cur + matched_prior)
            active = _weighted_sum(cur, "active_contributor_count")
            prior_active = (
                _weighted_sum(matched_prior, "active_contributor_count") if matched_prior else None
            )
            prior_external = (
                _weighted_sum(matched_prior, "external_contributor_count")
                if matched_prior
                else None
            )
            commits_raw = _weighted_sum(cur, "commit_count")
            commits_adj = _weighted_sum(cur, "commit_count", adjusted=True)
            releases = sum(o.observation.releases.release_count * o.weight for o in cur)
            prior_releases = (
                sum(o.observation.releases.release_count * o.weight for o in matched_prior)
                if matched_prior
                else None
            )
            issues_open = sum(o.observation.issues.opened_count * o.weight for o in cur)
            issues_closed = sum(o.observation.issues.closed_count * o.weight for o in cur)
            prior_backlog = (
                sum(o.observation.issues.open_count * o.weight for o in matched_prior)
                if matched_prior
                else None
            )
            prior_stars = (
                sum(o.observation.popularity.stargazer_count * o.weight for o in matched_prior)
                if matched_prior
                else None
            )
            prior_forks = (
                sum(o.observation.popularity.fork_count * o.weight for o in matched_prior)
                if matched_prior
                else None
            )
            features.update(
                {
                    f"developer_active_contributors_{days}d": active,
                    f"developer_active_contributor_growth_{days}d": safe_relative_change(
                        _weighted_sum(matched_cur, "active_contributor_count")
                        if matched_cur
                        else None,
                        prior_active,
                    ),
                    f"developer_external_contributor_growth_{days}d": safe_relative_change(
                        _weighted_sum(matched_cur, "external_contributor_count")
                        if matched_cur
                        else None,
                        prior_external,
                    ),
                    f"developer_commit_velocity_{days}d": commits_adj / days,
                    f"developer_commit_velocity_{days}d_raw": commits_raw / days,
                    f"developer_commit_velocity_{days}d_adjusted": commits_adj / days,
                    f"developer_release_velocity_{days}d": releases / days,
                    f"developer_release_growth_{days}d": safe_relative_change(
                        sum(o.observation.releases.release_count * o.weight for o in matched_cur)
                        if matched_cur
                        else None,
                        prior_releases,
                    ),
                    f"developer_issue_backlog_growth_{days}d": safe_relative_change(
                        sum(o.observation.issues.open_count * o.weight for o in matched_cur)
                        if matched_cur
                        else None,
                        prior_backlog,
                    ),
                    f"developer_issue_open_velocity_{days}d": issues_open / days,
                    f"developer_issue_close_velocity_{days}d": issues_closed / days,
                    f"developer_stargazer_growth_{days}d": safe_relative_change(
                        sum(
                            o.observation.popularity.stargazer_count * o.weight for o in matched_cur
                        )
                        if matched_cur
                        else None,
                        prior_stars,
                    ),
                    f"developer_fork_growth_{days}d": safe_relative_change(
                        sum(o.observation.popularity.fork_count * o.weight for o in matched_cur)
                        if matched_cur
                        else None,
                        prior_forks,
                    ),
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
            used_pkg.extend(pkg_cur + matched_pkg_prior)
            downloads = _package_sum(matched_pkg_cur, "download_count")
            prior_downloads = (
                _package_sum(matched_pkg_prior, "download_count") if matched_pkg_prior else None
            )
            current_downloads_all = _package_sum(pkg_cur, "download_count")
            dependents = _package_sum(matched_pkg_cur, "dependent_package_count")
            prior_dependents = (
                _package_sum(matched_pkg_prior, "dependent_package_count")
                if matched_pkg_prior
                else None
            )
            features[f"developer_package_download_growth_{days}d"] = safe_relative_change(
                downloads, prior_downloads
            )
            features[f"developer_dependent_package_growth_{days}d"] = safe_relative_change(
                dependents, prior_dependents
            )
            features[f"developer_package_downloads_{days}d"] = current_downloads_all
        else:
            diagnostics.append(f"missing current package window: {days}d")
            features[f"developer_package_download_growth_{days}d"] = None
            features[f"developer_dependent_package_growth_{days}d"] = None
            features[f"developer_package_downloads_{days}d"] = None
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
    dependent_growth = features.get("developer_dependent_package_growth_90d")
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
        (release_growth, weights.release_growth * 100),
        (fork_growth, weights.fork_growth * 100),
        (package_growth, config.package_adoption_weights.download_growth * 100),
        (dependent_growth, config.package_adoption_weights.dependent_package_growth * 100),
    ):
        if isinstance(value, (float, int)):
            quality_score += max(-1, min(1, value)) * weight
    if isinstance(backlog_growth, (float, int)):
        quality_score -= max(-1, min(1, backlog_growth)) * weights.maintenance_backlog_penalty * 100
    if top1 is not None:
        warning = config.contributor_concentration_penalties.top_one_warning_share
        high_risk = config.contributor_concentration_penalties.top_one_high_risk_share
        quality_score -= max(0.0, top1 - warning) * 30
        if top1 >= high_risk:
            diagnostics.append("high contributor concentration risk")
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
        key = _package_provenance_key(pkg_obs, selected_package.mapping.provider)
        provenance[key] = _provenance_payload(pkg_obs, selected_package.mapping.provider)
        source_as_of[key] = pkg_obs.available_at
        map_key = _mapping_key(selected_package.mapping)
        provenance[map_key] = _mapping_identity(selected_package.mapping)
        source_as_of[map_key] = selected_package.mapping.known_at
    for item in current90:
        obs = item.observation
        if obs.imported_history:
            diagnostics.append("repository import history flagged")
        if _noise_share(obs) >= 0.5:
            diagnostics.append("generated or automated activity is elevated")
    primary_window = 90
    eligible_primary = _canonical_repo_window(
        selected, as_of, primary_window, False, config
    ) + _canonical_repo_window(selected, as_of, primary_window, True, config)
    eligible_repo_ids = {item.observation.repository_id for item in eligible_primary}
    current_repo_ids = {item.observation.repository_id for item in current90}
    matched_repo_ids = {item.observation.repository_id for item in matched_current_90}
    matched_prior_repo_ids = {item.observation.repository_id for item in matched_prior_90}
    matched_package_ids = {
        canonical_package_series_identity(item.observation, item.mapping.provider)
        for item in matched_package_current_90
    }
    matched_prior_package_ids = {
        canonical_package_series_identity(item.observation, item.mapping.provider)
        for item in matched_package_prior_90
    }
    completeness = min(1.0, len(current_repo_ids) / max(1, len(eligible_repo_ids)))
    used_freshness_values = [
        (as_of - source_as_of[key]).days
        for key in source_as_of
        if not key.startswith("developer:mapping:") and source_as_of[key] <= as_of
    ]
    freshness = max(used_freshness_values) if used_freshness_values else 999
    freshness_score = (
        1.0
        if freshness <= config.staleness_thresholds.fresh_days
        else max(
            0.0,
            1
            - (freshness - config.staleness_thresholds.fresh_days)
            / (config.staleness_thresholds.stale_days - config.staleness_thresholds.fresh_days),
        )
    )
    has_current_package = (
        bool(current90_packages) and features.get("developer_package_downloads_90d") is not None
    )
    directional_keys = (
        "developer_active_contributor_growth_90d",
        "developer_external_contributor_growth_90d",
        "developer_release_growth_90d",
        "developer_issue_backlog_growth_90d",
        "developer_fork_growth_90d",
        "developer_package_download_growth_90d",
        "developer_dependent_package_growth_90d",
    )
    has_directional_evidence = any(features.get(key) is not None for key in directional_keys)
    if not has_directional_evidence:
        diagnostics.append("insufficient matched directional history")
        features["developer_momentum_quality_score"] = None
    if len(current_repo_ids) < config.minimum_history_requirements.minimum_mapped_repositories:
        diagnostics.append("minimum mapped repository requirement not met")
        quality_score = 0.0
        features["developer_momentum_quality_score"] = None
    preferred = config.minimum_history_requirements.preferred_windows_days
    preferred_available = (
        preferred in window_values
        and features.get(f"developer_active_contributors_{preferred}d") is not None
    )
    if not preferred_available:
        diagnostics.append("preferred history window unavailable")
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
            - (0.10 if not preferred_available else 0.0)
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
            "eligible_repository_count": len(eligible_repo_ids),
            "current_repository_count": len(current_repo_ids),
            "matched_repository_count": len(matched_repo_ids),
            "matched_prior_repository_count": len(matched_prior_repo_ids),
            "matched_package_count": len(matched_package_ids),
            "matched_prior_package_count": len(matched_prior_package_ids),
            "repository_coverage_ratio": completeness,
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
