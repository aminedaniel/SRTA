from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum

from pydantic import BaseModel, Field


class RevisionStatus(StrEnum):
    INITIAL = "initial"
    REVISED = "revised"
    UNKNOWN = "unknown"


class MacroObservation(BaseModel):
    series_id: str
    observation_date: date
    available_on: date
    vintage_date: date | None = None
    retrieved_at: datetime
    value: float
    unit: str
    source: str
    revision_status: RevisionStatus = RevisionStatus.UNKNOWN
    frequency: str
    provenance_url: str


class FedBalanceSheetRelease(BaseModel):
    release_date: date
    publication_timestamp: datetime
    source_url: str
    document_hash: str
    observations: list[MacroObservation]


class FedTextDocument(BaseModel):
    document_type: str
    publication_timestamp: datetime
    source_url: str
    document_hash: str
    title: str | None = None


class MacroFeature(BaseModel):
    name: str
    value: float | str | None
    available_on: date
    quality_score: float = Field(ge=0, le=1)
    source_series: list[str] = Field(default_factory=list)
