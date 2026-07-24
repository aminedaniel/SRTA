"""Local-only DuckDB persistence for raw evidence and immutable observations."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from datetime import UTC, datetime
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
    canonical_mapping_identity,
    canonical_package_series_identity,
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
        logical = "|".join(canonical_package_series_identity(item, include_interval=True))
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
        payload = canonical_mapping_identity(item)
        provider_record_id = hashlib.sha256(payload.encode()).hexdigest()
        logical = payload
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

# Research report and append-only thesis persistence.
from smct_research.research.models import (  # noqa: E402
    CompanyResearchReport,
    ThesisRecord,
    validate_report_identity,
)
from smct_research.research.thesis import (  # noqa: E402
    ALLOWED_TRANSITIONS,
    TERMINAL_STATUSES,
    thesis_record_hash,
    validate_thesis_record_hash,
)


def _ensure_research_tables(self: LocalAnalyticalStore) -> None:
    self.connection.execute(
        "CREATE TABLE IF NOT EXISTS research_reports (report_id VARCHAR PRIMARY KEY, ticker VARCHAR, report_as_of TIMESTAMP, schema_version VARCHAR, content_hash VARCHAR, payload_json VARCHAR)"
    )
    self.connection.execute(
        "CREATE TABLE IF NOT EXISTS research_thesis_versions (thesis_id VARCHAR, version INTEGER, ticker VARCHAR, status VARCHAR, effective_at TIMESTAMP, known_at TIMESTAMP, source_report_id VARCHAR, prior_version INTEGER, revision_reason VARCHAR, content_hash VARCHAR, payload_json VARCHAR, PRIMARY KEY(thesis_id, version))"
    )


def store_research_report(self: LocalAnalyticalStore, report: CompanyResearchReport) -> None:
    _ensure_research_tables(self)
    report = CompanyResearchReport.model_validate(report.model_dump(mode="python"))
    validate_report_identity(report)
    payload = report.model_dump_json()
    existing = self.connection.execute(
        "SELECT content_hash, payload_json FROM research_reports WHERE report_id=?",
        [report.report_id],
    ).fetchone()
    if existing:
        if existing[0] == report.canonical_content_hash and existing[1] == payload:
            return
        raise ValueError("conflicting immutable research report")
    self.connection.execute(
        "INSERT INTO research_reports VALUES (?, ?, ?, ?, ?, ?)",
        [
            report.report_id,
            report.ticker,
            report.as_of,
            report.schema_version,
            report.canonical_content_hash,
            payload,
        ],
    )


def _validate_loaded_report(row: tuple[str, str] | None) -> CompanyResearchReport | None:
    if row is None:
        return None
    stored_hash, payload = row
    report = CompanyResearchReport.model_validate_json(payload)
    if stored_hash != report.canonical_content_hash:
        raise ValueError("corrupt research report stored hash mismatch")
    validate_report_identity(report)
    return report


def load_research_report(
    self: LocalAnalyticalStore, report_id: str
) -> CompanyResearchReport | None:
    _ensure_research_tables(self)
    row = self.connection.execute(
        "SELECT content_hash, payload_json FROM research_reports WHERE report_id=?", [report_id]
    ).fetchone()
    return _validate_loaded_report(row)


def _require_source_report(
    self: LocalAnalyticalStore, record: ThesisRecord
) -> CompanyResearchReport:
    report = load_research_report(self, record.source_report_id)
    if report is None:
        raise ValueError("source research report does not exist")
    if report.ticker != record.ticker:
        raise ValueError("source report ticker does not match thesis ticker")
    if report.as_of != record.source_report_as_of:
        raise ValueError("source report as_of does not match thesis record")
    if report.as_of > record.known_at:
        raise ValueError("source report is not known by thesis known_at")
    return report


def _validate_thesis_for_store(self: LocalAnalyticalStore, record: ThesisRecord) -> None:
    validate_thesis_record_hash(record)
    _require_source_report(self, record)
    if record.version == 1:
        if record.status.value != "draft" or record.prior_version is not None:
            raise ValueError("initial thesis version must be draft without prior version")
        latest_existing = _latest_records_by_series(load_thesis_history(self, ticker=record.ticker))
        open_series = [item for item in latest_existing if item.status not in TERMINAL_STATUSES]
        if open_series:
            raise ValueError("cannot start a new thesis series while another series is nonterminal")
        return
    prior_row = self.connection.execute(
        "SELECT content_hash, payload_json FROM research_thesis_versions WHERE thesis_id=? AND version=?",
        [record.thesis_id, record.version - 1],
    ).fetchone()
    if prior_row is None:
        raise ValueError("preceding thesis version is missing")
    prior = _validate_loaded_thesis(prior_row)
    if prior.thesis_id != record.thesis_id or prior.ticker != record.ticker:
        raise ValueError("thesis identity changed across versions")
    if (
        record.source_report_id != prior.source_report_id
        or record.source_report_as_of != prior.source_report_as_of
    ):
        raise ValueError("source report cannot change within a thesis series")
    if record.prior_version != prior.version or record.version != prior.version + 1:
        raise ValueError("invalid thesis version sequence")
    if prior.status in TERMINAL_STATUSES:
        raise ValueError("cannot continue terminal thesis series")
    if record.status not in ALLOWED_TRANSITIONS[prior.status]:
        raise ValueError(
            f"invalid thesis transition: {prior.status.value} -> {record.status.value}"
        )
    if record.effective_at < prior.effective_at:
        raise ValueError("effective_at cannot move backward")
    if record.known_at < prior.known_at:
        raise ValueError("known_at cannot move backward")
    if record.updated_at < prior.updated_at:
        raise ValueError("updated_at cannot move backward")


def store_thesis_record(self: LocalAnalyticalStore, record: ThesisRecord) -> None:
    _ensure_research_tables(self)
    record = ThesisRecord.model_validate(record.model_dump(mode="python"))
    payload = record.model_dump_json()
    existing = self.connection.execute(
        "SELECT content_hash, payload_json FROM research_thesis_versions WHERE thesis_id=? AND version=?",
        [record.thesis_id, record.version],
    ).fetchone()
    if existing:
        if existing[0] == record.canonical_content_hash and existing[1] == payload:
            return
        raise ValueError("conflicting immutable thesis version")
    if record.version == 1:
        any_existing = self.connection.execute(
            "SELECT ticker FROM research_thesis_versions WHERE thesis_id=? LIMIT 1",
            [record.thesis_id],
        ).fetchone()
        if any_existing is not None:
            raise ValueError("cannot restart existing thesis series")
    _validate_thesis_for_store(self, record)
    self.connection.execute(
        "INSERT INTO research_thesis_versions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            record.thesis_id,
            record.version,
            record.ticker,
            record.status.value,
            record.effective_at,
            record.known_at,
            record.source_report_id,
            record.prior_version,
            record.revision_reason,
            record.canonical_content_hash,
            payload,
        ],
    )


def _validate_loaded_thesis(row: tuple[str, str]) -> ThesisRecord:
    stored_hash, payload = row
    record = ThesisRecord.model_validate_json(payload)
    if stored_hash != record.canonical_content_hash:
        raise ValueError("corrupt thesis stored hash mismatch")
    if thesis_record_hash(record) != record.canonical_content_hash:
        raise ValueError("corrupt thesis payload hash mismatch")
    return record


def _chronological_key(record: ThesisRecord) -> tuple[datetime, datetime, int]:
    return (record.known_at, record.effective_at, record.version)


def _history_sort_key(record: ThesisRecord) -> tuple[datetime, datetime, int, str]:
    return (*_chronological_key(record), record.thesis_id)


def _latest_records_by_series(records: list[ThesisRecord]) -> list[ThesisRecord]:
    latest: dict[str, ThesisRecord] = {}
    for record in sorted(records, key=_history_sort_key):
        latest[record.thesis_id] = record
    return sorted(latest.values(), key=_history_sort_key)


def load_thesis_history(
    self: LocalAnalyticalStore, thesis_id: str | None = None, ticker: str | None = None
) -> list[ThesisRecord]:
    _ensure_research_tables(self)
    ticker_norm = ticker.upper().strip() if ticker else None
    if thesis_id:
        rows = self.connection.execute(
            "SELECT content_hash, payload_json FROM research_thesis_versions WHERE thesis_id=?",
            [thesis_id],
        ).fetchall()
    elif ticker_norm:
        rows = self.connection.execute(
            "SELECT content_hash, payload_json FROM research_thesis_versions WHERE ticker=?",
            [ticker_norm],
        ).fetchall()
    else:
        rows = self.connection.execute(
            "SELECT content_hash, payload_json FROM research_thesis_versions"
        ).fetchall()
    records = [_validate_loaded_thesis(r) for r in rows]
    if ticker_norm is not None:
        mismatched = [record for record in records if record.ticker != ticker_norm]
        if mismatched:
            raise ValueError("ticker and thesis_id do not match")
    return sorted(records, key=_history_sort_key)


def _select_record(
    records: list[ThesisRecord], *, thesis_id: str | None, ticker: str | None
) -> ThesisRecord | None:
    if not records:
        return None
    records = sorted(records, key=_history_sort_key)
    latest_key = max(_chronological_key(record) for record in records)
    tied = [record for record in records if _chronological_key(record) == latest_key]
    series = {record.thesis_id for record in tied}
    if thesis_id is None and ticker is not None and len(series) > 1:
        raise ValueError("multiple thesis series are ambiguous; pass --thesis-id")
    return sorted(tied, key=_history_sort_key)[-1]


def load_latest_thesis(
    self: LocalAnalyticalStore, thesis_id: str | None = None, ticker: str | None = None
) -> ThesisRecord | None:
    return _select_record(
        load_thesis_history(self, thesis_id, ticker), thesis_id=thesis_id, ticker=ticker
    )


def load_thesis_as_of(
    self: LocalAnalyticalStore,
    query_as_of: datetime,
    thesis_id: str | None = None,
    ticker: str | None = None,
) -> ThesisRecord | None:
    asof = (
        query_as_of.replace(tzinfo=UTC)
        if query_as_of.tzinfo is None
        else query_as_of.astimezone(UTC)
    )
    records = [
        r
        for r in load_thesis_history(self, thesis_id, ticker)
        if r.known_at <= asof and r.effective_at <= asof
    ]
    return _select_record(records, thesis_id=thesis_id, ticker=ticker)


LocalAnalyticalStore.store_research_report = store_research_report  # type: ignore[attr-defined]
LocalAnalyticalStore.load_research_report = load_research_report  # type: ignore[attr-defined]
LocalAnalyticalStore.store_thesis_record = store_thesis_record  # type: ignore[attr-defined]
LocalAnalyticalStore.load_thesis_history = load_thesis_history  # type: ignore[attr-defined]
LocalAnalyticalStore.load_latest_thesis = load_latest_thesis  # type: ignore[attr-defined]
LocalAnalyticalStore.load_thesis_as_of = load_thesis_as_of  # type: ignore[attr-defined]
