# Renaissance Public Equity Activity

This low-weight signal ingests **Renaissance Technologies'** public SEC EDGAR `13F-HR` and `13F-HR/A` filings. It retains the reporting quarter, filing/publication date, amendment status, issuer, CUSIP, optional CUSIP-to-ticker mapping, shares, and 13F reported market value. Reported 13F values are in thousands of dollars and are normalized to dollars.

For point-in-time research, a filing is eligible only on or after its **filing date**, never its quarter-end date. An amendment supersedes an original filing only after the amendment's own filing date. Derived activity includes new and exited positions, share-count percentage changes, consecutive quarters held, position-size percentile in the disclosed portfolio, and disclosure age. The signal applies exponential decay for both disclosure age and the quarter-end-to-filing lag; the 45-day 13F filing window is explicitly penalized.

## Interpretation and limits

The signal is weak corroboration only: its absolute score is capped at 5 and its composite weight is 0.25. New or materially increased disclosed positions can be positive; repeated ownership is modestly positive; exits and reductions are weakly negative at most.

Public 13F data **cannot identify Medallion holdings**. It excludes shorts and many derivatives, and it is delayed manager-level disclosure rather than a complete or real-time view of any strategy. Do not use it as evidence that any disclosed position belongs to Medallion, or as a standalone investment decision.
