from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum

from pydantic import BaseModel, Field, model_validator


class ReportingPeriodType(StrEnum):
    QUARTERLY = "quarterly"
    ANNUAL = "annual"
    YEAR_TO_DATE = "year_to_date"


class FilingMetadata(BaseModel):
    cik: str
    accession_number: str
    form: str
    filed_at: date
    accepted_at: datetime | None = None
    report_date: date | None = None
    is_amendment: bool = False
    is_superseded: bool = False


class Provenance(BaseModel):
    provider: str = "sec_edgar"
    source_url: str
    retrieved_at: datetime
    publication_date: date | None = None
    filing_date: date | None = None
    raw_document_id: str | None = None


class FinancialObservation(BaseModel):
    metric: str
    value: float
    unit: str
    currency: str | None = "USD"
    period_type: ReportingPeriodType
    period_start: date | None = None
    period_end: date
    fiscal_year: int | None = None
    fiscal_period: str | None = None
    filing: FilingMetadata
    provenance: Provenance

    @model_validator(mode="after")
    def validate_period(self) -> FinancialObservation:
        if self.period_start is not None and self.period_start > self.period_end:
            raise ValueError("period_start cannot be after period_end")
        if self.provenance.filing_date is None:
            self.provenance.filing_date = self.filing.filed_at
        if self.provenance.publication_date is None:
            self.provenance.publication_date = self.filing.filed_at
        return self

    @property
    def available_on(self) -> date:
        return self.provenance.publication_date or self.filing.filed_at


class FeatureValue(BaseModel):
    name: str
    value: float | None
    as_of: date
    available_on: date
    quality_score: float = Field(ge=0, le=1)
    source_accessions: list[str] = Field(default_factory=list)
    period_start: date | None = None
    period_end: date | None = None
