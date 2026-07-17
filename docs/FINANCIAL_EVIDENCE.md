# Free-first financial evidence

`SecEdgarProvider` accesses SEC Company Facts (`/api/xbrl/companyfacts`) and submission history
(`/submissions`) only through a provider adapter. It requires an application name and contact email
in its configurable user-agent, uses a 5 requests/second default (below the SEC 10 rps guidance),
and caches raw JSON locally. Signals do not make network calls.

## SEC tag mappings

Normalization maps US-GAAP tags: revenue (`RevenueFromContractWithCustomerExcludingAssessedTax`,
`SalesRevenueNet`, `Revenues`), gross profit, operating income, net income, operating cash flow,
capital expenditures, cash, debt, diluted shares, and share-based compensation. Tag availability,
units, and company extensions vary; missing tags remain missing and lower
`missing_data_quality_score` rather than being inferred.

## Restatements and point in time

Each observation records accession, form, period, unit, filing date, retrieval time, source URL,
and publication date. Amendments are marked and earlier facts are marked superseded but never
deleted. Feature construction excludes records published after its requested `as_of` date and uses
only non-superseded observations available then, preventing look-ahead bias. A fiscal-year change
is handled by matching comparable period types and prior calendar years; it may reduce comparability.
