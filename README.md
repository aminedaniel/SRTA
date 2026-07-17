# SMCT Research

A systematic research platform for discovering underfollowed and potentially mispriced small- and mid-cap technology companies over a 24–36 month investment horizon.

## Current status

The initial implementation includes:

- canonical company, feature, signal, and thesis models
- plugin-based signal registry
- small/mid-cap technology universe filter
- valuation-compression signal
- Reddit awareness/crowding signal
- congressional disclosed-purchasing signal
- SEC Form 4 open-market cluster-buying corroboration signal
- Renaissance Public Equity Activity (delayed SEC Form 13F corroboration)
- composite research-priority scorer
- CLI and tests

## Run locally

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
pytest
smct evaluate examples/sample_snapshot.json
```

See `docs/ARCHITECTURE.md`, `docs/ROADMAP.md`, and `docs/CONGRESSIONAL_DISCLOSURES.md` for the design and implementation sequence.

## Milestone 2 local financial evidence

The SEC EDGAR adapter is free-first: it downloads Company Facts and submission-history JSON with a
local disk cache, then stores raw metadata and immutable normalized observations in local DuckDB.
Feature snapshots can be exported to Parquet. Set an identifiable SEC user agent with contact email,
for example in PowerShell: `$env:SMCT_SEC_USER_AGENT = "SMCT Research you@example.com"`.
No network is required for the test suite; tests use local fixtures. Reddit remains optional and is
disabled by default in any ingestion workflow.


## Form 4 research evidence

The Form 4 pipeline ingests public `4` and `4/A` filings, retains only filed open-market
purchases (transaction code `P`, acquired shares, positive share count and price), and applies
the filing date as its point-in-time availability boundary. It deduplicates amended transactions
and uses insider purchase clusters only as **corroborating research evidence—not standalone trade
instructions**.

## Ranked discovery screen

The offline screening workflow reads a canonical JSON or CSV universe and one normalized
feature-snapshot JSON file per ticker. It never lets signals fetch data directly. Run the included
six-company fixture (including a deliberately ineligible ETF) with:

```bash
smct screen examples/screening/universe.json examples/screening/features \
  --as-of 2026-07-17T00:00:00Z --include-ineligible \
  --output-json ranked-results.json --output-csv ranked-results.csv
```

Scores normalize only across signals with all required evidence. The output separately reports
unavailable signals, feature completeness, stale evidence, and point-in-time warnings; absence of
evidence is never converted into a neutral signal score.
