from __future__ import annotations

import json
from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, model_validator

from smct_research.core.models import normalize_utc


class RepositoryRole(StrEnum):
    CORE_PRODUCT = "core product"
    SDK = "SDK"
    INFRASTRUCTURE = "infrastructure"
    EXAMPLE = "example"
    DOCUMENTATION = "documentation"
    INTEGRATION = "integration"
    EXPERIMENTAL = "experimental"
    UNKNOWN = "unknown"


class PackageEcosystem(StrEnum):
    PYPI = "pypi"
    NPM = "npm"
    CRATES = "crates.io"
    MAVEN = "maven"
    NUGET = "nuget"
    RUBYGEMS = "rubygems"
    OCI = "oci"


class RepositoryIdentity(BaseModel, frozen=True):
    provider: str = "github"
    owner: str = Field(min_length=1)
    name: str = Field(min_length=1)
    repository_id: str = Field(min_length=1)
    source_identifier: str | None = None
    company_ticker: str
    is_first_party: bool = True
    is_archived: bool = False
    is_fork: bool = False
    is_mirror: bool = False
    primary_language: str | None = None
    role: RepositoryRole = RepositoryRole.UNKNOWN
    first_seen_at: datetime
    retrieved_at: datetime
    available_at: datetime

    @model_validator(mode="after")
    def normalize(self) -> RepositoryIdentity:
        object.__setattr__(self, "company_ticker", self.company_ticker.upper().strip())
        for field in ("first_seen_at", "retrieved_at", "available_at"):
            object.__setattr__(self, field, normalize_utc(getattr(self, field)))
        return self

    @property
    def key(self) -> str:
        return f"{self.provider}:{self.owner}/{self.name}"


class ContributorActivity(BaseModel, frozen=True):
    login: str
    commit_count: int = Field(ge=0)
    is_bot: bool = False
    is_external: bool | None = None
    email_domain: str | None = None


class PullRequestActivity(BaseModel, frozen=True):
    opened_count: int = Field(ge=0)
    merged_count: int = Field(ge=0)
    median_merge_time_hours: float | None = Field(default=None, ge=0)
    open_count: int = Field(default=0, ge=0)
    abandoned_share: float | None = Field(default=None, ge=0, le=1)


class IssueActivity(BaseModel, frozen=True):
    opened_count: int = Field(ge=0)
    closed_count: int = Field(ge=0)
    open_count: int = Field(ge=0)
    median_close_time_hours: float | None = Field(default=None, ge=0)
    stale_share: float | None = Field(default=None, ge=0, le=1)


class ReleaseActivity(BaseModel, frozen=True):
    release_count: int = Field(ge=0)
    latest_release_at: datetime | None = None

    @model_validator(mode="after")
    def normalize(self) -> ReleaseActivity:
        if self.latest_release_at is not None:
            object.__setattr__(self, "latest_release_at", normalize_utc(self.latest_release_at))
        return self


class StargazerForkObservation(BaseModel, frozen=True):
    stargazer_count: int = Field(ge=0)
    fork_count: int = Field(ge=0)
    watcher_count: int | None = Field(default=None, ge=0)
    star_spike_share: float | None = Field(default=None, ge=0, le=1)


