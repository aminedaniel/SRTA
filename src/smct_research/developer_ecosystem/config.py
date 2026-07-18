from __future__ import annotations

import json
import re
from importlib.resources import files
from pathlib import Path

from pydantic import BaseModel, Field, field_validator, model_validator

from smct_research.developer_ecosystem.models import RepositoryRole


class BotFilteringConfig(BaseModel):
    denylist_logins: list[str] = Field(default_factory=list)
    bot_login_patterns: list[str] = Field(default_factory=list)
    allowlist_logins: list[str] = Field(default_factory=list)


class ActivityThresholdsConfig(BaseModel):
    meaningful_activity_events_90d: int = 3


class StalenessThresholdsConfig(BaseModel):
    fresh_days: int = 30
    stale_days: int = 180


class ContributorConcentrationConfig(BaseModel):
    top_one_warning_share: float = 0.35
    top_one_high_risk_share: float = 0.50


class StarSpikeConfig(BaseModel):
    spike_growth_threshold: float = 0.75
    corroborating_growth_threshold: float = 0.10


class PackageAdoptionWeightsConfig(BaseModel):
    download_growth: float = 0.18
    dependent_package_growth: float = 0.10


class MinimumHistoryConfig(BaseModel):
    minimum_mapped_repositories: int = 1
    preferred_windows_days: int = 180


class B1ScoringWeightsConfig(BaseModel):
    contributor_growth: float = 0.28
    external_contributor_growth: float = 0.18
    package_download_growth: float = 0.18
    maintenance_backlog_penalty: float = 0.18
    breadth_bonus_cap: float = 0.10
    bot_penalty: float = 0.15


class DeveloperEcosystemConfig(BaseModel):
    lookback_windows_days: list[int] = Field(default_factory=lambda: [30, 90, 180])
    observation_window_tolerance_days: int = 1
    bot_filtering: BotFilteringConfig = Field(default_factory=BotFilteringConfig)
    repository_role_weights: dict[RepositoryRole, float] = Field(
        default_factory=lambda: {
            RepositoryRole.CORE_PRODUCT: 1.0,
            RepositoryRole.SDK: 0.9,
            RepositoryRole.INFRASTRUCTURE: 0.75,
            RepositoryRole.INTEGRATION: 0.65,
            RepositoryRole.EXPERIMENTAL: 0.35,
            RepositoryRole.EXAMPLE: 0.25,
            RepositoryRole.DOCUMENTATION: 0.15,
            RepositoryRole.UNKNOWN: 0.5,
        }
    )
    activity_thresholds: ActivityThresholdsConfig = Field(default_factory=ActivityThresholdsConfig)
    staleness_thresholds: StalenessThresholdsConfig = Field(
        default_factory=StalenessThresholdsConfig
    )
    contributor_concentration_penalties: ContributorConcentrationConfig = Field(
        default_factory=ContributorConcentrationConfig
    )
    star_spike_diagnostics: StarSpikeConfig = Field(default_factory=StarSpikeConfig)
    package_adoption_weights: PackageAdoptionWeightsConfig = Field(
        default_factory=PackageAdoptionWeightsConfig
    )
    minimum_history_requirements: MinimumHistoryConfig = Field(default_factory=MinimumHistoryConfig)
    b1_scoring_weights: B1ScoringWeightsConfig = Field(default_factory=B1ScoringWeightsConfig)

    @field_validator("lookback_windows_days")
    @classmethod
    def validate_windows(cls, value: list[int]) -> list[int]:
        if not value or any(item <= 0 for item in value) or 90 not in value:
            raise ValueError("lookback windows must be positive and include 90")
        return value

    @model_validator(mode="after")
    def validate_semantics(self) -> DeveloperEcosystemConfig:
        if self.observation_window_tolerance_days < 0:
            raise ValueError("observation_window_tolerance_days must be nonnegative")
        if self.staleness_thresholds.fresh_days >= self.staleness_thresholds.stale_days:
            raise ValueError("fresh_days must be less than stale_days")
        for pattern in self.bot_filtering.bot_login_patterns:
            re.compile(pattern)
        return self


def _parse_scalar(value: str) -> object:
    value = value.strip()
    if value.startswith("[") and value.endswith("]"):
        inner = value[1:-1].strip()
        return [] if not inner else [item.strip().strip("\"'") for item in inner.split(",")]
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        try:
            return int(value)
        except ValueError:
            try:
                return float(value)
            except ValueError:
                return value.strip("\"'")


def _tiny_yaml(text: str) -> dict[str, object]:
    root: dict[str, object] = {}
    stack: list[tuple[int, dict[str, object]]] = [(-1, root)]
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].rstrip()
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip(" "))
        key, _, value = line.strip().partition(":")
        while stack and indent <= stack[-1][0]:
            stack.pop()
        parent = stack[-1][1]
        if value.strip() == "":
            child: dict[str, object] = {}
            parent[key] = child
            stack.append((indent, child))
        else:
            parent[key] = _parse_scalar(value)
    return root


def load_developer_ecosystem_config(path: Path | None = None) -> DeveloperEcosystemConfig:
    try:
        if path is None:
            default = files("smct_research.config").joinpath("developer_ecosystem.yaml")
            text = Path(str(default)).read_text()
            suffix = ".yaml"
        else:
            text = path.read_text()
            suffix = path.suffix.lower()
        raw = json.loads(text) if suffix == ".json" else _tiny_yaml(text)
        return DeveloperEcosystemConfig.model_validate(raw)
    except Exception as error:
        raise ValueError(f"invalid developer ecosystem config: {error}") from error
