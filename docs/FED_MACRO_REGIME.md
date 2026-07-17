# Federal Reserve and liquidity regime

This module is a bounded research-context modifier—not a buy, sell, or execution instruction. `FRED_API_KEY` and an identifiable `FRED_USER_AGENT` are required only for uncached FRED requests. The user agent is deliberately configurable (for example, `SMCT Research ops@example.org`); a blank, placeholder, or value without a contact email is rejected.

## Series metadata and point-in-time use

The typed registry records each series' unit, frequency, release source, and revision behavior. It includes percent rates (EFFR and Treasury yields), CPI's `index_1982_84_100`, balance-sheet values in millions/billions USD (including daily RRPONTSYD), and percentage-point spread series. Normalization preserves that metadata.

Each observation separately records its observation date, release/publication date, first public availability date, vintage date, and retrieval timestamp. Historical calculations include an observation only on or after `first_available_on`; for FRED/ALFRED this is the first `realtime_start` vintage. Observation dates are never assumed to be release dates. Revised values consequently cannot appear in an earlier historical evaluation.

## Classification thresholds

- CPI inflation is trailing year-over-year percentage change: `(CPI_t / CPI_t-12m - 1) * 100`. The real policy-rate estimate is EFFR less that value. It is explicitly unavailable when CPI history is insufficient.
- Policy change greater than +10bp over six months is `tightening`; less than -10bp is `easing`; otherwise EFFR >=4% is `restrictive_stable`, else `neutral`. Missing EFFR history yields `unknown`.
- Liquidity uses normalized 13-week WALCL percentage change. At least +1% is `expansion`, at most -1% is `contraction`, and moves inside that band are `stable`. Missing comparison data yields `unknown`.
- A positive `H41_EMERGENCY` value produces the separate `emergency_liquidity` classification, regardless of ordinary balance-sheet movement.

Company sensitivity uses ratios (interest burden, floating-rate exposure, and debt due divided by total debt), not unnormalized dollar figures, so equivalent capital structures score consistently across company sizes.