class RepositoryObservation(BaseModel, frozen=True):
    provider: str = "github"
    provider_record_id: str = Field(min_length=1)
    repository_id: str = Field(min_length=1)
    owner: str = Field(min_length=1)
    name: str = Field(min_length=1)
    is_archived: bool = False
    is_fork: bool = False
    is_mirror: bool = False
    company_ticker: str
    commit_count: int = Field(ge=0)
    active_contributor_count: int = Field(ge=0)
    new_contributor_count: int = Field(ge=0)
    returning_contributor_count: int = Field(ge=0)
    external_contributor_count: int = Field(default=0, ge=0)
    internal_contributor_count: int | None = Field(default=None, ge=0)
    unique_corporate_email_domains: int | None = Field(default=None, ge=0)
    pull_requests: PullRequestActivity
    issues: IssueActivity
    releases: ReleaseActivity
    popularity: StargazerForkObservation
    contributors: tuple[ContributorActivity, ...] = ()
    default_branch_activity_count: int = Field(default=0, ge=0)
    repository_size_kb: int | None = Field(default=None, ge=0)
    generated_code_share: float | None = Field(default=None, ge=0, le=1)
    generated_activity_share: float = Field(default=0, ge=0, le=1)
    bot_activity_share: float = Field(default=0, ge=0, le=1)
    mass_formatting_share: float = Field(default=0, ge=0, le=1)
    lockfile_only_share: float = Field(default=0, ge=0, le=1)
    dependency_update_share: float = Field(default=0, ge=0, le=1)
    imported_history: bool = False
    observation_window_start: datetime
    observation_window_end: datetime
    retrieved_at: datetime | None = None
    available_at: datetime
    source_provenance: str = Field(min_length=1)

    @model_validator(mode="after")
    def normalize(self) -> RepositoryObservation:
        object.__setattr__(self, "company_ticker", self.company_ticker.upper().strip())
        for field in ("observation_window_start", "observation_window_end", "available_at"):
            object.__setattr__(self, field, normalize_utc(getattr(self, field)))
        if self.retrieved_at is not None:
            object.__setattr__(self, "retrieved_at", normalize_utc(self.retrieved_at))
        object.__setattr__(self, "provider", self.provider.strip().lower())
        object.__setattr__(self, "owner", self.owner.strip())
        object.__setattr__(self, "name", self.name.strip())
        if self.observation_window_end <= self.observation_window_start:
            raise ValueError("observation window end must be after start")
        if self.available_at < self.observation_window_end:
            raise ValueError("available_at cannot be before observation_window_end")
        if (
            self.new_contributor_count + self.returning_contributor_count
            > self.active_contributor_count
        ):
            raise ValueError("new plus returning contributors cannot exceed active contributors")
        if self.external_contributor_count > self.active_contributor_count:
            raise ValueError("external contributors cannot exceed active contributors")
        return self

    @property
    def repo_key(self) -> str:
        return f"{self.provider}:{self.owner}/{self.name}"


class PackageObservation(BaseModel, frozen=True):
    provider: str
    provider_record_id: str = Field(min_length=1)
    ecosystem: PackageEcosystem
    package_name: str = Field(min_length=1)
    company_ticker: str
    repository_id: str | None = None
    repository_owner: str | None = None
    repository_name: str | None = None
    download_count: int | None = Field(default=None, ge=0)
    dependent_package_count: int | None = Field(default=None, ge=0)
    release_count: int = Field(default=0, ge=0)
    latest_release_at: datetime | None = None
    observation_window_start: datetime
    observation_window_end: datetime
    available_at: datetime
    source_identifier: str = Field(min_length=1)

    @model_validator(mode="after")
    def normalize(self) -> PackageObservation:
        object.__setattr__(self, "company_ticker", self.company_ticker.upper().strip())
        for field in ("observation_window_start", "observation_window_end", "available_at"):
            object.__setattr__(self, field, normalize_utc(getattr(self, field)))
        if self.latest_release_at is not None:
            object.__setattr__(self, "latest_release_at", normalize_utc(self.latest_release_at))
        if self.observation_window_end <= self.observation_window_start:
            raise ValueError("observation window end must be after start")
        if self.available_at < self.observation_window_end:
            raise ValueError("available_at cannot be before observation_window_end")
        provider = self.provider.strip().lower()
        if not provider:
            raise ValueError("package observation provider cannot be empty")
        object.__setattr__(self, "provider", provider)
        object.__setattr__(self, "package_name", self.package_name.strip().lower())
        if self.repository_id is not None:
            repository_id = self.repository_id.strip()
            if not repository_id:
                raise ValueError("package observation repository_id cannot be empty")
            object.__setattr__(self, "repository_id", repository_id)
        if (self.repository_owner is None) != (self.repository_name is None):
            raise ValueError("package observation repository owner/name must be provided together")
        if self.repository_owner is not None and self.repository_name is not None:
            owner = self.repository_owner.strip()
            name = self.repository_name.strip()
            if not owner or not name:
                raise ValueError("package observation repository owner/name cannot be empty")
            object.__setattr__(self, "repository_owner", owner.lower())
            object.__setattr__(self, "repository_name", name.lower())
        return self


