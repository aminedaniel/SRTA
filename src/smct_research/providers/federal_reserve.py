"""Current FRED and point-in-time ALFRED adapters with durable raw-cache snapshots."""

from __future__ import annotations

import hashlib
import json
import os
import re
from collections.abc import Mapping
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from smct_research.macro.models import (
    FedBalanceSheetRelease,
    MacroObservation,
    RevisionStatus,
    SeriesDefinition,
)
from smct_research.providers.base import (
    DataProvider,
    FixedRetryPolicy,
    ProviderConfigurationError,
    ProviderResponseError,
    RateLimiter,
    RetryPolicy,
)
from smct_research.providers.sec_edgar import ConservativeRateLimiter, Transport, _urlopen_transport


def _series(
    unit: str, frequency: str, release_source: str, revisions_occur: bool
) -> SeriesDefinition:
    return SeriesDefinition(
        unit=unit,
        frequency=frequency,
        release_source=release_source,
        revisions_occur=revisions_occur,
    )


FRED_SERIES: dict[str, SeriesDefinition] = {
    "EFFR": _series("percent", "daily", "Federal Reserve Bank of New York", True),
    "CPIAUCSL": _series("index_1982_84_100", "monthly", "Bureau of Labor Statistics", True),
    "DGS2": _series("percent", "daily", "U.S. Treasury", True),
    "DGS10": _series("percent", "daily", "U.S. Treasury", True),
    "DGS3MO": _series("percent", "daily", "U.S. Treasury", True),
    "WALCL": _series("millions_usd", "weekly", "Federal Reserve H.4.1", True),
    "WRESBAL": _series("millions_usd", "weekly", "Federal Reserve H.4.1", True),
    "RRPONTSYD": _series("billions_usd", "daily", "Federal Reserve Bank of New York", True),
    "T10Y2Y": _series("percentage_points", "daily", "U.S. Treasury", True),
    "T10Y3M": _series("percentage_points", "daily", "U.S. Treasury", True),
    "H41_EMERGENCY": _series("millions_usd", "weekly", "Federal Reserve H.4.1", True),
}
USER_AGENT_EMAIL_PATTERN = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")


def _date_field(item: Mapping[str, Any], *names: str) -> date | None:
    for name in names:
        if item.get(name):
            return datetime.fromisoformat(str(item[name])).date()
    return None


