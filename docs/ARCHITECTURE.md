# Architecture

## Product objective

Identify underfollowed or mispriced U.S.-listed small- and mid-cap technology companies with a 24–36 month investment horizon. The system is a research and stock-selection platform, not an execution-first trading system.

## Architectural boundaries

1. Data adapters collect raw information from vendors and public sources.
2. Normalizers convert raw data into canonical company features.
3. Signals consume only feature snapshots; signals never call external APIs.
4. The scoring layer ranks research priority but does not issue automatic trades.
5. A persistent thesis record stores variant perception, evidence, risks, valuation scenarios, and invalidation conditions.
6. Monitoring records whether a thesis is strengthening, weakening, invalidated, or fully priced.

## Initial modules

- Universe construction
- Financial normalization
- Valuation and expectations
- Business quality
- Operating momentum
- Corporate insider alignment
- Congressional disclosed purchasing and political-trading context
- Developer ecosystem activity
- Reddit awareness, narrative, and crowding
- Value-trap detection
- Composite research-priority scoring
- Thesis lifecycle management

## Deliberate exclusions from v0.1

- Live brokerage execution
- Intraday microstructure signals
- Stop-loss automation
- Kelly sizing
- High-frequency scheduling

These may be added later as optional downstream capabilities, but they do not drive the core design.

## Ranked discovery workflow

`smct screen` is the batch boundary for research discovery. A universe reader creates canonical
company entries, `UniversePolicy` retains every eligibility exclusion reason, and the feature
assembly layer turns normalized provider evidence into point-in-time `FeatureSnapshot` objects.
Signals receive only a snapshot and therefore cannot make external API calls. The batch service
evaluates every registered signal whose required fields are present, then normalizes the composite
score over evaluated signals only. It orders equal scores by ticker, emits coverage and stale-data
diagnostics, and can write terminal, JSON, and flat CSV representations. See the local command in
the README and `examples/screening/` for a reproducible offline example.

## Milestone 4 research reports

- Completed: deterministic company research report.
- Completed: append-only persistent thesis records.
- Not completed here: catalyst tracking over time, weekly watchlist change report, validation framework.
- See `docs/RESEARCH_REPORTS.md` for schema, point-in-time behavior, lifecycle transitions, persistence, and CLI examples.
Additional hardening documents full canonical report identity, canonical versus ranked ordering, unknown signal-weight handling, hash verification, source-report integrity, timestamp progression, multiple thesis-series selection, `--thesis-id` CLI behavior, and controlled error semantics.

