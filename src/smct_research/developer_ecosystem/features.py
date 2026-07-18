from __future__ import annotations

import math
from collections import defaultdict
from datetime import datetime

from smct_research.core.models import FeatureSnapshot, normalize_utc
from smct_research.developer_ecosystem.models import (
    DeveloperDataQuality,
    DeveloperEcosystemFeatures,
    PackageObservation,
    RepositoryMapping,
    RepositoryObservation,
    RepositoryRole,
)

ROLE_WEIGHTS = {
    RepositoryRole.CORE_PRODUCT: 1.0,
    RepositoryRole.SDK: 0.9,
    RepositoryRole.INFRASTRUCTURE: 0.75,
    RepositoryRole.INTEGRATION: 0.65,
    RepositoryRole.EXPERIMENTAL: 0.35,
    RepositoryRole.EXAMPLE: 0.25,
    RepositoryRole.DOCUMENTATION: 0.15,
    RepositoryRole.UNKNOWN: 0.5,
}


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


def _weighted_sum(items: list[tuple[RepositoryObservation, float]], attr: str) -> float:
    return sum(float(getattr(obs, attr)) * weight for obs, weight in items)


def _select(
    repo_obs: list[RepositoryObservation],
    mappings: list[RepositoryMapping],
    ticker: str,
    as_of: datetime,
) -> tuple[list[tuple[RepositoryObservation, float]], list[str]]:
    diagnostics: list[str] = []
    selected = []
    active_mappings = [m for m in mappings if m.ticker == ticker and m.is_effective(as_of)]
    for obs in repo_obs:
        if normalize_utc(obs.available_at) > as_of:
            diagnostics.append(f"future observation excluded: {obs.repo_key}")
            continue
        if obs.company_ticker != ticker:
            raise ValueError("ticker mismatch in repository observation")
        matches = [m for m in active_mappings if m.matches(obs)]
        if not matches:
            diagnostics.append(f"unmapped repository excluded: {obs.repo_key}")
            continue
        included = [m for m in matches if m.include]
        if len(included) > 1:
            raise ValueError(f"ambiguous repository mapping for {obs.repo_key}")
        if not included:
            diagnostics.append(f"explicitly excluded repository: {obs.repo_key}")
            continue
        mapping = included[0]
        weight = mapping.weight * ROLE_WEIGHTS.get(mapping.role, 0.5)
        selected.append((obs, weight))
    return selected, diagnostics


def _window(
    items: list[tuple[RepositoryObservation, float]],
    as_of: datetime,
    days: int,
    prior: bool = False,
) -> list[tuple[RepositoryObservation, float]]:
    end = as_of.timestamp() - (days * 86400 if prior else 0)
    start = end - days * 86400
    return [(o, w) for o, w in items if start <= o.observation_window_end.timestamp() <= end]


def _package_window(
    items: list[PackageObservation], as_of: datetime, days: int, prior: bool = False
) -> list[PackageObservation]:
    end = as_of.timestamp() - (days * 86400 if prior else 0)
    start = end - days * 86400
    return [
        o
        for o in items
        if start <= o.observation_window_end.timestamp() <= end and o.available_at <= as_of
    ]


def _agreement(first: object, second: object) -> float:
    if not isinstance(first, (float, int)):
        return 0.25
    if isinstance(second, (float, int)) and first * second > 0:
        return 1.0
    return 0.5


