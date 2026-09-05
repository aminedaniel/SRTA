# Research workflow architecture

The research workflow builds on the immutable domain models from PR #14.

- `screening.registry` centralizes the ten signals; both evaluation entry points use it.
- `screening.configuration` validates shared universe filters and real signal-ID weights.
- `research.builder` converts a ranked result plus its exact input snapshot into an
  immutable report. Contributions reconcile to the composite score. No missing
  signal is synthesized and future evidence is not shown as an available valuation.
- `research.render` emits portable Markdown. JSON retains the complete schema.
- `research.store` uses SQLite transactions for immutable reports, full screening
  runs and append-only thesis revisions. The existing DuckDB store retains the
  analytical evidence. Revision checks prevent lost thesis updates.
- `research.thesis` defines user-authored catalysts and numeric invalidation
  conditions. Alerts never silently change thesis status.
- `research.changes` compares full runs. Display filters are applied after the
  complete run is stored, so top-N views cannot accidentally remove history.
- `financials.ingest` is the SEC ingestion boundary. Signals remain offline and
  deterministic; unavailable external feeds do not become fabricated observations.

Q1 financial quality adds an explicit operating-evidence dimension. It combines
revenue growth scaled to 25%, operating margin scaled to 20%, and, when available,
FCF margin scaled to 20%, diluted share growth relative to 2%, and SBC/revenue
relative to 10%. Each component is clipped to [-1, 1]; their average is multiplied
by 50. Optional omissions reduce the number of components and confidence.
These are transparent research heuristics and are not a calibrated return model.

## Release boundaries

This release completes the local report, thesis and monitoring workflow and adds
a working SEC import path. It does not enable brokerage execution, automatically
acquire every alternative-data feed, license market data, build a historical
universe or assert that the signals have passed long-horizon investment validation.
See QUICKSTART for the supported operating steps and input limitations.
