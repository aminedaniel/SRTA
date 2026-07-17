"""FRED/ALFRED and H.4.1 adapters; all I/O stays outside research signals."""

from __future__ import annotations

import hashlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from smct_research.macro.models import FedBalanceSheetRelease, MacroObservation, RevisionStatus
from smct_research.providers.base import (
    DataProvider,
    FixedRetryPolicy,
    ProviderConfigurationError,
    ProviderResponseError,
    RateLimiter,
    RetryPolicy,
)
from smct_research.providers.sec_edgar import ConservativeRateLimiter, Transport, _urlopen_transport


class FederalReserveProvider(DataProvider):
    provider_id = "federal_reserve"

    def __init__(
        self,
        cache_dir: Path,
        api_key: str | None = None,
        *,
        base_url: str = "https://api.stlouisfed.org",
        rate_limiter: RateLimiter | None = None,
        retry_policy: RetryPolicy | None = None,
        transport: Transport | None = None,
    ) -> None:
        self.cache_dir, self.api_key, self.base_url = (
            cache_dir,
            api_key or os.getenv("FRED_API_KEY"),
            base_url.rstrip("/"),
        )
        self.rate_limiter, self.retry_policy, self.transport = (
            rate_limiter or ConservativeRateLimiter(),
            retry_policy or FixedRetryPolicy(),
            transport or _urlopen_transport,
        )

    def fetch(self, identifier: str) -> dict[str, Any]:
        return self.fred_series(identifier)

    def fred_series(self, series_id: str) -> dict[str, Any]:
        return self._json(
            "fred/series/observations", {"series_id": series_id}, f"fred/{series_id}.json"
        )

    def alfred_series(self, series_id: str, vintage_date: str) -> dict[str, Any]:
        return self._json(
            "fred/series/observations",
            {"series_id": series_id, "vintage_dates": vintage_date},
            f"alfred/{series_id}-{vintage_date}.json",
        )

    def _json(self, endpoint: str, params: dict[str, str], cache_name: str) -> dict[str, Any]:
        path = self.cache_dir / cache_name
        if path.exists():
            return json.loads(path.read_text())
        if not self.api_key:
            raise ProviderConfigurationError(
                "FRED_API_KEY must be supplied for uncached FRED requests"
            )
        query = urlencode({**params, "api_key": self.api_key, "file_type": "json"})

        def download() -> dict[str, Any]:
            self.rate_limiter.acquire()
            try:
                data = json.loads(
                    self.transport(
                        f"{self.base_url}/{endpoint}?{query}",
                        {"User-Agent": "SMCT Research contact@example.com"},
                    )
                )
            except json.JSONDecodeError as error:
                raise ProviderResponseError("FRED returned invalid JSON") from error
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(data, sort_keys=True))
            return data

        return self.retry_policy.run(download)

    @staticmethod
    def normalize_fred(
        payload: dict[str, Any],
        series_id: str,
        *,
        retrieved_at: datetime | None = None,
        vintage: bool = False,
    ) -> list[MacroObservation]:
        retrieved = retrieved_at or datetime.now(UTC)
        result: list[MacroObservation] = []
        for item in payload.get("observations", []):
            if item.get("value") == ".":
                continue
            observation = datetime.fromisoformat(item["date"]).date()
            vintage_date = (
                datetime.fromisoformat(item["realtime_start"]).date()
                if item.get("realtime_start")
                else None
            )
            result.append(
                MacroObservation(
                    series_id=series_id,
                    observation_date=observation,
                    available_on=vintage_date or observation,
                    vintage_date=vintage_date,
                    retrieved_at=retrieved,
                    value=float(item["value"]),
                    unit="percent",
                    source="alfred" if vintage else "fred",
                    revision_status=RevisionStatus.REVISED if vintage else RevisionStatus.UNKNOWN,
                    frequency="daily",
                    provenance_url="https://fred.stlouisfed.org/series/" + series_id,
                )
            )
        return result

    @staticmethod
    def normalize_h41(
        payload: dict[str, Any], source_url: str, published_at: datetime
    ) -> FedBalanceSheetRelease:
        observations = [
            MacroObservation(
                series_id=x["series_id"],
                observation_date=datetime.fromisoformat(x["date"]).date(),
                available_on=published_at.date(),
                retrieved_at=published_at,
                value=float(x["value"]),
                unit=x.get("unit", "millions_usd"),
                source="federal_reserve_h41",
                frequency="weekly",
                provenance_url=source_url,
            )
            for x in payload["observations"]
        ]
        return FedBalanceSheetRelease(
            release_date=published_at.date(),
            publication_timestamp=published_at,
            source_url=source_url,
            document_hash=hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest(),
            observations=observations,
        )
