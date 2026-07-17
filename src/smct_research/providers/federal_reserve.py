"""FRED/ALFRED and H.4.1 adapters; all I/O stays outside research signals."""

from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import UTC, date, datetime
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


def _date_field(item: dict[str, Any], *names: str) -> date | None:
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
        self.user_agent = user_agent or os.getenv("FRED_USER_AGENT")
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
        definition = FRED_SERIES.get(series_id)
        if definition is None:
            raise ValueError(f"No series metadata registered for {series_id!r}")
        retrieved = retrieved_at or datetime.now(UTC)
        result: list[MacroObservation] = []
        for item in payload.get("observations", []):
            if item.get("value") == ".":
                continue
            observation_date = datetime.fromisoformat(item["date"]).date()
            vintage_date = _date_field(item, "vintage_date", "realtime_start")
            publication_date = _date_field(
                item, "publication_date", "release_date", "realtime_start"
            )
            # FRED/ALFRED's realtime_start is the first vintage containing this value.
            first_available_on = _date_field(item, "first_available_on", "realtime_start")
            if first_available_on is None:
                raise ValueError(
                    f"{series_id} observation {observation_date} lacks public availability"
                )
            status = (
                RevisionStatus.REVISED
                if vintage and definition.revisions_occur
                else RevisionStatus.INITIAL
            )
            result.append(
                MacroObservation(
                    series_id=series_id,
                    observation_date=observation_date,
                    publication_date=publication_date or first_available_on,
                    first_available_on=first_available_on,
                    vintage_date=vintage_date,
                    retrieved_at=retrieved,
                    value=float(item["value"]),
                    unit=definition.unit,
                    source="alfred" if vintage else "fred",
                    revision_status=status,
                    frequency=definition.frequency,
                    provenance_url="https://fred.stlouisfed.org/series/" + series_id,
                )
            )
        return result

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