def calculate_developer_ecosystem_features(
    repo_observations: list[RepositoryObservation],
    mappings: list[RepositoryMapping],
    ticker: str,
    as_of: datetime,
    package_observations: list[PackageObservation] | None = None,
    windows: tuple[int, ...] = (30, 90, 180),
) -> DeveloperEcosystemFeatures:
    as_of = normalize_utc(as_of)
    ticker = ticker.upper().strip()
    selected, diagnostics = _select(repo_observations, mappings, ticker, as_of)
    packages = [
        p
        for p in (package_observations or [])
        if p.company_ticker == ticker and p.available_at <= as_of
    ]
    active_repo_ids = {o.repository_id for o, _ in selected}
    features: dict[str, float | int | str | bool | None] = {}
    provenance: dict[str, str] = {}
    source_as_of: dict[str, datetime] = {}
    current90: list[tuple[RepositoryObservation, float]] = []
    for days in windows:
        cur = _window(selected, as_of, days)
        prior = _window(selected, as_of, days, True)
        current90 = cur if days == 90 else current90
        active = _weighted_sum(cur, "active_contributor_count")
        prior_active = _weighted_sum(prior, "active_contributor_count") if prior else None
        external = _weighted_sum(cur, "external_contributor_count")
        prior_external = _weighted_sum(prior, "external_contributor_count") if prior else None
        commits = _weighted_sum(cur, "commit_count")
        releases = sum(o.releases.release_count * w for o, w in cur)
        issues_open = sum(o.issues.opened_count * w for o, w in cur)
        issues_closed = sum(o.issues.closed_count * w for o, w in cur)
        prior_backlog = sum(o.issues.open_count * w for o, w in prior) if prior else None
        backlog = sum(o.issues.open_count * w for o, w in cur)
        stars = sum(o.popularity.stargazer_count * w for o, w in cur)
        prior_stars = sum(o.popularity.stargazer_count * w for o, w in prior) if prior else None
        forks = sum(o.popularity.fork_count * w for o, w in cur)
        prior_forks = sum(o.popularity.fork_count * w for o, w in prior) if prior else None
        pkg_cur = _package_window(packages, as_of, days)
        pkg_prior = _package_window(packages, as_of, days, True)
        downloads = sum(float(p.download_count or 0) for p in pkg_cur)
        prior_downloads = (
            sum(float(p.download_count or 0) for p in pkg_prior) if pkg_prior else None
        )
        features.update(
            {
                f"developer_active_contributors_{days}d": active or None,
                f"developer_active_contributor_growth_{days}d": safe_relative_change(
                    active, prior_active
                ),
                f"developer_external_contributor_growth_{days}d": safe_relative_change(
                    external, prior_external
                ),
                f"developer_commit_velocity_{days}d": commits / days,
                f"developer_release_velocity_{days}d": releases / days,
                f"developer_issue_backlog_growth_{days}d": safe_relative_change(
                    backlog, prior_backlog
                ),
                f"developer_issue_open_velocity_{days}d": issues_open / days,
                f"developer_issue_close_velocity_{days}d": issues_closed / days,
                f"developer_stargazer_growth_{days}d": safe_relative_change(stars, prior_stars),
                f"developer_fork_growth_{days}d": safe_relative_change(forks, prior_forks),
                f"developer_package_download_growth_{days}d": safe_relative_change(
                    downloads, prior_downloads
                ),
            }
        )
    contrib_counts: defaultdict[str, float] = defaultdict(float)
    total_nonbot = 0.0
    bot = 0.0
    for obs, weight in current90:
        for c in obs.contributors:
            if c.is_bot:
                bot += c.commit_count * weight
            else:
                contrib_counts[c.login] += c.commit_count * weight
                total_nonbot += c.commit_count * weight
    shares = (
        sorted((v / total_nonbot for v in contrib_counts.values()), reverse=True)
        if total_nonbot
        else []
    )
    top1 = shares[0] if shares else None
    top5 = sum(shares[:5]) if shares else None
    breadth = len(
        {
            o.repository_id
            for o, _ in current90
            if o.commit_count + o.releases.release_count + o.pull_requests.merged_count >= 3
        }
    )
    bot_share = bot / (bot + total_nonbot) if bot + total_nonbot else 0.0
    concentration = top1 if top1 is not None else None
    contributor_growth = features.get("developer_active_contributor_growth_90d")
    external_growth = features.get("developer_external_contributor_growth_90d")
    package_growth = features.get("developer_package_download_growth_90d")
    backlog_growth = features.get("developer_issue_backlog_growth_90d")
    star_growth = features.get("developer_stargazer_growth_90d")
    quality_score = 0.0
    for value, weight in ((contributor_growth, 28), (external_growth, 18), (package_growth, 18)):
        if isinstance(value, (float, int)):
            quality_score += max(-1, min(1, value)) * weight
    if isinstance(backlog_growth, (float, int)):
        quality_score -= max(-1, min(1, backlog_growth)) * 18
    if concentration is not None:
        quality_score -= max(0, concentration - 0.35) * 30
    quality_score += min(10, breadth * 3) - bot_share * 15
    if (
        isinstance(star_growth, (float, int))
        and star_growth > 0.75
        and not any(
            isinstance(v, (float, int)) and v > 0.1
            for v in (contributor_growth, external_growth, package_growth)
        )
    ):
        diagnostics.append("star spike lacks contributor, fork, release, or package corroboration")
        quality_score -= 12
    features.update(
        {
            "developer_contributor_concentration": concentration,
            "developer_top_one_contributor_share": top1,
            "developer_top_five_contributor_share": top5,
            "developer_bus_factor_proxy": 1 / top1 if top1 else None,
            "developer_bot_activity_share": bot_share,
            "developer_ecosystem_breadth": breadth,
            "developer_activity_concentration": concentration,
            "developer_momentum_quality_score": max(-100.0, min(100.0, quality_score)),
            "developer_cross_source_agreement": _agreement(package_growth, contributor_growth),
        }
    )
    for obs, _ in selected:
        provenance[f"{obs.provider_record_id}"] = obs.source_provenance
        source_as_of[f"{obs.provider_record_id}"] = obs.available_at
        if getattr(obs, "is_archived", False):
            diagnostics.append("archived repository excluded")
        if obs.imported_history:
            diagnostics.append("repository import history flagged")
        if (
            obs.generated_activity_share
            + obs.mass_formatting_share
            + obs.lockfile_only_share
            + obs.dependency_update_share
            > 0.5
        ):
            diagnostics.append("generated or automated activity is elevated")
    freshness = min((as_of - max(source_as_of.values())).days if source_as_of else 999, 999)
    completeness = min(
        1.0,
        len(active_repo_ids)
        / max(1, len([m for m in mappings if m.ticker == ticker and m.include])),
    )
    confidence = max(
        0.0,
        min(
            1.0,
            0.2
            + 0.25 * bool(current90)
            + 0.15 * min(1, breadth / 2)
            + 0.15 * bool(packages)
            + 0.15 * completeness
            + 0.10 * max(0, 1 - freshness / 180)
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
            "windows": list(windows),
            "mapped_repositories": len(active_repo_ids),
            "package_observations": len(packages),
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
