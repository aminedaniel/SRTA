"""Free SEC EDGAR JSON adapter with disk caching and conservative pacing."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from time import monotonic, sleep
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from smct_research.providers.base import (
    DataProvider,
    FixedRetryPolicy,
    ProviderConfigurationError,
    ProviderRateLimitError,
    ProviderRequestError,
    ProviderResponseError,
    RateLimiter,
    RetryPolicy,
)

SEC_BASE_URL = "https://data.sec.gov"


@dataclass
class ConservativeRateLimiter:
    """Spacing of 0.2 seconds (5 rps), intentionally below SEC's 10 rps ceiling."""

    requests_per_second: float = 5.0
    _last_request: float | None = None

    def acquire(self) -> None:
        if not 0 < self.requests_per_second < 10:
            raise ProviderRateLimitError(
                "SEC rate must be greater than 0 and below 10 requests/second"
            )
        interval = 1 / self.requests_per_second
        if self._last_request is not None:
            sleep(max(0.0, interval - (monotonic() - self._last_request)))
        self._last_request = monotonic()


Transport = Callable[[str, dict[str, str]], bytes]


def _urlopen_transport(url: str, headers: dict[str, str]) -> bytes:
    try:
        with urlopen(Request(url, headers=headers), timeout=30) as response:  # nosec B310: fixed SEC URL
            return response.read()
    except HTTPError as error:
        raise ProviderRequestError(f"SEC returned HTTP {error.code} for {url}") from error
    except URLError as error:
        raise ProviderRequestError(f"SEC request failed for {url}: {error.reason}") from error


class SecEdgarProvider(DataProvider):
    provider_id = "sec_edgar"

    def __init__(
        self,
        user_agent: str,
        cache_dir: Path,
        *,
        base_url: str = SEC_BASE_URL,
        rate_limiter: RateLimiter | None = None,
        retry_policy: RetryPolicy | None = None,
        transport: Transport | None = None,
    ) -> None:
        if not user_agent.strip() or "@" not in user_agent:
            raise ProviderConfigurationError(
                "SEC user-agent must identify the application and contact email"
            )
        self.user_agent, self.cache_dir, self.base_url = user_agent, cache_dir, base_url.rstrip("/")
        self.rate_limiter = rate_limiter or ConservativeRateLimiter()
        self.retry_policy = retry_policy or FixedRetryPolicy()
        self.transport = transport or _urlopen_transport

    def fetch(self, identifier: str) -> dict[str, Any]:
        return self._get_json(identifier, f"raw/{identifier}.json")

    def company_facts(self, cik: str | int) -> dict[str, Any]:
        cik10 = self._cik(cik)
        return self._get_json(
            f"api/xbrl/companyfacts/CIK{cik10}.json", f"companyfacts/CIK{cik10}.json"
        )

    def submissions(self, cik: str | int) -> dict[str, Any]:
        cik10 = self._cik(cik)
        return self._get_json(f"submissions/CIK{cik10}.json", f"submissions/CIK{cik10}.json")

    @staticmethod
    def _cik(cik: str | int) -> str:
        digits = str(cik).strip().lstrip("0")
        if not digits.isdigit():
            raise ProviderConfigurationError("CIK must contain digits only")
        return digits.zfill(10)

    def _get_json(self, endpoint: str, cache_name: str) -> dict[str, Any]:
        path = self.cache_dir / cache_name
        if path.exists():
            return self._read_json(path)

        def download() -> dict[str, Any]:
            self.rate_limiter.acquire()
            payload = self.transport(
                f"{self.base_url}/{endpoint.lstrip('/')}",
                {
                    "User-Agent": self.user_agent,
                    "Accept-Encoding": "gzip, deflate",
                    "Host": "data.sec.gov",
                },
            )
            try:
                data = json.loads(payload)
            except (UnicodeDecodeError, json.JSONDecodeError) as error:
                raise ProviderResponseError(f"SEC returned invalid JSON for {endpoint}") from error
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(data, sort_keys=True), encoding="utf-8")
            return data

        return self.retry_policy.run(download)

    @staticmethod
    def _read_json(path: Path) -> dict[str, Any]:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as error:
            raise ProviderResponseError(f"Cached SEC JSON is invalid: {path}") from error
        if not isinstance(data, dict):
            raise ProviderResponseError(f"Cached SEC JSON must be an object: {path}")
        return data


class Renaissance13FEdgarProvider(SecEdgarProvider):
    """Download Renaissance Technologies' public 13F-HR/13F-HR/A information tables.

    This adapter intentionally ingests only manager-level public EDGAR disclosures.
    Callers supply a CUSIP-to-ticker mapping because EDGAR's 13F tables do not
    reliably provide ticker symbols.
    """

    renaissance_cik = "0001037389"

    def renaissance_13f_filings(self) -> list[dict[str, str]]:
        recent = self.submissions(self.renaissance_cik).get("filings", {}).get("recent", {})
        forms = recent.get("form", [])
        accessions = recent.get("accessionNumber", [])
        dates = recent.get("filingDate", [])
        documents = recent.get("primaryDocument", [])
        filings: list[dict[str, str]] = []
        for form, accession, filing_date, document in zip(
            forms, accessions, dates, documents, strict=True
        ):
            if form in {"13F-HR", "13F-HR/A"}:
                filings.append(
                    {
                        "form": form,
                        "accession_number": accession,
                        "filing_date": filing_date,
                        "primary_document": document,
                    }
                )
        return filings

    def filing_document(self, accession_number: str, document_name: str) -> bytes:
        accession = accession_number.replace("-", "")
        cache_path = self.cache_dir / "13f" / accession / document_name
        if cache_path.exists():
            return cache_path.read_bytes()

        def download() -> bytes:
            self.rate_limiter.acquire()
            url = f"https://www.sec.gov/Archives/edgar/data/{int(self.renaissance_cik)}/{accession}/{document_name}"
            payload = self.transport(
                url, {"User-Agent": self.user_agent, "Accept-Encoding": "gzip, deflate"}
            )
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_bytes(payload)
            return payload

        return self.retry_policy.run(download)