class PackageSelector(BaseModel, frozen=True):
    ecosystem: PackageEcosystem
    package_name: str = Field(min_length=1)
    provider: str | None = None
    repository_id: str | None = None
    repository_owner: str | None = None
    repository_name: str | None = None

    @model_validator(mode="after")
    def normalize(self) -> PackageSelector:
        object.__setattr__(self, "package_name", self.package_name.strip().lower())
        if self.provider is not None:
            provider = self.provider.strip().lower()
            if not provider:
                raise ValueError("package selector provider cannot be empty")
            object.__setattr__(self, "provider", provider)
        if self.repository_id is not None:
            repository_id = self.repository_id.strip()
            if not repository_id:
                raise ValueError("package selector repository_id cannot be empty")
            object.__setattr__(self, "repository_id", repository_id)
        if self.repository_owner is not None:
            owner = self.repository_owner.strip()
            if not owner:
                raise ValueError("package selector repository_owner cannot be empty")
            object.__setattr__(self, "repository_owner", owner.lower())
        if self.repository_name is not None:
            name = self.repository_name.strip()
            if not name:
                raise ValueError("package selector repository_name cannot be empty")
            if not self.repository_owner:
                raise ValueError("package selector repository_name requires repository_owner")
            object.__setattr__(self, "repository_name", name.lower())
        return self


class RepositoryMapping(BaseModel, frozen=True):
    ticker: str
    provider: str = "github"
    owner: str | None = None
    name: str | None = None
    repository_id: str | None = None
    organization: str | None = None
    include: bool = True
    role: RepositoryRole = RepositoryRole.UNKNOWN
    weight: float = Field(default=1.0, ge=0)
    is_first_party: bool = True
    package_names: tuple[str, ...] = ()
    package_selectors: tuple[PackageSelector, ...] = ()
    include_forks: bool = False
    effective_from: datetime
    effective_to: datetime | None = None
    known_at: datetime

    @model_validator(mode="after")
    def normalize(self) -> RepositoryMapping:
        object.__setattr__(self, "ticker", self.ticker.upper().strip())
        object.__setattr__(self, "effective_from", normalize_utc(self.effective_from))
        object.__setattr__(self, "known_at", normalize_utc(self.known_at))
        if self.effective_to is not None:
            object.__setattr__(self, "effective_to", normalize_utc(self.effective_to))
        provider = self.provider.strip().lower()
        if not provider:
            raise ValueError("repository mapping provider cannot be empty")
        object.__setattr__(self, "provider", provider)
        if self.repository_id is not None:
            repository_id = self.repository_id.strip()
            if not repository_id:
                raise ValueError("repository_id cannot be empty")
            object.__setattr__(self, "repository_id", repository_id)
        if self.owner is not None:
            owner = self.owner.strip()
            if not owner:
                raise ValueError("mapping owner cannot be empty")
            object.__setattr__(self, "owner", owner.lower())
        if self.name is not None:
            name = self.name.strip()
            if not name:
                raise ValueError("mapping repository name cannot be empty")
            object.__setattr__(self, "name", name.lower())
        if self.organization is not None:
            organization = self.organization.strip()
            if not organization:
                raise ValueError("mapping organization cannot be empty")
            object.__setattr__(self, "organization", organization.lower())
        if self.name and not self.owner:
            raise ValueError("repository name mappings require an owner")
        if self.package_names:
            raise ValueError("package_names is deprecated; use package_selectors with ecosystem")
        if not any((self.repository_id, self.name, self.organization, self.package_selectors)):
            raise ValueError("mapping requires repository, organization, or package selector")
        if self.effective_to is not None and self.effective_to < self.effective_from:
            raise ValueError("effective_to cannot be before effective_from")
        return self

    def is_effective(self, as_of: datetime) -> bool:
        ts = normalize_utc(as_of)
        return (
            self.effective_from <= ts
            and self.known_at <= ts
            and (self.effective_to is None or ts <= self.effective_to)
        )

    def matches(self, obs: RepositoryObservation) -> bool:
        if not any((self.repository_id, self.owner and self.name, self.organization)):
            return False
        if obs.provider != self.provider:
            return False
        if self.repository_id and obs.repository_id != self.repository_id:
            return False
        if self.owner and obs.owner.lower() != self.owner.lower():
            return False
        if self.name and obs.name.lower() != self.name.lower():
            return False
        if self.organization and obs.owner.lower() != self.organization.lower():
            return False
        return True


