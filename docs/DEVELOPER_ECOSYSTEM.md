# Developer Ecosystem Momentum (B1)

B1 evaluates whether public developer ecosystems are becoming healthier, broader, and more durable over a 6–24 month research horizon. It is deterministic, offline-testable, and intended for explainable research evidence rather than automated trading.

## Rationale and interpretation

Developer adoption can improve before financial statements show the effect. B1 looks for sustained external contributor growth, releases, maintenance quality, package adoption, and breadth across important repositories.

Stars alone are weak evidence. Commit count alone is weak evidence. Public repositories may not represent the company’s total engineering activity, so closed-source companies can appear artificially weak. Community activity can be unrelated to revenue capture. Repository ownership and importance can change. Developer evidence is research evidence, not a trade instruction.

## Canonical data contract

The module defines immutable provider-neutral models for repository mappings, repository identity, repository observations, contributor activity, pull requests, issues, releases, stargazers/forks, package observations, feature output, and data-quality output. Repository observations carry repository status (`is_archived`, `is_fork`, `is_mirror`) because feature calculation consumes observations directly.

Repository observations retain observation windows, `available_at`, provider record IDs, and provenance. Package observations support PyPI, npm, crates.io, Maven, NuGet, RubyGems, and OCI-style registries without coupling to one API response shape.

## Point-in-time rules

Only observations with `available_at` on or before the evaluation timestamp are eligible. Mapping `effective_from`, `effective_to`, and `known_at` are enforced so historical analysis does not assume ownership before it was effective or known. Missing history remains missing and is not interpolated.

## Observation grain and interval matching

Each repository or package observation is treated as an aggregate for its declared interval. For a requested 30-, 90-, or 180-day window, B1 uses only observations whose `observation_window_start` and `observation_window_end` match the requested current or immediately-prior comparable interval and whose duration matches the requested grain within the configured tolerance. The default tolerance is one day for inclusive-boundary differences.

The calculation selects at most one canonical observation per provider/repository/window or provider/ecosystem/package/window. It does not use `available_at` as the activity timestamp, does not rescale a 90-day aggregate into 30-day velocity, and does not double-count overlapping rolling snapshots. If the required grain is absent, the window features remain missing.

## Missing-current versus true-zero semantics

No eligible current observation means current levels, velocities, and growth features for that window are `None`, with an explicit diagnostic. Current evidence with no prior comparable observation may populate current levels and velocities, but growth remains `None`. A true observed zero remains numeric `0`. Package observations with `download_count = None` are not converted to zero downloads.

## Company-to-repository mapping

Mappings support organizations, selected repositories outside a primary organization, exclusions, repository-role weights, first-party/community classification, package names, and effective dates.

Mapping precedence is deterministic: explicit exclusions override broader includes; otherwise the most-specific active mapping controls the repository, ordered by exact repository ID, exact owner/name, then organization. Multiple conflicting mappings at the same specificity fail clearly. Only mappings effective and known as of the evaluation timestamp participate.

Archived repositories, mirrors, and forks are excluded by default. Forks can be included only when the observation marks them as explicitly includable. Documentation-only and example repositories are not excluded by default, but their role weights come from configuration.

## Package mapping rules

Package evidence must match active approved company mappings through `package_names`. If a package is linked to a repository, that repository must be in the selected mapped repository set. Packages linked to excluded or wrong repositories are excluded. Unmapped packages produce diagnostics. Ambiguous package mappings fail clearly, and mapping effective/known dates apply to package evidence.

## Metrics

Contributor metrics include active, new, returning, external contributors, growth, concentration, top-one/top-five shares, and a bus-factor proxy. Maintenance metrics include issue-open and issue-close velocity, backlog growth, stale issue share, PR backlog, and merge/close timing where available. Adoption metrics include stargazer, fork, package-download, dependent-package growth, adoption breadth, and repository/package agreement.

## Bot classification precedence

Bot classification uses configuration consistently for contributor concentration, bot share, and score/confidence penalties. Precedence is: explicit allowlist means non-bot; explicit denylist means bot; provider bot flag means bot; configured login patterns may classify as bot; otherwise the normalized observation value is used. Contributor-derived bot share is reconciled with provider-reported `RepositoryObservation.bot_activity_share` by using the higher share for penalties.

## Raw versus adjusted activity

Raw commit velocity is preserved for audit as `developer_commit_velocity_<window>d_raw`. Adjusted commit velocity is preserved as `developer_commit_velocity_<window>d_adjusted` and is also emitted through the backward-compatible `developer_commit_velocity_<window>d` feature. Adjustment discounts generated activity, mass formatting, lockfile-only changes, automated dependency updates, provider-reported bot share, and imported histories. Combined noise shares are clamped to avoid negative activity.

## Filtering and manipulation controls

The module separates bot activity and exposes diagnostics for bot-heavy histories, generated or automated activity, repository imports, lockfile-only updates, mass formatting, promotional star spikes, mirrors, forks, archived repositories, and documentation/example repositories with low product relevance.

