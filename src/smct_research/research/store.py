"""Transactional local research history. Analytical observations remain in DuckDB."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from smct_research.core.models import normalize_utc
from smct_research.research.builder import canonical_json, report_id
from smct_research.research.models import CompanyResearchReport
from smct_research.research.thesis import ThesisDocument


class ResearchStore:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(path)
        self.connection.execute("PRAGMA foreign_keys = ON")
        self.connection.executescript("""
            CREATE TABLE IF NOT EXISTS reports (
                id TEXT PRIMARY KEY, ticker TEXT NOT NULL, as_of TEXT NOT NULL, payload TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS reports_ticker_time ON reports(ticker, as_of);
            CREATE TABLE IF NOT EXISTS runs (
                id TEXT PRIMARY KEY, as_of TEXT NOT NULL, payload TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS thesis_revisions (
                ticker TEXT NOT NULL, revision INTEGER NOT NULL, recorded_at TEXT NOT NULL,
                payload TEXT NOT NULL, PRIMARY KEY(ticker, revision));
        """)

    def __enter__(self) -> ResearchStore:
        return self

    def __exit__(self, *args: object) -> None:
        self.connection.close()

    def save_run(
        self, reports: list[CompanyResearchReport], as_of: datetime, policy: dict[str, Any]
    ) -> str:
        as_of = normalize_utc(as_of)
        if len({r.company.ticker for r in reports}) != len(reports):
            raise ValueError("A research run cannot contain duplicate tickers")
        if any(r.as_of != as_of for r in reports):
            raise ValueError("Reports must share the run's evaluation timestamp")
        payload = {
            "as_of": as_of.isoformat(),
            "policy": policy,
            "report_ids": [report_id(r) for r in reports],
        }
        serialized = canonical_json(payload)
        identity = hashlib.sha256(serialized.encode()).hexdigest()
        with self.connection:
            for report in reports:
                self.connection.execute(
                    "INSERT OR IGNORE INTO reports VALUES (?, ?, ?, ?)",
                    (
                        report_id(report),
                        report.company.ticker,
                        report.as_of.isoformat(),
                        canonical_json(report.model_dump(mode="json")),
                    ),
                )
            self.connection.execute(
                "INSERT OR IGNORE INTO runs VALUES (?, ?, ?)",
                (identity, as_of.isoformat(), serialized),
            )
        return identity

    def report(self, ticker: str, as_of: datetime | None = None) -> CompanyResearchReport:
        limit = normalize_utc(as_of).isoformat() if as_of else "9999"
        row = self.connection.execute(
            "SELECT payload FROM reports WHERE ticker=? AND as_of<=? ORDER BY as_of DESC, rowid DESC LIMIT 1",
            (ticker.upper().strip(), limit),
        ).fetchone()
        if row is None:
            raise ValueError(f"No saved report for {ticker.upper()} at the requested time")
        return CompanyResearchReport.model_validate_json(row[0])

    def run(self, as_of: datetime | None = None) -> dict[str, Any]:
        limit = normalize_utc(as_of).isoformat() if as_of else "9999"
        row = self.connection.execute(
            "SELECT id, payload FROM runs WHERE as_of<=? ORDER BY as_of DESC, rowid DESC LIMIT 1",
            (limit,),
        ).fetchone()
        if row is None:
            raise ValueError("No saved research run at the requested time")
        value: dict[str, Any] = json.loads(row[1])
        value["id"] = row[0]
        value["reports"] = [
            CompanyResearchReport.model_validate_json(
                self.connection.execute(
                    "SELECT payload FROM reports WHERE id=?", (key,)
                ).fetchone()[0]
            )
            for key in value["report_ids"]
        ]
        return value

    def save_thesis(self, thesis: ThesisDocument) -> ThesisDocument:
        """Compare-and-swap revision; a stale editor cannot overwrite newer research."""
        try:
            self.connection.execute("BEGIN IMMEDIATE")
            current = self.connection.execute(
                "SELECT revision, payload FROM thesis_revisions WHERE ticker=? ORDER BY revision DESC LIMIT 1",
                (thesis.ticker,),
            ).fetchone()
            revision = current[0] if current else 0
            if thesis.revision != revision:
                raise ValueError(
                    f"Thesis revision conflict: file has {thesis.revision}, current is {revision}"
                )
            if (
                thesis.report_id
                and not self.connection.execute(
                    "SELECT 1 FROM reports WHERE id=? AND ticker=?",
                    (thesis.report_id, thesis.ticker),
                ).fetchone()
            ):
                raise ValueError("Thesis report_id must reference a saved report for this ticker")
            payload = thesis.model_dump()
            payload.update(revision=revision + 1, updated_at=datetime.now(UTC))
            if current:
                payload["created_at"] = json.loads(current[1])["created_at"]
            saved = ThesisDocument.model_validate(payload)
            self.connection.execute(
                "INSERT INTO thesis_revisions VALUES (?, ?, ?, ?)",
                (
                    saved.ticker,
                    saved.revision,
                    saved.updated_at.isoformat(),
                    saved.model_dump_json(),
                ),
            )
            self.connection.commit()
            return saved
        except Exception:
            self.connection.rollback()
            raise

    def thesis(self, ticker: str, revision: int | None = None) -> ThesisDocument:
        row = self.connection.execute(
            "SELECT payload FROM thesis_revisions WHERE ticker=? AND revision<=? ORDER BY revision DESC LIMIT 1",
            (ticker.upper().strip(), revision if revision is not None else 2**31),
        ).fetchone()
        if row is None:
            raise ValueError(f"No saved thesis for {ticker.upper()}")
        result = ThesisDocument.model_validate_json(row[0])
        if revision is not None and result.revision != revision:
            raise ValueError("Requested thesis revision does not exist")
        return result

    def theses(self) -> list[ThesisDocument]:
        tickers = self.connection.execute(
            "SELECT DISTINCT ticker FROM thesis_revisions ORDER BY ticker"
        ).fetchall()
        return [self.thesis(row[0]) for row in tickers]
