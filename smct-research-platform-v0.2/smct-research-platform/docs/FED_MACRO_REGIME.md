# Federal Reserve and liquidity regime

This module is a bounded research-context modifier—not a buy, sell, or execution instruction.
Providers fetch/cache FRED and ALFRED observations and normalize H.4.1 releases before signals see
features. `FRED_API_KEY` supplies the optional FRED key; offline fixtures and existing caches need
no key. Historical evaluation filters by each observation's `available_on` date and uses ALFRED
vintages where revisions apply.

## Mappings and assumptions

- EFFR: effective federal funds rate; CPIAUCSL: real-rate estimate; DGS2/DGS10/DGS3MO: curve spreads.
- WALCL: total assets; WRESBAL: reserve balances; RRPONTSYD: reverse repos. H.4.1 emergency
  facilities are retained under `H41_EMERGENCY` when present.
- Policy change above +10bp over six months is `tightening`; below -10bp is `easing`; otherwise
  EFFR >=4% is `restrictive_stable`, else `neutral`.
- Positive 13-week total-assets change is `expansion`; zero/negative is `contraction`.
- The signal's absolute score is capped at 15. Confidence is mean source completeness; it is not
  a probability of returns.

## Risks

Series publication timing, revisions, changing H.4.1 line items, CPI lag, and simple calendar-day
lookbacks can distort historical classifications. Rate sensitivity inputs are issuer disclosures and
may be incomplete, particularly floating-rate debt and refinancing schedules.
