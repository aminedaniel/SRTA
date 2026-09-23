# Evidence, weekly selection and validation

SRTA ranks small and mid cap technology companies for further research over 24–36
months. It does not place orders. A score is not an estimated return or a buy rating.

## What can run today

The SEC importer produces date bounded revenue, margins, cash flow, dilution and
balance sheet observations from public Company Facts. Other registered signals need
independent evidence. The `enrich` command merges **dated local exports** without
silently filling gaps. It does not download licensed datasets.

| Evidence | Screen signal | Required source |
| --- | --- | --- |
| SEC operating results and dilution | Q1 | `ingest-sec` |
| EV/Sales versus historical valuation and growth | A1 | Vendor history exported as dated evidence |
| Reverse DCF scenarios | A2 | Cash-flow, price and assumptions via existing `dcf` workflow |
| Fiscal-period-safe consensus revisions | A3 | Existing `revisions` workflow and licensed dated consensus |
| Developer activity | B1 | Existing mapped GitHub/package history workflow |
| Reddit attention and promotion | F1 | Dated optional data export |
| Congressional purchases | E2 | Public disclosure dates via existing adapter |
| Form 4 clusters | E3 | Filed SEC transactions via existing adapter |
| Federal Reserve regime | M1 | Existing dated Fed provider |
| Delayed institutional 13F evidence | I1 | Existing filed 13F adapter |
| Forward P/E relative to history | V1 | Dated positive forward P/E export spanning at least four years |
| Trend, RSI, momentum, volatility width | T1, T2, T3 | Adjusted daily closes, with sufficient history |

`T3` has **zero composite weight**: volatility compression can indicate an upcoming
move but says nothing about its direction. No OCO order is opened. Momentum receives
only a modest weight, so fundamental evidence remains the main research question.
The named source and availability time are retained for each imported observation.
Future observations are excluded. Numeric estimates and alternative data must come
from an identifiable source; they are never inferred from stock price.

## Weekly workflow

```bash
smct ingest-sec universe.json --output-dir .smct/sec-features
smct enrich .smct/sec-features .smct/weekly-features \
  --as-of 2026-09-23T23:00:00Z \
  --prices exports/adjusted_closes.csv \
  --multiples exports/forward_pe.csv \
  --evidence exports/dated_features.csv
smct research universe.json .smct/weekly-features \
  --as-of 2026-09-23T23:00:00Z --config config/research.yaml
```

`ingest-sec` requires CIKs and an identifiable `SMCT_SEC_USER_AGENT`; it can also
read a local Company Facts directory. `enrich` accepts:

- `adjusted_closes.csv`: `ticker,date,adjusted_close`, with ISO dates. A close is
  usable from the following UTC day. At least 200 closes are needed for EMA/RSI,
  253 for 12-minus-one-month momentum, and 119 for width compression.
- `forward_pe.csv`: `ticker,date,forward_pe`. Nonpositive ratios are discarded.
  The trailing five-year sample needs 24 observations spanning four years and a
  recent observation. The result is a within-company z-score.
- `dated_features.csv`: `ticker,feature,value,available_at,source`, with an ISO
  timestamp. This is for already normalized, independently sourced evidence.
  Distinct provider records may be merged; a duplicate feature from the SEC
  snapshot or two values with the same availability time is rejected.

The default `screen` and `research` commands show only companies with at least 50%
of weighted signals evaluated. For a deliberately sparse fixture, use
`--min-coverage 0`. The full universe and its missing-evidence diagnostics remain
in the saved research run. Coverage is a gate, not a probability of success.
The 50% threshold is an operational safeguard chosen before return testing.

## Test whether the model generates alpha

Collect **contemporaneous** universes, including securities that later delisted,
and one snapshot directory for each decision date. For example:

```text
history/universes/2021-01-04.json
history/snapshots/2021-01-04/ABC.json
history/adjusted_closes.csv
```

Then run:

```bash
smct validate-history history/universes history/snapshots \
  history/adjusted_closes.csv --benchmark VOO \
  --output-json validation-output/results.json
```

The validator screens each historical universe at its own date, selects the two
highest ranked names meeting the 50% coverage gate, enters at the next supplied
trading close, and measures equal-weight 12, 24 and 36 month returns against VOO.
It subtracts 0.5% round-trip costs, records incomplete and censored cohorts, and
does not count overlapping cohorts as independent evidence for its significance
diagnostic. A provisional positive finding requires 30 non-overlapping cohorts
with a t-statistic of at least 2. That deliberately strict criterion is **not**
a proof of future alpha. A stronger study would also use total-return prices,
delisting proceeds, size/sector-matched benchmarks, subperiod tests and an
untouched holdout period. Do not tune weights on this test set.

The repository currently contains no real historical set with the required
point-in-time universes, feature snapshots and outcomes. Unit tests use synthetic
examples to check code behavior; **alpha has not been established**. In particular,
the earlier Monday screen with roughly 10% coverage cannot serve as a performance
backtest or be treated as a fully systematic recommendation.
