"""Local-only DuckDB persistence for raw evidence and immutable observations."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from smct_research.developer_ecosystem.models import (
        PackageObservation,
        RepositoryMapping,
        RepositoryObservation,
    )
from smct_research.developer_ecosystem.models import (
    PackageObservation,
    RepositoryMapping,
    RepositoryObservation,
)
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

    def _ensure_estimate_table(self) -> None:
        self.connection.execute(
            "CREATE TABLE IF NOT EXISTS estimate_snapshots ("
            "provider VARCHAR, provider_record_id VARCHAR, ticker VARCHAR, metric VARCHAR, "
            "target_period_end DATE, period_type VARCHAR, horizon_label VARCHAR, unit VARCHAR, "
            "currency VARCHAR, basis VARCHAR, consensus DOUBLE, analyst_count INTEGER, "
            "high DOUBLE, "
            "low DOUBLE, standard_deviation DOUBLE, estimate_breadth DOUBLE, "
            "source_identifier VARCHAR, "
            "published_at TIMESTAMP, retrieved_at TIMESTAMP, available_at TIMESTAMP, "
            "PRIMARY KEY(provider, provider_record_id), "
            "UNIQUE(ticker, metric, target_period_end, period_type, unit, currency, basis, "
            "available_at, provider))"
        )

    @staticmethod
    def _estimate_row(item: ConsensusEstimate) -> list[Any]:
        return [
            item.provider,
            item.provider_record_id,
            item.ticker,
            item.metric.value,
            item.target_period_end,
            item.period_type.value,
            item.horizon_label,
            item.unit,
            item.currency,
            item.basis.value,
            item.consensus,
            item.analyst_count,
            item.high,
            item.low,
            item.standard_deviation,
            item.estimate_breadth,
            item.source_identifier,
            item.published_at,
            item.retrieved_at,
            item.available_at,
        ]

    def store_estimate_snapshots(self, snapshots: Iterable[ConsensusEstimate]) -> None:
        """Append exact duplicates only; conflicting immutable records raise ValueError."""
        self._ensure_estimate_table()
        for item in snapshots:
            if not isinstance(item, ConsensusEstimate):
                raise TypeError("snapshots must contain ConsensusEstimate")
            existing = self.connection.execute(
                "SELECT * FROM estimate_snapshots WHERE provider = ? AND provider_record_id = ?",
                [item.provider, item.provider_record_id],
            ).fetchone()
            if existing is not None:
                names = [column[0] for column in self.connection.description]
                stored = ConsensusEstimate.model_validate(dict(zip(names, existing, strict=True)))
                if stored.model_dump(mode="json") == item.model_dump(mode="json"):
                    continue
                raise ValueError("conflicting immutable provider record ID")
            logical = self.connection.execute(
                "SELECT provider_record_id FROM estimate_snapshots WHERE ticker=? AND metric=? "
                "AND target_period_end=? AND period_type=? AND unit=? AND currency=? AND basis=? "
                "AND available_at=? AND provider=?",
                [
                    item.ticker,
                    item.metric.value,
                    item.target_period_end,
                    item.period_type.value,
                    item.unit,
                    item.currency,
                    item.basis.value,
                    item.available_at,
                    item.provider,
                ],
            ).fetchone()
            if logical is not None:
                raise ValueError("conflicting logical estimate snapshot")
            self.connection.execute(
                "INSERT INTO estimate_snapshots VALUES "
                "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                self._estimate_row(item),
            )

    def load_estimate_snapshots(self) -> list[ConsensusEstimate]:
        """Read persisted snapshots for deterministic full-field round-trip verification."""
        self._ensure_estimate_table()
        cursor = self.connection.execute(
            "SELECT * FROM estimate_snapshots ORDER BY available_at, provider, provider_record_id"
        )
        rows = cursor.fetchall()
        names = [item[0] for item in cursor.description]
        return [
            ConsensusEstimate.model_validate(dict(zip(names, row, strict=True))) for row in rows
        ]


def _json_identity(item: object) -> str:
    payload = item.model_dump(mode="json")  # type: ignore[attr-defined]
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def _store_immutable_json(
    self: LocalAnalyticalStore,
    table: str,
    provider: str,
    provider_record_id: str,
    logical_id: str,
    payload: str,
) -> None:
    self.connection.execute(
        f"CREATE TABLE IF NOT EXISTS {table} (provider VARCHAR, provider_record_id VARCHAR, logical_id VARCHAR, payload_json VARCHAR, PRIMARY KEY(provider, provider_record_id), UNIQUE(provider, logical_id))"
    )
    existing = self.connection.execute(
        f"SELECT payload_json FROM {table} WHERE provider=? AND provider_record_id=?",
        [provider, provider_record_id],
    ).fetchone()
    if existing is not None:
        if existing[0] == payload:
            return
        raise ValueError("conflicting immutable provider record ID")
    logical = self.connection.execute(
        f"SELECT payload_json FROM {table} WHERE provider=? AND logical_id=?",
        [provider, logical_id],
    ).fetchone()
    if logical is not None and logical[0] != payload:
        raise ValueError("conflicting logical developer ecosystem record")
    self.connection.execute(
        f"INSERT INTO {table} VALUES (?, ?, ?, ?)",
        [provider, provider_record_id, logical_id, payload],
    )


def store_repository_observations(
    self: LocalAnalyticalStore, observations: Iterable[RepositoryObservation]
) -> None:
    for item in observations:
        payload = item.model_dump_json()
        logical = f"{item.repository_id}|{item.observation_window_start.isoformat()}|{item.observation_window_end.isoformat()}|{item.available_at.isoformat()}"
        _store_immutable_json(
            self,
            "developer_repository_observations",
            item.provider,
            item.provider_record_id,
            logical,
            payload,
        )


def load_repository_observations(self: LocalAnalyticalStore) -> list[RepositoryObservation]:
    self.connection.execute(
        "CREATE TABLE IF NOT EXISTS developer_repository_observations (provider VARCHAR, provider_record_id VARCHAR, logical_id VARCHAR, payload_json VARCHAR, PRIMARY KEY(provider, provider_record_id), UNIQUE(provider, logical_id))"
    )
    rows = self.connection.execute(
        "SELECT payload_json FROM developer_repository_observations ORDER BY provider, provider_record_id"
    ).fetchall()
    return [RepositoryObservation.model_validate_json(row[0]) for row in rows]


def store_package_observations(
    self: LocalAnalyticalStore, observations: Iterable[PackageObservation]
) -> None:
    for item in observations:
        payload = item.model_dump_json()
        logical = f"{item.ecosystem}|{item.package_name}|{item.observation_window_start.isoformat()}|{item.observation_window_end.isoformat()}|{item.available_at.isoformat()}"
        _store_immutable_json(
            self,
            "developer_package_observations",
            item.provider,
            item.provider_record_id,
            logical,
            payload,
        )


def load_package_observations(self: LocalAnalyticalStore) -> list[PackageObservation]:
    self.connection.execute(
        "CREATE TABLE IF NOT EXISTS developer_package_observations (provider VARCHAR, provider_record_id VARCHAR, logical_id VARCHAR, payload_json VARCHAR, PRIMARY KEY(provider, provider_record_id), UNIQUE(provider, logical_id))"
    )
    rows = self.connection.execute(
        "SELECT payload_json FROM developer_package_observations ORDER BY provider, provider_record_id"
    ).fetchall()
    return [PackageObservation.model_validate_json(row[0]) for row in rows]


def store_repository_mappings(
    self: LocalAnalyticalStore, mappings: Iterable[RepositoryMapping]
) -> None:
    for item in mappings:
        payload = item.model_dump_json()
        provider_record_id = _json_identity(item)
        logical = f"{item.ticker}|{item.provider}|{item.organization}|{item.owner}|{item.name}|{item.repository_id}|{item.effective_from.isoformat()}|{item.known_at.isoformat()}"
        _store_immutable_json(
            self,
            "developer_repository_mappings",
            item.provider,
            provider_record_id,
            logical,
            payload,
        )


def load_repository_mappings(self: LocalAnalyticalStore) -> list[RepositoryMapping]:
    self.connection.execute(
        "CREATE TABLE IF NOT EXISTS developer_repository_mappings (provider VARCHAR, provider_record_id VARCHAR, logical_id VARCHAR, payload_json VARCHAR, PRIMARY KEY(provider, provider_record_id), UNIQUE(provider, logical_id))"
    )
    rows = self.connection.execute(
        "SELECT payload_json FROM developer_repository_mappings ORDER BY provider, provider_record_id"
    ).fetchall()
    return [RepositoryMapping.model_validate_json(row[0]) for row in rows]


LocalAnalyticalStore.store_repository_observations = store_repository_observations  # type: ignore[attr-defined]
LocalAnalyticalStore.load_repository_observations = load_repository_observations  # type: ignore[attr-defined]
LocalAnalyticalStore.store_package_observations = store_package_observations  # type: ignore[attr-defined]
LocalAnalyticalStore.load_package_observations = load_package_observations  # type: ignore[attr-defined]
LocalAnalyticalStore.store_repository_mappings = store_repository_mappings  # type: ignore[attr-defined]
LocalAnalyticalStore.load_repository_mappings = load_repository_mappings  # type: ignore[attr-defined]
