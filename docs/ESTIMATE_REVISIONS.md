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

## Reverse-DCF interaction metadata

The requested `revision_vs_implied_growth_alignment`, `revision_vs_dcf_direction`, and
`revision_dcf_divergence` fields are **explicitly deferred** until the reverse-DCF feature schema
provides a stable point-in-time implied-growth availability contract. A3 remains independent from
A2; no revision or DCF value is silently blended into the other signal in this release.

## CLI selectors and stable identity

Use `smct revisions HISTORY --ticker TICKER --as-of TIMESTAMP` for a single unambiguous series. If a file contains multiple stable identities, add selectors such as `--provider`, `--basis gaap|non_gaap|provider_defined`, `--period-type quarter|fiscal_year`, and `--period-end YYYY-MM-DD` rather than letting the CLI choose a provider or accounting basis arbitrarily.

A stable estimate-revision series is identified by provider, ticker, metric, target fiscal-period end, period type, unit, currency, and basis. Horizon labels such as `FY1` are retained only as metadata and rollover diagnostics; they are never enough to compare two consensus values. Rollover diagnostics are emitted only when the same provider, basis, period type, unit, currency, metric, ticker, and horizon label move to a different target period.

## Raw diagnostics versus normalized scoring features

Raw fields such as `eps_dispersion_change_30d` preserve the absolute change in provider-reported estimate dispersion for audit and diagnostics. A3 scoring uses `eps_dispersion_change_ratio_30d`, a scale-free relative change: `(current-prior)/abs(prior)` when prior dispersion is not near zero, otherwise the symmetric `2*(current-prior)/(abs(current)+abs(prior))` form. The value is `None` when either observation is unavailable and is never infinite or nonfinite.

A3 uses EPS-specific quality (`eps_revision_quality_score`) as its confidence input. Revenue revision and revenue quality fields can corroborate directional evidence, but revenue quality cannot replace or overwrite EPS quality for A3 confidence.
