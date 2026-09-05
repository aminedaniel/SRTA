"""SEC import boundary that emits canonical snapshots consumed by the screener."""

from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import UTC, datetime, time, timedelta
from pathlib import Path

import typer

from smct_research.core.models import FeatureSnapshot
from smct_research.financials.normalize import derive_features, normalize_company_facts
from smct_research.providers.base import ProviderError
from smct_research.providers.sec_edgar import SecEdgarProvider
from smct_research.research.commands import parse_time, write_text
from smct_research.screening.io import load_universe
from smct_research.storage.duckdb_store import LocalAnalyticalStore


def ingest_sec(
    universe_file: Path,
    output_dir: Path = typer.Option(Path(".smct/features")),  # noqa: B008
    raw_directory: Path | None = typer.Option(None),  # noqa: B008
    cache_dir: Path = typer.Option(Path(".smct/sec-cache")),  # noqa: B008
    evidence_db: Path = typer.Option(Path(".smct/evidence.duckdb")),  # noqa: B008
    as_of: str | None = typer.Option(None),
    refresh: bool = typer.Option(False),
) -> None:
    """Import SEC Company Facts, using live SEC data or local CIK-numbered JSON files."""
    try:
        timestamp = parse_time(as_of)
        companies = load_universe(universe_file)
        provider = (
            None
            if raw_directory
            else SecEdgarProvider(
                os.environ.get("SMCT_SEC_USER_AGENT", ""), cache_dir, refresh=refresh
            )
        )
        if not raw_directory and timestamp > datetime.now(UTC):
            raise ValueError("A live import cannot have a future as-of timestamp")
    except (OSError, ValueError, ProviderError) as error:
        raise typer.BadParameter(str(error)) from error
    results: list[dict[str, object]] = []
    store = LocalAnalyticalStore(evidence_db)
    try:
        for company in companies:
            try:
                if not company.cik:
                    raise ValueError("CIK is required for SEC import")
                cik = SecEdgarProvider._cik(company.cik)
                if raw_directory:
                    payload = json.loads(
                        (raw_directory / f"CIK{cik}.json").read_text(encoding="utf-8")
                    )
                else:
                    assert provider is not None
                    payload = provider.company_facts(cik)
                if not isinstance(payload, dict):
                    raise ValueError("Company Facts must be a JSON object")
                if str(payload.get("cik", "")).zfill(10) != cik:
                    raise ValueError("Company Facts CIK does not match the universe company")
                retrieved = datetime.now(UTC)
                observations = normalize_company_facts(payload, retrieved_at=retrieved)
                # Company Facts has filing dates but not exact acceptance times. Make each
                # filing available at the next UTC midnight to avoid intraday look-ahead.
                cutoff = timestamp.date() - timedelta(days=1)
                features = derive_features(observations, cutoff)
                if "revenue" not in features:
                    raise ValueError(
                        "No usable revenue evidence was public before the requested time"
                    )
                sources = {}
                source_as_of = {}
                for key, feature in features.items():
                    if key in {"filing_recency_days", "missing_data_quality_score"}:
                        continue
                    for accession in feature.source_accessions:
                        source_key = f"sec:{key}:{accession}"
                        sources[source_key] = (
                            f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/"
                            f"{accession.replace('-', '')}/"
                        )
                        source_as_of[source_key] = datetime.combine(
                            feature.available_on + timedelta(days=1), time.min, UTC
                        )
                values: dict[str, float | int | str | bool | None] = {
                    key: item.value for key, item in features.items()
                }
                values.update(
                    {
                        f"period:{key}": f"{item.period_start or 'instant'} / {item.period_end}"
                        for key, item in features.items()
                        if item.period_end
                    }
                )
                snapshot = FeatureSnapshot(
                    ticker=company.ticker,
                    as_of=timestamp,
                    values=values,
                    sources=sources,
                    source_as_of=source_as_of,
                )
                document_id = hashlib.sha256(
                    json.dumps(payload, sort_keys=True).encode()
                ).hexdigest()
                store.store_raw_source(
                    "sec_edgar",
                    document_id,
                    retrieved.isoformat(),
                    f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json",
                    payload,
                )
                store.store_observations(observations)
                safe = re.sub(r"[^A-Za-z0-9.-]", "_", company.ticker)
                write_text(output_dir / f"{safe}.json", snapshot.model_dump_json(indent=2) + "\n")
                detail = {key: item.model_dump(mode="json") for key, item in features.items()}
                write_text(
                    output_dir / "financial_evidence" / f"{safe}.json",
                    json.dumps(detail, indent=2) + "\n",
                )
                results.append(
                    {"ticker": company.ticker, "status": "ok", "features": len(features)}
                )
            except (OSError, ValueError, TypeError, ProviderError) as error:
                results.append({"ticker": company.ticker, "status": "failed", "error": str(error)})
    finally:
        store.close()
    write_text(
        output_dir / "financial_evidence" / "import-manifest.json",
        json.dumps(
            {
                "as_of": timestamp.isoformat(),
                "mode": "local" if raw_directory else "SEC",
                "companies": results,
            },
            indent=2,
        )
        + "\n",
    )
    for result in results:
        typer.echo(f"{result['ticker']}: {result['status']} {result.get('error', '')}")
    if any(result["status"] == "failed" for result in results):
        raise typer.Exit(1)