class FederalReserveProvider(DataProvider):
    provider_id = "federal_reserve"

    def __init__(
        self,
        cache_dir: Path,
        api_key: str | None = None,
        *,
        user_agent: str | None = None,
        cache_ttl: timedelta | None = None,
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
        self.user_agent, self.cache_ttl = user_agent or os.getenv("FRED_USER_AGENT"), cache_ttl
        self.rate_limiter, self.retry_policy, self.transport = (
            rate_limiter or ConservativeRateLimiter(),
            retry_policy or FixedRetryPolicy(),
            transport or _urlopen_transport,
        )

    def fetch(self, identifier: str) -> dict[str, Any]:
        return self.fred_series(identifier)

    def fred_series(self, series_id: str, *, refresh: bool = False) -> dict[str, Any]:
        """Fetch current values only; use ``alfred_series`` for historical PIT research."""
        return self._json(
            "fred/series/observations",
            {"series_id": series_id},
            f"fred/{series_id}.json",
            refresh=refresh,
        )

    def alfred_series(
        self, series_id: str, vintage_date: str, *, refresh: bool = False
    ) -> dict[str, Any]:
        """Fetch a requested ALFRED vintage; normalization needs release availability."""
        return self._json(
            "fred/series/observations",
            {"series_id": series_id, "vintage_dates": vintage_date},
            f"alfred/{series_id}-{vintage_date}.json",
            refresh=refresh,
        )

    def _cache_is_fresh(self, path: Path) -> bool:
        if not path.exists():
            return False
        if self.cache_ttl is None:
            return True
        modified = datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)
        return datetime.now(UTC) - modified <= self.cache_ttl

    def _json(
        self, endpoint: str, params: dict[str, str], cache_name: str, *, refresh: bool
    ) -> dict[str, Any]:
        path = self.cache_dir / cache_name
        if not refresh and self._cache_is_fresh(path):
            return self._read_json(path)
        if not self.api_key:
            raise ProviderConfigurationError(
                "FRED_API_KEY must be supplied for uncached FRED requests"
            )
        user_agent = self.user_agent.strip() if self.user_agent else ""
        if (
            "contact@example.com" in user_agent
            or len(user_agent) < 8
            or not USER_AGENT_EMAIL_PATTERN.search(user_agent)
        ):
            raise ProviderConfigurationError(
                "FRED_USER_AGENT must identify the application and include a contact email "
                "before uncached FRED requests"
            )
        query = urlencode({**params, "api_key": self.api_key, "file_type": "json"})

        def download() -> dict[str, Any]:
            self.rate_limiter.acquire()
            try:
                data = json.loads(
                    self.transport(
                        f"{self.base_url}/{endpoint}?{query}", {"User-Agent": user_agent}
                    )
                )
            except json.JSONDecodeError as error:
                raise ProviderResponseError("FRED returned invalid JSON") from error
            path.parent.mkdir(parents=True, exist_ok=True)
            encoded = json.dumps(data, sort_keys=True)
            path.write_text(encoded)
            timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
            snapshot = path.parent / "snapshots" / f"{path.stem}-{timestamp}{path.suffix}"
            snapshot.parent.mkdir(parents=True, exist_ok=True)
            snapshot.write_text(encoded)
            return data

        return self.retry_policy.run(download)

    @staticmethod
    def _definition(series_id: str) -> SeriesDefinition:
        definition = FRED_SERIES.get(series_id)
        if definition is None:
            raise ValueError(f"No series metadata registered for {series_id!r}")
        return definition

    @classmethod
    def normalize_current_fred(
        cls, payload: dict[str, Any], series_id: str, *, retrieved_at: datetime | None = None
    ) -> list[MacroObservation]:
        """Normalize current FRED values; realtime_start is not an original release date."""
        definition, retrieved = cls._definition(series_id), retrieved_at or datetime.now(UTC)
        return [
            MacroObservation(
                series_id=series_id,
                observation_date=datetime.fromisoformat(item["date"]).date(),
                publication_date=None,
                first_available_on=None,
                vintage_date=_date_field(item, "realtime_start"),
                retrieved_at=retrieved,
                value=float(item["value"]),
                unit=definition.unit,
                source="fred_current",
                revision_status=RevisionStatus.UNKNOWN,
                frequency=definition.frequency,
                provenance_url="https://fred.stlouisfed.org/series/" + series_id,
                point_in_time_eligible=False,
            )
            for item in payload.get("observations", [])
            if item.get("value") != "."
        ]

    @classmethod
    def normalize_alfred(
        cls,
        payload: dict[str, Any],
        series_id: str,
        *,
        availability_by_observation: Mapping[date, date],
        retrieved_at: datetime | None = None,
    ) -> list[MacroObservation]:
        """Normalize a historical ALFRED vintage using a complete release-calendar mapping."""
        definition, retrieved = cls._definition(series_id), retrieved_at or datetime.now(UTC)
        result = []
        for item in payload.get("observations", []):
            if item.get("value") == ".":
                continue
            observed = datetime.fromisoformat(item["date"]).date()
            original_available = availability_by_observation.get(observed)
            vintage_available = _date_field(item, "realtime_start")
            if original_available is None or vintage_available is None:
                raise ValueError(
                    f"{series_id} observation {observed} lacks complete vintage availability"
                )
            result.append(
                MacroObservation(
                    series_id=series_id,
                    observation_date=observed,
                    publication_date=vintage_available,
                    first_available_on=original_available,
                    vintage_date=vintage_available,
                    retrieved_at=retrieved,
                    value=float(item["value"]),
                    unit=definition.unit,
                    source="alfred",
                    revision_status=(
                        RevisionStatus.REVISED
                        if definition.revisions_occur and vintage_available > original_available
                        else RevisionStatus.INITIAL
                    ),
                    frequency=definition.frequency,
                    provenance_url="https://fred.stlouisfed.org/series/" + series_id,
                    point_in_time_eligible=True,
                )
            )
        return result

    normalize_fred = normalize_current_fred

    @staticmethod
    def normalize_h41(
        payload: dict[str, Any], source_url: str, published_at: datetime
    ) -> FedBalanceSheetRelease:
        observations = []
        for item in payload["observations"]:
            definition = FRED_SERIES.get(item["series_id"])
            observations.append(
                MacroObservation(
                    series_id=item["series_id"],
                    observation_date=datetime.fromisoformat(item["date"]).date(),
                    publication_date=published_at.date(),
                    first_available_on=published_at.date(),
                    retrieved_at=published_at,
                    value=float(item["value"]),
                    unit=item.get("unit") or (definition.unit if definition else "millions_usd"),
                    source="federal_reserve_h41",
                    frequency=definition.frequency if definition else "weekly",
                    provenance_url=source_url,
                )
            )
        return FedBalanceSheetRelease(
            release_date=published_at.date(),
            publication_timestamp=published_at,
            source_url=source_url,
            document_hash=hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest(),
            observations=observations,
        )

    @staticmethod
    def _read_json(path: Path) -> dict[str, Any]:
        try:
            data = json.loads(path.read_text())
        except json.JSONDecodeError as error:
            raise ProviderResponseError(f"Cached FRED JSON is invalid: {path}") from error
        if not isinstance(data, dict):
            raise ProviderResponseError(f"Cached FRED JSON must be an object: {path}")
        return data
