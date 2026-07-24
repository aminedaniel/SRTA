from __future__ import annotations

import hashlib
import json
from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel

from smct_research.core.models import ThesisStatus, normalize_utc
from smct_research.research.models import CompanyResearchReport, ThesisRecord

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


THESIS_ORDERED_LIST_PATHS = {
    ("thesis", "supporting_evidence"),
    ("thesis", "key_risks"),
    ("thesis", "invalidation_conditions"),
}


def _thesis_canonicalize(value: Any, path: tuple[str, ...] = ()) -> Any:
    if isinstance(value, BaseModel):
        return _thesis_canonicalize(value.model_dump(mode="python"), path)
    if isinstance(value, datetime):
        return normalize_utc(value).isoformat().replace("+00:00", "Z")
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {
            str(key): _thesis_canonicalize(item, (*path, str(key)))
            for key, item in sorted(value.items(), key=lambda entry: str(entry[0]))
        }
    if isinstance(value, (list, tuple, set, frozenset)):
        items = [_thesis_canonicalize(item, path) for item in value]
        if path in THESIS_ORDERED_LIST_PATHS:
            return items
        return sorted(
            items,
            key=lambda item: (
                type(item).__name__,
                json.dumps(item, sort_keys=True, separators=(",", ":"), ensure_ascii=False),
            ),
        )
    return value


def thesis_record_payload(record_or_data: ThesisRecord | dict[str, object]) -> dict[str, object]:
    data = (
        record_or_data.model_dump(mode="python")
        if isinstance(record_or_data, ThesisRecord)
        else dict(record_or_data)
    )
    data.pop("canonical_content_hash", None)
    return _thesis_canonicalize(data)


def thesis_record_hash(record_or_data: ThesisRecord | dict[str, object]) -> str:
    payload = thesis_record_payload(record_or_data)
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()


def _record_hash(data: dict[str, object]) -> str:
    return thesis_record_hash(data)


def validate_thesis_record_hash(record: ThesisRecord) -> None:
    if record.canonical_content_hash != thesis_record_hash(record):
        raise ValueError("thesis record content hash mismatch")


def create_initial_thesis_record(
    report: CompanyResearchReport, created_at: datetime | None = None
) -> ThesisRecord:
    now = normalize_utc(created_at or report.as_of)
    thesis = report.candidate_thesis.model_copy(
        update={"status": ThesisStatus.DRAFT, "created_at": now, "updated_at": now}
    )
    reason = "Initial draft thesis created from deterministic company research report."
    data: dict[str, object] = {
        "schema_version": "thesis_record.v1",
        "thesis_id": stable_thesis_id(report.ticker, report.report_id, thesis.title),
        "version": 1,
        "ticker": report.ticker,
        "thesis": thesis,
        "status": ThesisStatus.DRAFT,
        "effective_at": report.as_of,
        "known_at": report.as_of,
        "source_report_id": report.report_id,
        "source_report_as_of": report.as_of,
        "revision_reason": reason,
        "prior_version": None,
        "created_at": now,
        "updated_at": now,
    }
    data["canonical_content_hash"] = _record_hash(data)
    return ThesisRecord.model_validate(data)


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
    if eff < record.effective_at:
        raise ValueError("effective_at cannot move backward")
    if known < record.known_at:
        raise ValueError("known_at cannot move backward")
    if known < eff:
        raise ValueError("known_at must be greater than or equal to effective_at")
    thesis = record.thesis.model_copy(update={"status": status, "updated_at": known})
    version = record.version + 1
    data: dict[str, object] = {
        "schema_version": "thesis_record.v1",
        "thesis_id": record.thesis_id,
        "version": version,
        "ticker": record.ticker,
        "thesis": thesis,
        "status": status,
        "effective_at": eff,
        "known_at": known,
        "source_report_id": record.source_report_id,
        "source_report_as_of": record.source_report_as_of,
        "revision_reason": reason,
        "prior_version": record.version,
        "created_at": record.created_at,
        "updated_at": known,
    }
    data["canonical_content_hash"] = _record_hash(data)
    return ThesisRecord.model_validate(data)
