"""Feature assembly boundary: providers supply data; signals only consume it."""

from __future__ import annotations

from datetime import datetime

from smct_research.core.models import FeatureSnapshot
from smct_research.screening.models import FeatureAssemblyInput


class FeatureSnapshotAssembler:
    def assemble(
        self, evidence: FeatureAssemblyInput, as_of: datetime | None = None
    ) -> FeatureSnapshot:
        """Construct a snapshot without network access or provider side effects."""
        return FeatureSnapshot(
            ticker=evidence.ticker,
            as_of=as_of or evidence.as_of,
            values=evidence.values,
            sources=evidence.sources,
            source_as_of=evidence.source_as_of,
        )
