# Congressional disclosed-purchasing signal

## Research purpose

Congressional transaction disclosures are treated as secondary alignment evidence for a 24–36 month research thesis. They are not treated as proof of insider information, a standalone buy recommendation, or a short-term timing signal.

## Canonical transaction record

Each normalized record preserves:

- filer and chamber
- member, spouse, dependent, joint, or unknown ownership
- ticker and transaction type
- transaction date and public disclosure date
- original low and high reported value bounds
- midpoint estimated value
- source document URL

## Point-in-time rule

The observation becomes available to the research system on the public disclosure date. Historical studies must never make the transaction visible on its earlier transaction date. This prevents look-ahead bias.

## Aggregated features

The initial signal consumes:

- `congress_purchase_count_90d`
- `congress_sale_count_90d`
- `congress_unique_buyers_90d`
- `congress_estimated_purchase_usd_90d`
- `congress_latest_purchase_age_days`
- `congress_median_disclosure_lag_days`
- `congress_committee_relevance_score`
- `congress_repeat_buyer_score`

The purchase estimate uses the midpoint of each disclosed value range. Original bounds remain available for uncertainty analysis.

## Scoring principles

The score increases with:

- multiple independent purchasing households
- repeated purchasing rather than one isolated transaction
- larger disclosed value ranges
- recent transactions that were disclosed promptly
- contextual committee relevance
- purchases dominating sales

The score is discounted for stale activity and long disclosure lags. Committee relevance has a capped contribution and is never treated as causal evidence.

## Validation requirements

Forward-return studies should test 12-, 24-, and 36-month outcomes using disclosure-date availability. Required robustness checks include:

- member-only versus spouse/dependent transactions
- single-filer versus multi-filer clusters
- committee-relevant versus unrelated issuers
- purchase-size buckets
- disclosure-lag buckets
- pre- and post-earnings disclosures
- sector, market-cap, liquidity, and regime controls
- comparison against matched companies with similar valuation and operating momentum

The signal should remain a low-to-moderate weight until it demonstrates incremental value after those controls.
