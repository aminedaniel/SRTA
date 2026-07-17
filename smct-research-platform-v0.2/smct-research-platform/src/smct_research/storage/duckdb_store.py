"""Local-only DuckDB persistence for raw evidence and immutable observations."""

from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from smct_research.financials.models import FeatureValue, FinancialObservation


class LocalAnalyticalStore:
    def __init__(self, database_path: Path) -> None:
        database_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            import duckdb  # type: ignore[import-not-found]
        except ImportError as error:
            raise RuntimeError(
                "DuckDB is required for local analytical storage; "
                "install smct-research dependencies"
            ) from error
        self.connection: Any = duckdb.connect(str(database_path))
        self.connection.execute("""CREATE TABLE IF NOT EXISTS raw_sources (
            provider VARCHAR, document_id VARCHAR, retrieved_at TIMESTAMP, source_url VARCHAR,
            payload_json VARCHAR, PRIMARY KEY(provider, document_id))""")
        self.connection.execute(
            "CREATE TABLE IF NOT EXISTS financial_observations ("
            "accession_number VARCHAR, metric VARCHAR, value DOUBLE, unit VARCHAR, "
            "currency VARCHAR, period_type VARCHAR, period_start DATE, period_end DATE, "
            "fiscal_year INTEGER, fiscal_period VARCHAR, filed_at DATE, available_on DATE, "
            "is_amendment BOOLEAN, is_superseded BOOLEAN, provenance_json VARCHAR, "
            "PRIMARY KEY(accession_number, metric, unit, period_end, period_start))"
        )

    def close(self) -> None:
        self.connection.close()

    def store_raw_source(
        self,
        provider: str,
        document_id: str,
        retrieved_at: str,
        source_url: str,
        payload: dict[str, object],
    ) -> None:
        self.connection.execute(
            "INSERT OR IGNORE INTO raw_sources VALUES (?, ?, ?, ?, ?)",
            [provider, document_id, retrieved_at, source_url, json.dumps(payload, sort_keys=True)],
        )

    def store_observations(self, observations: Iterable[FinancialObservation]) -> None:
        for item in observations:
            self.connection.execute(
                "INSERT OR IGNORE INTO financial_observations "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    item.filing.accession_number,
                    item.metric,
                    item.value,
                    item.unit,
                    item.currency,
                    item.period_type.value,
                    item.period_start,
                    item.period_end,
                    item.fiscal_year,
                    item.fiscal_period,
                    item.filing.filed_at,
                    item.available_on,
                    item.filing.is_amendment,
                    item.filing.is_superseded,
                    item.provenance.model_dump_json(),
                ],
            )

    def export_features_parquet(self, features: dict[str, FeatureValue], path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        rows = [
            (
                name,
                value.value,
                value.as_of,
                value.available_on,
                value.quality_score,
                json.dumps(value.source_accessions),
            )
            for name, value in features.items()
        ]
        self.connection.execute(
            "CREATE OR REPLACE TEMP TABLE feature_export("
            "name VARCHAR, value DOUBLE, as_of DATE, available_on DATE, "
            "quality_score DOUBLE, source_accessions VARCHAR)"
        )
        self.connection.executemany("INSERT INTO feature_export VALUES (?, ?, ?, ?, ?, ?)", rows)
        escaped = str(path).replace("'", "''")
        self.connection.execute(f"COPY feature_export TO '{escaped}' (FORMAT PARQUET)")
