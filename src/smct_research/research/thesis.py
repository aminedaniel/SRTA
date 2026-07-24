from __future__ import annotations

import hashlib
from datetime import datetime

from smct_research.core.models import ResearchThesis, ThesisStatus, normalize_utc
from smct_research.research.models import CompanyResearchReport, ThesisRecord, content_hash

TERMINAL_STATUSES = {ThesisStatus.INVALIDATED, ThesisStatus.FULLY_PRICED}
ALLOWED_TRANSITIONS: dict[ThesisStatus, set[ThesisStatus]] = {
    ThesisStatus.DRAFT: {ThesisStatus.ACTIVE},
    ThesisStatus.ACTIVE: {
        ThesisStatus.STRENGTHENING,
        ThesisStatus.WEAKENING,
        ThesisStatus.INVALIDATED,
        ThesisStatus.FULLY_PRICED,
    },
    ThesisStatus.STRENGTHENING: {
        ThesisStatus.ACTIVE,
        ThesisStatus.WEAKENING,
        ThesisStatus.INVALIDATED,
        ThesisStatus.FULLY_PRICED,
    },
    ThesisStatus.WEAKENING: {
        ThesisStatus.ACTIVE,
        ThesisStatus.STRENGTHENING,
        ThesisStatus.INVALIDATED,
        ThesisStatus.FULLY_PRICED,
    },
    ThesisStatus.INVALIDATED: set(),
    ThesisStatus.FULLY_PRICED: set(),
}


def stable_thesis_id(ticker: str, report_id: str, title: str) -> str:
    raw = f"{ticker.upper().strip()}|{report_id}|{title}"
    return "thesis_" + hashlib.sha256(raw.encode()).hexdigest()[:24]


def _record_hash(thesis: ResearchThesis, status: ThesisStatus, version: int, reason: str) -> str:
    return content_hash(
        {
            "thesis": thesis.model_dump(mode="json"),
            "status": status.value,
            "version": version,
            "revision_reason": reason,
        }
    )


def create_initial_thesis_record(
    report: CompanyResearchReport, created_at: datetime | None = None
) -> ThesisRecord:
    now = normalize_utc(created_at or report.as_of)
    thesis = report.candidate_thesis.model_copy(
        update={"status": ThesisStatus.DRAFT, "created_at": now, "updated_at": now}
    )
    reason = "Initial draft thesis created from deterministic company research report."
    return ThesisRecord(
        thesis_id=stable_thesis_id(report.ticker, report.report_id, thesis.title),
        version=1,
        ticker=report.ticker,
        thesis=thesis,
        status=ThesisStatus.DRAFT,
        effective_at=report.as_of,
        known_at=report.as_of,
        source_report_id=report.report_id,
        source_report_as_of=report.as_of,
        revision_reason=reason,
        prior_version=None,
        canonical_content_hash=_record_hash(thesis, ThesisStatus.DRAFT, 1, reason),
        created_at=now,
        updated_at=now,
    )


def transition_thesis(
    record: ThesisRecord,
    status: ThesisStatus,
    reason: str,
    effective_at: datetime,
    known_at: datetime,
) -> ThesisRecord:
    if not reason.strip():
        raise ValueError("revision reason is required")
    if record.status in TERMINAL_STATUSES:
        raise ValueError(f"cannot transition terminal thesis status {record.status.value}")
    if status not in ALLOWED_TRANSITIONS[record.status]:
        raise ValueError(f"invalid thesis transition: {record.status.value} -> {status.value}")
    eff = normalize_utc(effective_at)
    known = normalize_utc(known_at)
    thesis = record.thesis.model_copy(update={"status": status, "updated_at": known})
    version = record.version + 1
    return ThesisRecord(
        thesis_id=record.thesis_id,
        version=version,
        ticker=record.ticker,
        thesis=thesis,
        status=status,
        effective_at=eff,
        known_at=known,
        source_report_id=record.source_report_id,
        source_report_as_of=record.source_report_as_of,
        revision_reason=reason,
        prior_version=record.version,
        canonical_content_hash=_record_hash(thesis, status, version, reason),
        created_at=record.created_at,
        updated_at=known,
    )
