# Run the stock research tool

SMCT 0.3 completes the local research workflow for 24–36 month stock selection:
screen a universe, inspect the evidence behind each score, save a thesis, and
check what changed. The default discovery universe is U.S. common equities in
technology, $300 million–$20 billion market cap, with at least $2 million average
daily dollar volume. The size limits are configurable; larger portfolio holdings
can also be reported with `--include-ineligible`.

## Install

Use Python 3.11 or later. From the repository directory:

```bash
python -m venv .venv
```

Activate it in Windows PowerShell:

```powershell
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
```

Or in macOS/Linux:

```bash
source .venv/bin/activate
python -m pip install -e '.[dev]'
```

All commands below also work as `python -m smct_research` instead of `smct`.
No brokerage account or order execution is involved.

## Try the complete workflow

This example uses **synthetic companies and historical fixture inputs**, not live
stocks or current recommendations. The date is deliberately fixed so the result
is reproducible.

```bash
smct research examples/screening/universe.json examples/screening/features --as-of 2026-07-17T00:00:00Z --output-dir research-output
```

Open `research-output/index.md`. The folder includes ranked CSV/JSON exports,
one Markdown and JSON research report per company, and a manifest identifying
the saved run. Reports include available and unavailable signals, weighted score
contributions, scenario valuations when supplied, risks, source references and
data-quality diagnostics. Theses are user-authored: the generator does not invent
a variant perception, price target or catalyst.

To show fewer companies without truncating the saved comparison history:

```bash
smct research examples/screening/universe.json examples/screening/features --as-of 2026-07-17T00:00:00Z --top 10 --min-coverage 40 --min-score 60
```

`--min-coverage 40` requires 40% of the registered signals to evaluate. The default
is zero so sparse evidence remains inspectable. Confidence and coverage are
different: high confidence in one observation is not broad evidence coverage.

## Use actual company data

1. Supply a universe JSON/CSV following `examples/screening/universe.json`.
   Include the correct ticker, CIK, market capitalization, sector/industry,
   country, exchange, active status, security type and average daily **dollar**
   volume. Market cap and liquidity must reflect the evaluation date; this release
   does not automatically discover or refresh the full listed-stock universe.
2. Import SEC financial evidence. Set your own identifiable SEC contact string:

```powershell
$env:SMCT_SEC_USER_AGENT = "SMCT Research your-name your-email@example.com"
smct ingest-sec my-universe.json --output-dir .smct/features --refresh
smct research my-universe.json .smct/features --output-dir research-output
```

On macOS/Linux use `export SMCT_SEC_USER_AGENT="SMCT Research your-name your-email@example.com"`.
The address above is a placeholder to replace with your own. There is no API key
for this adapter. Requests are paced at five per second and Company Facts cache
entries expire after one hour; `--refresh` bypasses them.

For an offline import, put Company Facts JSON files named `CIK0000001234.json`
in a directory and pass `--raw-directory DIRECTORY --as-of ISO_TIMESTAMP`.
The file's CIK must match its company. Raw evidence and observations are saved in
DuckDB; detailed periods and accessions are also exported under
`FEATURES_DIRECTORY/financial_evidence/`.

The financial-quality signal Q1 can run from SEC evidence alone when comparable
revenue growth and operating margin are available. Other signals require their
specific normalized inputs. Current prices, historical valuation multiples,
analyst estimate history, Reddit, GitHub/package histories and disclosure
features are **not automatically populated by the SEC command**. Existing offline
providers and the feature assembler support those inputs; unavailable signals
remain unavailable. Reddit ingestion stays optional and is not enabled here.

Ratios use matching reporting periods. Discrete quarters, year-to-date amounts
and annual amounts are distinguished. A year-to-date cash flow is not divided by
quarterly revenue. Both current and noncurrent components are required to show
reported long-term debt; it is not labeled total debt or used to invent net cash.
DCF inputs must still supply independently verified annual/TTM amounts and total
debt; do not substitute quarterly SEC outputs for annual values.