Star-spike diagnostics check corroboration from contributor growth, external-contributor growth, fork growth, release growth or velocity, and package growth. Uncorroborated star spikes are penalized.

## Provenance

Every used current and prior repository/package observation is recorded with provider-scoped keys such as `developer:repository:<provider>:<repository_id>:<provider_record_id>` and `developer:package:<provider>:<ecosystem>:<package_name>:<provider_record_id>`. Mapping provenance uses `developer:mapping:<provider>:<mapping-identity>`. Provenance preserves provider, provider record ID, source identifier or provenance, availability timestamp, interval, and repository/package identity. Excluded, unused, and future records are not added.

## Configuration loading

Defaults live in `config/developer_ecosystem.yaml` and are also packaged for CLI use. `smct developer-velocity` accepts `--config` to override defaults. Configuration controls lookback windows, bot allow/deny/patterns, repository-role weights, meaningful-activity threshold, staleness thresholds, concentration penalties, star-spike thresholds, package weights, minimum history, B1 scoring weights, breadth bonus, and bot penalty. Malformed configuration is reported as a controlled CLI error.

## Scoring, completeness, and confidence

The initial composite scorer weight for B1 is `0.85`, a moderate weight below core financial signals. The signal score rewards corroborated contributor, external-contributor, package, release, and breadth evidence, and penalizes backlog growth, high contributor concentration, bot-heavy activity, and star spikes without operational confirmation.

Completeness reflects current eligible mapped repositories rather than any historical record. Excluded and inactive mappings are not counted. Freshness is calculated from evidence actually used in the score, and one fresh source does not fully erase old critical evidence. Package availability contributes only when a current eligible package observation exists. Missing current 90-day evidence materially reduces confidence and can make B1 unavailable.

## Providers and rate limits

Tests and CLI examples use deterministic offline JSON/JSONL providers. A GitHub provider contract is present for future REST or GraphQL implementation with authentication, caching, conservative retries, rate-limit diagnostics, provider timestamps, incomplete-history diagnostics, and typed errors.

## CLI examples

```bash
smct developer-velocity examples/developer_ecosystem/history.json \
  --mapping-file examples/developer_ecosystem/mappings.json \
  --ticker ACME \
  --as-of 2026-07-17T00:00:00Z
```

```bash
smct developer-velocity examples/developer_ecosystem/history.json \
  --mapping-file examples/developer_ecosystem/mappings.json \
  --package-history-file examples/developer_ecosystem/packages.json \
  --ticker ACME \
  --as-of 2026-07-17T00:00:00Z \
  --output-json /tmp/developer.json \
  --output-csv /tmp/developer.csv
```

## Known limitations

The first implementation is intentionally offline-first. It does not scrape GitHub, infer private engineering productivity, assign investment recommendations, or assert that every repository under a corporate organization is financially relevant. Missing evidence remains missing, so companies with limited public data may have unavailable or low-confidence B1 evidence.

## Matched-series growth

Current levels and velocities use all valid current observations for the requested grain. Growth features use only matched current/prior pairs with the same provider, repository or package identity, and grain. Prior-only and current-only series are reported as unmatched diagnostics and are not interpreted as declines or growth from zero. Multiple providers for the same repository series are treated as ambiguous unless a future provider-precedence policy is added.

## Historical mapping policy

Mappings must be known by the evaluation timestamp and must cover the entire observation interval: `effective_from <= observation_window_start` and `effective_to is None or effective_to >= observation_window_end`. Partially overlapping aggregate observations are excluded rather than prorated, so pre-ownership repository or package history is not retroactively attached.

## Package identity policy

Package selectors distinguish ecosystem, normalized package name, optional provider, and optional repository identity. Thus `pypi:example` and `npm:example` are separate. Legacy `package_names` are still accepted for bundled examples, but typed package selectors are preferred when multiple ecosystems or providers can share names.

## Configuration-field usage

Retained configuration fields are production-wired: lookback windows and tolerance control grain selection; bot allow/deny/patterns drive bot classification; role weights affect repository weighting; meaningful-activity threshold affects breadth; fresh/stale days affect freshness confidence; concentration warning/high-risk thresholds affect penalties/diagnostics; star-spike thresholds affect manipulation diagnostics; package download/dependent weights affect quality score; minimum mapped repositories and preferred history reduce confidence or availability; B1 scoring weights affect contributor, external, maintenance, breadth, and bot score components. The composite B1 weight remains centralized in the composite scorer rather than duplicated in feature configuration.

## Provider revisions

Offline providers preserve later point-in-time corrections when the same provider/repository or provider/package interval has a new `available_at` and a new provider record ID. Exact duplicates are idempotent, while conflicting records for the same provider record ID or same logical interval plus `available_at` are controlled provider errors. Feature selection uses only revisions available by the evaluation timestamp and chooses the latest eligible revision deterministically.