def canonical_package_series_identity(
    package: PackageObservation,
    repository_provider: str | None = None,
    grain_days: int | None = None,
    include_interval: bool = False,
) -> tuple[str, ...]:
    identity: tuple[str, ...] = (
        package.provider.strip().lower(),
        package.ecosystem.value,
        package.package_name.strip().lower(),
        (repository_provider or "").strip().lower(),
        (package.repository_id or "").strip(),
        (package.repository_owner or "").strip().lower(),
        (package.repository_name or "").strip().lower(),
    )
    if grain_days is not None:
        identity = (*identity, f"{grain_days}d")
    if include_interval:
        identity = (
            *identity,
            package.observation_window_start.isoformat(),
            package.observation_window_end.isoformat(),
            package.available_at.isoformat(),
        )
    return identity


def canonical_mapping_identity(mapping: RepositoryMapping) -> str:
    payload = mapping.model_dump(mode="json")
    for key in ("provider", "owner", "name", "organization"):
        if payload.get(key) is not None:
            payload[key] = str(payload[key]).strip().lower()
    if payload.get("repository_id") is not None:
        payload["repository_id"] = str(payload["repository_id"]).strip()
    selectors = []
    for selector in payload.get("package_selectors") or []:
        normalized = dict(selector)
        for key in ("ecosystem", "package_name", "provider", "repository_owner", "repository_name"):
            if normalized.get(key) is not None:
                normalized[key] = str(normalized[key]).strip().lower()
        if normalized.get("repository_id") is not None:
            normalized["repository_id"] = str(normalized["repository_id"]).strip()
        selectors.append(normalized)
    payload["package_selectors"] = sorted(
        selectors,
        key=lambda item: (
            item.get("ecosystem") or "",
            item.get("package_name") or "",
            item.get("provider") or "",
            item.get("repository_id") or "",
            item.get("repository_owner") or "",
            item.get("repository_name") or "",
        ),
    )
    payload["package_names"] = sorted(payload.get("package_names") or [])
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


class DeveloperDataQuality(BaseModel, frozen=True):
    score: float = Field(ge=0, le=1)
    data_freshness_days: float | None = None
    coverage_percentage: float = Field(ge=0, le=1)
    completeness: float = Field(ge=0, le=1)
    diagnostics: tuple[str, ...] = ()


class DeveloperEcosystemFeatures(BaseModel, frozen=True):
    ticker: str
    as_of: datetime
    features: dict[str, float | int | str | bool | None]
    quality: DeveloperDataQuality
    evidence: tuple[str, ...] = ()
    risks: tuple[str, ...] = ()
    provenance: dict[str, str] = Field(default_factory=dict)
    source_as_of: dict[str, datetime] = Field(default_factory=dict)
    diagnostics: tuple[str, ...] = ()
    metadata: dict[str, Any] = Field(default_factory=dict)
