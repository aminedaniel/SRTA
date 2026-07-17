"""Local-only DuckDB persistence for raw evidence and immutable observations."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from smct_research.estimates.models import ConsensusEstimate

from smct_research.estimates.models import ConsensusEstimate
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
            "observation_id VARCHAR PRIMARY KEY, accession_number VARCHAR, metric VARCHAR, "
            "value DOUBLE, unit VARCHAR, "
            "currency VARCHAR, period_type VARCHAR, period_start DATE, period_end DATE, "
            "fiscal_year INTEGER, fiscal_period VARCHAR, filed_at DATE, available_on DATE, "
            "is_amendment BOOLEAN, is_superseded BOOLEAN, provenance_json VARCHAR)"
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
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    self._observation_id(item),
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

    @staticmethod
    def _observation_id(item: FinancialObservation) -> str:
        identity = "|".join(
            str(value)
            for value in (
                item.filing.accession_number,
                item.metric,
                item.unit,
                item.period_start,
                item.period_end,
                item.value,
            )
        )
        return hashlib.sha256(identity.encode()).hexdigest()

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

    def store_estimate_snapshots(self, snapshots: Iterable[ConsensusEstimate]) -> None:
        """Append immutable normalized consensus observations; duplicates are ignored."""
        self.connection.execute(
            "CREATE TABLE IF NOT EXISTS estimate_snapshots ("
            "provider VARCHAR, provider_record_id VARCHAR, ticker VARCHAR, metric VARCHAR, "
            "target_period_end DATE, period_type VARCHAR, unit VARCHAR, currency VARCHAR, "
            "basis VARCHAR, "
            "consensus DOUBLE, analyst_count INTEGER, high DOUBLE, low DOUBLE, "
            "standard_deviation DOUBLE, "
            "source_identifier VARCHAR, published_at TIMESTAMP, retrieved_at TIMESTAMP, "
            "available_at TIMESTAMP, "
            "PRIMARY KEY(provider, provider_record_id), "
            "UNIQUE(ticker, metric, target_period_end, basis, available_at, provider))"
        )
        for item in snapshots:
            if not isinstance(item, ConsensusEstimate):
                raise TypeError("snapshots must contain ConsensusEstimate")
            existing = self.connection.execute(
                "SELECT * FROM estimate_snapshots WHERE provider = ? AND provider_record_id = ?",
                [item.provider, item.provider_record_id],
            ).fetchone()
            logical = self.connection.execute(
                "SELECT provider_record_id FROM estimate_snapshots WHERE ticker = ? AND metric = ? "
                "AND target_period_end = ? AND basis = ? AND available_at = ? AND provider = ?",
                [
                    item.ticker,
                    item.metric.value,
                    item.target_period_end,
                    item.basis.value,
                    item.available_at,
                    item.provider,
                ],
            ).fetchone()
            if existing is not None or logical is not None:
                if existing is not None and existing[0:2] == (
                    item.provider,
                    item.provider_record_id,
                ):
                    continue
                raise ValueError("conflicting immutable estimate snapshot")
            self.connection.execute(
                "INSERT INTO estimate_snapshots VALUES "
                "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    item.provider,
                    item.provider_record_id,
                    item.ticker,
                    item.metric.value,
                    item.target_period_end,
                    item.period_type.value,
                    item.unit,
                    item.currency,
                    item.basis.value,
                    item.consensus,
                    item.analyst_count,
                    item.high,
                    item.low,
                    item.standard_deviation,
                    item.source_identifier,
                    item.published_at,
                    item.retrieved_at,
                    item.available_at,
                ],
            )

    def load_estimate_snapshots(self) -> list[ConsensusEstimate]:
        """Read persisted snapshots for deterministic round-trip verification."""
        rows = self.connection.execute(
            "SELECT * FROM estimate_snapshots ORDER BY available_at, provider_record_id"
        ).fetchall()
        names = [item[0] for item in self.connection.description]
        return [
            ConsensusEstimate.model_validate(dict(zip(names, row, strict=True))) for row in rows
        ]
