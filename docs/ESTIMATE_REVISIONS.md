# Consensus estimate revisions

Consensus snapshots are immutable provider observations selected by `available_at`, never by the
retrieval date alone. The local provider accepts JSON, CSV, and JSON Lines and normalizes records
into a stable identity of ticker, metric, fiscal-period end, period type, unit, currency, and basis.
This prevents `FY1`/`Q1` labels from rolling into a different fiscal period.

At an evaluation time the newest available snapshot is selected; each 7/30/60/90-day comparison
uses only a snapshot available on or before the requested date and within the configured tolerance.
No values are interpolated. Missing history remains missing. Changes use `(current-prior)/abs(prior)`
when stable and the symmetric `2*(current-prior)/(abs(current)+abs(prior))` measure near zero or
across a sign change. Diagnostics explicitly identify these cases, stale records, low coverage, and
rollovers.

Analyst counts and dispersion describe coverage and disagreement, rather than directional evidence.
Quality is separate from raw momentum and accounts for age, coverage, history, and consistency.
Provider licensing may limit storing or redistributing consensus data; users must comply with their
provider agreement. Revisions are research evidence—not trade instructions.
