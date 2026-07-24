# Company research reports and thesis lifecycle

SMCT research reports turn the offline ranked screening workflow into a deterministic, point-in-time company report and an append-only thesis record. Reports prioritize research and stock-selection diligence; they are not automated trade instructions, brokerage actions, or buy/sell recommendations.

## Deterministic synthesis policy

The report builder reuses the universe loader, feature snapshots, registered signals, composite scorer, and ranked result. It does not call providers, networks, LLMs, brokerages, or external APIs. Identical canonical inputs produce stable JSON, Markdown, report IDs, evidence ordering, warning ordering, and content hashes.

## Canonical report identity and hashes

A report's `report_id` and `canonical_content_hash` are derived from one canonical payload helper that excludes only `report_id` and `canonical_content_hash` themselves. The payload includes schema version, company identity, company name, universe metadata, `as_of`, eligibility, exclusion reasons, rank, score, confidence, feature completeness, narratives, signal assessments, supporting/contradictory/contextual evidence, risks, catalysts, invalidation conditions, valuation summary, missing evidence, unavailable signals, stale and point-in-time warnings, provenance, source timestamps, and the candidate thesis. Meaningful changes to evidence, risks, provenance, timestamps, valuation, catalysts, or narratives change both the content hash and report ID.

## Canonical versus ranked ordering

Semantically unordered fields are recursively canonicalized: dictionaries sort by key, nested metadata is canonicalized, unordered lists are sorted without collapsing type distinctions, timestamps are UTC normalized, and enums serialize to values. Signal assessments are sorted by `signal_id`. Supporting evidence, contradictory evidence, contextual evidence, and invalidation conditions preserve builder order; positive and negative evidence are ranked by weighted contribution, then signal ID, then stable evidence text. The report-specific JSON serializer preserves those ranked lists while sorting mapping keys and ending with a single newline. Catalysts are canonically sorted unless a future workflow explicitly ranks them.

## Report schema

`CompanyResearchReport` includes schema version, stable report ID, company metadata, `as_of`, eligibility, rank, composite score and confidence, feature completeness, executive summary, variant perception, signal assessments, supporting evidence, contradictory evidence, contextual evidence, risks, catalysts, invalidation conditions, valuation summary, missing evidence, unavailable signals, stale and point-in-time warnings, provenance, source timestamps, and a candidate `ResearchThesis`.

`SignalAssessment` records signal score, confidence, direction, weighted contribution, thesis, evidence, risks, metadata, availability, stale warnings, and point-in-time warnings. `ValuationSummary` preserves unavailable valuation fields as missing rather than converting them to zero.

## Unknown signal weights

The report layer never silently assigns an unknown signal a default composite weight. If an evaluated signal lacks a configured composite weight, the signal remains visible but its weighted contribution is `Unavailable`, and missing evidence includes a deterministic diagnostic such as `M1: composite weight unavailable`. Unknown-weight signals are excluded from contribution-ranked supporting and contradictory evidence until a weight is configured.

## Evidence ordering

Positive signals support the thesis. Negative signals and risks appear as contradictory evidence or key risks. Neutral signals populate contextual evidence. Unavailable signals remain in missing-evidence sections.

## Point-in-time behavior and timestamp progression

Reports surface stale source timestamps and future-dated source warnings from the screening workflow. Thesis transitions preserve independent `effective_at` and `known_at` values but reject regressions: `known_at` must not move backward, `effective_at` must not move backward, and `updated_at` must not move backward. A thesis version is visible only when both `known_at <= query_as_of` and `effective_at <= query_as_of`, so later revisions do not leak into historical queries.

## Missing evidence behavior

Missing snapshots, unavailable signals, signal diagnostics, missing valuation fields, stale evidence, and point-in-time warnings are preserved explicitly. Markdown uses `Missing` and `Unavailable` rather than ambiguous placeholders.

## Thesis lifecycle

Thesis versions are append-only `ThesisRecord` rows containing stable thesis ID, version, ticker, `ResearchThesis` payload, status, effective timestamp, known-at timestamp, source report ID and `as_of`, revision reason, prior version, content hash, and created/updated timestamps.

Allowed transitions are:

- `draft -> active`
- `active -> strengthening | weakening | invalidated | fully_priced`
- `strengthening -> active | weakening | invalidated | fully_priced`
- `weakening -> active | strengthening | invalidated | fully_priced`

`invalidated` and `fully_priced` are terminal. Every transition requires a nonempty revision reason.

## Append-only persistence and integrity checks

DuckDB tables `research_reports` and `research_thesis_versions` store immutable JSON payloads and content hashes. Exact duplicate writes are idempotent. Conflicting immutable records, ticker mixing for one thesis ID, skipped versions, incorrect prior-version references, corrupt hashes, invalid status transitions, terminal continuations, source-report swaps within an existing thesis series, internally inconsistent thesis payloads, and regressing timestamps raise controlled errors. Persistence recalculates report IDs, report hashes, thesis hashes, and lifecycle invariants rather than trusting self-asserted payload values.

## Source-report integrity

Every thesis version references a persisted report. Persistence verifies that the source report exists, report ticker matches thesis ticker, report `as_of` matches `source_report_as_of`, and the report was known by the thesis version. A transition retains the prior report and persistence enforces that `source_report_id` and `source_report_as_of` cannot change within a thesis series. Incorporating a new report into an existing thesis is intentionally left for a later explicitly tested feature.

## Multiple thesis series

A ticker may start a new thesis series only after every existing series for that ticker is terminal (`invalidated` or `fully_priced`). A ticker may have multiple thesis series after terminal completion. Ticker-only latest and as-of lookups use chronological ordering by `known_at`, `effective_at`, version, and thesis ID as a deterministic tie-breaker. If a query remains ambiguous, CLI users must pass `--thesis-id`. `thesis-list` always prints thesis IDs so separate series can be identified.

## CLI examples and controlled errors

```bash
smct report examples/screening/universe.json examples/screening/features \
  --ticker ACME --as-of 2026-07-17T00:00:00Z \
  --output-json /tmp/acme-report.json --output-markdown /tmp/acme-report.md \
  --database /tmp/research.duckdb --save-report --create-thesis
```

`--save-report` and `--create-thesis` require `--database`; `--create-thesis` persists the source report before storing the thesis. The command supports `--config` with the same universe policy configuration accepted by `smct screen`.

```bash
smct thesis-list /tmp/research.duckdb --ticker ACME --output-json /tmp/theses.json
smct thesis-show /tmp/research.duckdb --ticker ACME --as-of 2026-07-17T00:00:00Z
smct thesis-show /tmp/research.duckdb --thesis-id thesis_... --as-of 2026-07-17T00:00:00Z
smct thesis-transition /tmp/research.duckdb --ticker ACME --status active \
  --reason "Initial research review completed" \
  --effective-at 2026-07-18T00:00:00Z --known-at 2026-07-18T00:00:00Z
```

Controlled CLI errors are reported through Typer without raw tracebacks for missing tickers, missing snapshots, invalid timestamps, invalid transitions, terminal transitions, ambiguous series, and persistence-integrity failures.

## Limitations

The workflow does not track catalysts over time, produce weekly watchlist change reports, or implement a validation framework in this milestone.
