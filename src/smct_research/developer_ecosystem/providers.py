from __future__ import annotations

import json
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from smct_research.developer_ecosystem.models import (
    PackageObservation,
    RepositoryMapping,
    RepositoryObservation,
)
from smct_research.providers.base import ProviderResponseError


class DeveloperHistoryProvider(ABC):
    @abstractmethod
    def fetch_repository_history(self, ticker: str) -> list[RepositoryObservation]: ...


class PackageHistoryProvider(ABC):
    @abstractmethod
    def fetch_package_history(self, ticker: str) -> list[PackageObservation]: ...


class GitHubProviderContract(DeveloperHistoryProvider):
    def __init__(
        self, token: str | None = None, cache_directory: Path | None = None, retry_count: int = 2
    ) -> None:
        self.token = token
        self.cache_directory = cache_directory
        self.retry_count = retry_count
        self.rate_limit_remaining: int | None = None
        self.incomplete_history_diagnostics: list[str] = []

    def fetch_repository_history(self, ticker: str) -> list[RepositoryObservation]:
        raise ProviderResponseError("live GitHub access is not implemented; use offline fixtures")


def _records(path: Path) -> list[dict[str, Any]]:
    try:
        text = path.read_text()
        if path.suffix.lower() == ".jsonl":
            return [json.loads(line) for line in text.splitlines() if line.strip()]
        raw = json.loads(text)
    except (OSError, json.JSONDecodeError) as error:
        raise ProviderResponseError(f"malformed provider file: {error}") from error
    if isinstance(raw, dict):
        for key in ("repository_observations", "package_observations", "mappings", "records"):
            if key in raw:
                raw = raw[key]
                break
    if not isinstance(raw, list) or any(not isinstance(item, dict) for item in raw):
        raise ProviderResponseError("provider file must contain a JSON array of objects")
    return raw


class OfflineDeveloperHistoryProvider(DeveloperHistoryProvider):
    def __init__(self, path: Path) -> None:
        self.path = path

    def fetch_repository_history(self, ticker: str) -> list[RepositoryObservation]:
        wanted = ticker.upper().strip()
        records = [RepositoryObservation.model_validate(item) for item in _records(self.path)]
        return [item for item in records if item.company_ticker == wanted]


class OfflinePackageHistoryProvider(PackageHistoryProvider):
    def __init__(self, path: Path) -> None:
        self.path = path

    def fetch_package_history(self, ticker: str) -> list[PackageObservation]:
        wanted = ticker.upper().strip()
        records = [PackageObservation.model_validate(item) for item in _records(self.path)]
        return [item for item in records if item.company_ticker == wanted]


def load_repository_mappings(path: Path, ticker: str | None = None) -> list[RepositoryMapping]:
    records = [RepositoryMapping.model_validate(item) for item in _records(path)]
    if ticker:
        wanted = ticker.upper().strip()
        records = [item for item in records if item.ticker == wanted]
    return records
