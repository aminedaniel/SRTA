# Federal Reserve and liquidity regime

This module is a bounded research-context modifier—not a buy, sell, or execution instruction. `FRED_API_KEY` and an identifiable `FRED_USER_AGENT` are required only for uncached FRED requests. The user agent is deliberately configurable (for example, `SMCT Research ops@example.org`); a blank, placeholder, or value without a contact email is rejected.

## Series metadata and point-in-time use

The typed registry records each series' unit, frequency, release source, and revision behavior. It includes percent rates (EFFR and Treasury yields), CPI's `index_1982_84_100`, balance-sheet values in millions/billions USD (including daily RRPONTSYD), and percentage-point spread series. Normalization preserves that metadata.

Current FRED values are explicitly marked ineligible for historical point-in-time evaluation: their `realtime_start` describes the response vintage, not an observation release date. Historical values must be normalized from ALFRED together with a complete release-calendar mapping that supplies each observation's original public availability date. Each point-in-time observation separately records observation, publication, availability, vintage, and retrieval dates; values without established availability are never used in historical calculations. Cached current and ALFRED payloads support configurable TTLs, explicit refresh, and immutable timestamped raw snapshots. A revised ALFRED value becomes eligible only on its own vintage date; `derive_regime_as_retrieved` is provided for safe current-snapshot monitoring and must not be used as a historical point-in-time substitute.

## Classification thresholds

- CPI inflation is trailing year-over-year percentage change: `(CPI_t / CPI_t-12m - 1) * 100`. The real policy-rate estimate is EFFR less that value. It is explicitly unavailable when CPI history is insufficient.
- Policy change greater than +10bp over six months is `tightening`; less than -10bp is `easing`; otherwise EFFR >=4% is `restrictive_stable`, else `neutral`. Missing EFFR history yields `unknown`.
- Liquidity uses normalized 13-week WALCL percentage change. At least +1% is `expansion`, at most -1% is `contraction`, and moves inside that band are `stable`. Missing comparison data yields `unknown`.
- A positive `H41_EMERGENCY` value produces the separate `emergency_liquidity` classification, regardless of ordinary balance-sheet movement.

Company sensitivity uses ratios (interest burden, floating-rate exposure, and debt due divided by total debt), not unnormalized dollar figures, so equivalent capital structures score consistently across company sizes.
