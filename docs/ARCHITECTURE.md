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
