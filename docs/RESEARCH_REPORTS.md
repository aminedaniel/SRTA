# Company research reports and thesis lifecycle

SMCT research reports turn the offline ranked screening workflow into a deterministic, point-in-time company report and an append-only thesis record. Reports prioritize research and stock-selection diligence; they are not automated trade instructions, brokerage actions, or buy/sell recommendations.

## Deterministic synthesis policy

The report builder reuses the universe loader, feature snapshots, registered signals, composite scorer, and ranked result. It does not call providers, networks, LLMs, brokerages, or external APIs. Identical canonical inputs produce stable JSON, Markdown, report IDs, evidence ordering, warning ordering, and content hashes.

## Report schema

`CompanyResearchReport` includes schema version, stable report ID, company metadata, `as_of`, eligibility, rank, composite score and confidence, feature completeness, executive summary, variant perception, signal assessments, evidence, risks, catalysts, invalidation conditions, valuation summary, missing evidence, unavailable signals, stale and point-in-time warnings, provenance, source timestamps, and a candidate `ResearchThesis`.

`SignalAssessment` records signal score, confidence, direction, weighted contribution, thesis, evidence, risks, metadata, availability, stale warnings, and point-in-time warnings. `ValuationSummary` preserves unavailable valuation fields as missing rather than converting them to zero.

## Evidence ordering

Evidence is ordered by `signal score × signal confidence × configured composite weight`. Positive signals support the thesis. Negative signals and risks appear as contradictory evidence or key risks. Neutral signals remain context. Unavailable signals remain in missing-evidence sections.

## Point-in-time behavior

Reports surface stale source timestamps and future-dated source warnings from the screening workflow. Thesis as-of reads only return versions where both `known_at <= query_as_of` and `effective_at <= query_as_of`, so later revisions do not leak into historical queries.

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

## Append-only persistence

DuckDB tables `research_reports` and `research_thesis_versions` store immutable JSON payloads and content hashes. Exact duplicate writes are idempotent. Conflicting immutable records, ticker mixing for one thesis ID, skipped versions, and incorrect prior-version references raise controlled errors.

## CLI examples

```bash
smct report examples/screening/universe.json examples/screening/features \
  --ticker ACME --as-of 2026-07-17T00:00:00Z \
  --output-json /tmp/acme-report.json --output-markdown /tmp/acme-report.md \
  --database /tmp/research.duckdb --save-report --create-thesis
```

```bash
smct thesis-list /tmp/research.duckdb --ticker ACME
smct thesis-show /tmp/research.duckdb --ticker ACME --as-of 2026-07-17T00:00:00Z
smct thesis-transition /tmp/research.duckdb --ticker ACME --status active \
  --reason "Initial research review completed" \
  --effective-at 2026-07-18T00:00:00Z --known-at 2026-07-18T00:00:00Z
```

## Limitations

The workflow does not track catalysts over time, produce weekly watchlist change reports, or implement a validation framework in this milestone.
