from __future__ import annotations

import json
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, TypeVar

from pydantic import ValidationError

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
            raw: object = [json.loads(line) for line in text.splitlines() if line.strip()]
        else:
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


T = TypeVar("T", RepositoryObservation, PackageObservation, RepositoryMapping)


def _canonical_payload(item: T) -> str:
    return json.dumps(item.model_dump(mode="json"), sort_keys=True)


def _provider_key(
    item: RepositoryObservation | PackageObservation | RepositoryMapping,
) -> tuple[str, str] | None:
    if isinstance(item, RepositoryMapping):
        return None
    return item.provider, item.provider_record_id


def _logical_key(
    item: RepositoryObservation | PackageObservation | RepositoryMapping,
) -> tuple[str, ...]:
    if isinstance(item, RepositoryObservation):
        return (
            "repo",
            item.provider,
            item.repository_id,
            item.observation_window_start.isoformat(),
            item.observation_window_end.isoformat(),
            item.available_at.isoformat(),
        )
    if isinstance(item, PackageObservation):
        return (
            "pkg",
            item.provider,
            item.ecosystem.value,
            item.package_name.strip().lower(),
            item.repository_id or "",
            item.observation_window_start.isoformat(),
            item.observation_window_end.isoformat(),
            item.available_at.isoformat(),
        )
    return ("map", _canonical_payload(item))


def _dedupe(items: list[T]) -> list[T]:
    by_provider: dict[tuple[str, str], str] = {}
    by_logical: dict[tuple[str, ...], str] = {}
    output: list[T] = []
    seen_payloads: set[str] = set()
    for item in items:
        payload = _canonical_payload(item)
        if payload in seen_payloads:
            continue
        provider_key = _provider_key(item)
        if provider_key is not None:
            existing = by_provider.get(provider_key)
            if existing is not None and existing != payload:
                raise ProviderResponseError("conflicting immutable provider record ID")
            by_provider[provider_key] = payload
        logical_key = _logical_key(item)
        existing = by_logical.get(logical_key)
        if existing is not None and existing != payload:
            raise ProviderResponseError("conflicting logical developer ecosystem record")
        by_logical[logical_key] = payload
        seen_payloads.add(payload)
        output.append(item)
    return output


def _validate_repository_records(path: Path) -> list[RepositoryObservation]:
    try:
        return _dedupe([RepositoryObservation.model_validate(item) for item in _records(path)])
    except ValidationError as error:
        raise ProviderResponseError(f"invalid repository observation: {error}") from error


def _validate_package_records(path: Path) -> list[PackageObservation]:
    try:
        return _dedupe([PackageObservation.model_validate(item) for item in _records(path)])
    except ValidationError as error:
        raise ProviderResponseError(f"invalid package observation: {error}") from error


def _validate_mapping_records(path: Path) -> list[RepositoryMapping]:
    try:
        return _dedupe([RepositoryMapping.model_validate(item) for item in _records(path)])
    except ValidationError as error:
        raise ProviderResponseError(f"invalid repository mapping: {error}") from error


class OfflineDeveloperHistoryProvider(DeveloperHistoryProvider):
    def __init__(self, path: Path) -> None:
        self.path = path

    def fetch_repository_history(self, ticker: str) -> list[RepositoryObservation]:
        wanted = ticker.upper().strip()
        return [
            item
            for item in _validate_repository_records(self.path)
            if item.company_ticker == wanted
        ]


class OfflinePackageHistoryProvider(PackageHistoryProvider):
    def __init__(self, path: Path) -> None:
        self.path = path

    def fetch_package_history(self, ticker: str) -> list[PackageObservation]:
        wanted = ticker.upper().strip()
        return [
            item for item in _validate_package_records(self.path) if item.company_ticker == wanted
        ]


def load_repository_mappings(path: Path, ticker: str | None = None) -> list[RepositoryMapping]:
    records = _validate_mapping_records(path)
    if ticker:
        wanted = ticker.upper().strip()
        records = [item for item in records if item.ticker == wanted]
    return records