SEC Company Facts records filing dates rather than exact intraday availability.
Imports conservatively make a filing usable at the next UTC midnight. Current
Company Facts downloads are not a substitute for a survivorship-free historical
universe or an independently archived point-in-time dataset.

## Save and maintain a thesis

```bash
smct thesis init ALPH alph-thesis.json
```

Edit the JSON file. Fill in `variant_perception`, evidence, risks and invalidation
conditions. Use `status: "active"` when the thesis is ready. The default horizon
is 30 months. The supported statuses are `draft`, `active`, `strengthening`,
`weakening`, `invalidated` and `fully_priced`.

Optional structured tracking:

```json
{
  "catalysts": [
    {"description": "Review next quarterly results", "due_date": "2026-10-30", "status": "pending", "outcome": ""}
  ],
  "rules": [
    {"feature": "yoy_revenue_growth", "operator": "lt", "threshold": 0,
     "description": "Revenue contraction invalidates the growth assumption"}
  ]
}
```

Merge these fields into the full thesis file. Thresholds are in the feature's
native units: `0.10` means 10% for a ratio. Operators: `lt`, `le`, `gt`, `ge`.
Rules alert on a **true** condition. Catalyst statuses: `pending`, `occurred`,
`cancelled`.

```bash
smct thesis save alph-thesis.json
smct thesis list
smct thesis show ALPH --revision 1
smct thesis review examples/screening/features --as-of 2026-07-17T00:00:00Z --output review.json
```

Every save appends a revision and updates the local editing file's revision
number. An outdated file cannot overwrite newer changes. To resume editing,
run `smct thesis show ALPH --output alph-thesis.json`. Reviews flag due catalysts
and invalidation rules without changing status. Missing, future or older-than-90-day
evidence is marked unavailable for rule evaluation. Text-only invalidation
conditions are retained for manual review.

## Compare later screens

For a fixture demonstration, evaluate the same inputs one week later:

```bash
smct research examples/screening/universe.json examples/screening/features --as-of 2026-07-24T00:00:00Z
smct changes --since 2026-07-17T00:00:00Z --watchlist-only --output weekly-changes.md
```

For real research, refresh inputs before each run. `--since` selects the latest
saved run on or before that timestamp. The ending run is the latest saved run,
or the run on/before `--as-of`. Score deltas are withheld when the available
signal set, scoring model, eligibility or universe policy changes. A removed
company is not treated as a downgrade. Omitting `--watchlist-only` compares the
entire screened universe.

No recurring job is created by these commands. They can be run manually or from
an existing scheduler. Keep your databases and research inputs outside the public
source repository; default generated directories are git-ignored.

## Settings, persistence and validation

`smct research ... --config config/research.yaml` and `smct screen ... --config
config/research.yaml` use the same universe and signal weights. JSON is also
supported. Weights use real IDs (`A1`, `A2`, `A3`, `B1`, `Q1`, `F1`, `E2`, `E3`,
`M1`, `I1`), must be finite/nonnegative, and override the defaults individually.
The bundled two-level YAML format is supported; use JSON for richer mappings.
The `research` section documents the default thesis horizon; individual thesis
files control their own horizon.

Research runs and append-only thesis versions live in `.smct/research.sqlite3`.
Override with `--db PATH` on research/report/thesis/changes commands. Raw SEC
evidence lives in `.smct/evidence.duckdb` (`--evidence-db PATH` on ingestion).
Back up the `.smct` directory to retain history. Report IDs are SHA-256 hashes
of their canonical content, including scoring weights and the input snapshot hash.

```bash
python -m ruff check .
python -m mypy src
python -m pytest -q
```

The tests include the complete offline SEC → features → ranking → report path,
report persistence, thesis conflicts and monitoring, period alignment and
point-in-time protections. These are software checks, not evidence of investing
alpha. Forward-return studies, ablations and survivorship-free validation still
require actual historical datasets and have not been claimed as completed.

SEC references: [Data APIs](https://www.sec.gov/search-filings/edgar-application-programming-interfaces)
and [fair-access guidance](https://www.sec.gov/search-filings/edgar-search-assistance/accessing-edgar-data).
