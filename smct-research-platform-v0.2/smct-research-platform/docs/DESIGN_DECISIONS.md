# Design decisions

## ADR-001: Research-first, execution-optional

The core product ranks research candidates and maintains investment theses. Brokerage execution is outside the core domain and may be introduced only through an optional adapter.

## ADR-002: Signals consume canonical features

Signals may not perform network requests. This permits deterministic testing, point-in-time validation, vendor replacement, and provenance tracking.

## ADR-003: Multidimensional scores over opaque recommendations

The system will expose valuation, quality, momentum, expectations, alternative-data, insider-alignment, crowding, dilution, and balance-sheet dimensions. A composite score prioritizes research; it is not a buy instruction.

## ADR-004: Reddit measures attention and narrative risk

Reddit is used to identify underfollowed companies, sentiment divergence, narrative formation, promotional activity, and crowding. Bullish sentiment alone is not considered positive evidence.

## ADR-005: Long-horizon validation

Signal research will use point-in-time data and forward 12-, 24-, and 36-month outcomes, including drawdown, dilution, bankruptcy, acquisition, and permanent-impairment labels.


## ADR-006: Congressional disclosures are alignment evidence, not insider proof

The platform labels this evidence as congressional disclosed purchasing. It does not infer illegality or privileged-information use. Scores emphasize independent buyer breadth, repeat purchasing, transaction recency, public-disclosure timing, and purchase-versus-sale balance. Committee relevance is contextual metadata with a capped contribution.

Backtests must use the public disclosure date, never the earlier transaction date, to prevent look-ahead bias. Reported dollar ranges are normalized using their midpoint while retaining the original bounds and source document. Transactions by members, spouses, dependents, and joint owners remain separately identifiable.
