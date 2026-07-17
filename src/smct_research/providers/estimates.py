"""Provider-neutral estimate interface and deterministic local-file adapter."""

from __future__ import annotations

import csv
import json
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol

from smct_research.estimates.models import ConsensusEstimate

from .base import ProviderResponseError


class EstimateProvider(Protocol):
    provider_id: str

    def fetch_estimate_history(
        self, ticker: str, start: datetime | None = None, end: datetime | None = None
    ) -> list[ConsensusEstimate]: ...
    def normalize_estimates(
        self, raw_records: Iterable[dict[str, Any]]
    ) -> list[ConsensusEstimate]: ...


class OfflineEstimateProvider:
    provider_id = "offline_file"

    def __init__(self, path: Path):
        self.path = path

    def _raw(self) -> list[dict[str, Any]]:
        try:
            text = self.path.read_text()
            if self.path.suffix.lower() == ".jsonl":
                return [json.loads(line) for line in text.splitlines() if line.strip()]
            if self.path.suffix.lower() == ".csv":
                return list(csv.DictReader(text.splitlines()))
            value = json.loads(text)
            return value if isinstance(value, list) else value.get("records", [])
        except (OSError, ValueError, json.JSONDecodeError) as error:
            raise ProviderResponseError(f"invalid estimate file: {error}") from error

    def normalize_estimates(self, raw_records: Iterable[dict[str, Any]]) -> list[ConsensusEstimate]:
        values = []
        seen: dict[str, ConsensusEstimate] = {}
        for raw in raw_records:
            try:
                item = ConsensusEstimate.model_validate(
                    {**raw, "provider": raw.get("provider", self.provider_id)}
                )
            except Exception as error:
                raise ProviderResponseError(f"invalid estimate record: {error}") from error
            prior = seen.get(item.provider_record_id)
            if prior is not None:
                if prior.model_dump(mode="json") == item.model_dump(mode="json"):
                    continue
                raise ProviderResponseError(
                    f"duplicate provider record ID: {item.provider_record_id}"
                )
            seen[item.provider_record_id] = item
            values.append(item)
        return sorted(values, key=lambda x: (x.available_at, x.provider_record_id))

    def fetch_estimate_history(
        self, ticker: str, start: datetime | None = None, end: datetime | None = None
    ) -> list[ConsensusEstimate]:
        return [
            x
            for x in self.normalize_estimates(self._raw())
            if x.ticker == ticker.upper()
            and (start is None or x.available_at >= start)
            and (end is None or x.available_at <= end)
        ]
