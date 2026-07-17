"""Feature assembly boundary: providers supply data; signals only consume it."""

from __future__ import annotations

from datetime import datetime

from smct_research.core.models import FeatureSnapshot, normalize_utc
from smct_research.screening.models import FeatureAssemblyInput


class FeatureSnapshotAssembler:
    def assemble(
        self, evidence: FeatureAssemblyInput, as_of: datetime | None = None
    ) -> FeatureSnapshot:
        """Construct a snapshot without network access or provider side effects."""
        requested_as_of = normalize_utc(as_of) if as_of else evidence.as_of
        availability = [evidence.as_of, *evidence.source_as_of.values()]
        latest_available = max(availability)
        if requested_as_of < latest_available:
            raise ValueError("Feature snapshot as_of cannot precede evidence availability")
        return FeatureSnapshot(
            ticker=evidence.ticker,
            as_of=requested_as_of,
            values=evidence.values,
            sources=evidence.sources,
            source_as_of=evidence.source_as_of,
        )
