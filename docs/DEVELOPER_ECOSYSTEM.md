# Developer Ecosystem Momentum (B1)

B1 evaluates whether public developer ecosystems are becoming healthier, broader, and more durable over a 6–24 month research horizon. It is designed for explainable research evidence, not automated trading.

## Rationale and interpretation

Developer adoption can improve before financial statements show the effect. B1 looks for sustained external contributor growth, releases, maintenance quality, package adoption, and breadth across important repositories.

Stars alone are weak evidence. Commit count alone is weak evidence. Public repositories may not represent the company’s total engineering activity, so closed-source companies can appear artificially weak. Community activity can be unrelated to revenue capture. Repository ownership and importance can change. Developer evidence is research evidence, not a trade instruction.

## Canonical data contract

The module defines immutable provider-neutral models for repository mappings, repository identity, repository observations, contributor activity, pull requests, issues, releases, stargazers/forks, package observations, feature output, and data-quality output.

Repository observations retain observation windows, `available_at`, provider record IDs, and provenance. Package observations support PyPI, npm, crates.io, Maven, NuGet, RubyGems, and OCI-style registries without coupling to one API response shape.

## Point-in-time rules

Only observations with `available_at` on or before the evaluation timestamp are used. Mapping `effective_from`, `effective_to`, and `known_at` are enforced so historical analysis does not assume ownership before it was effective or known. Missing history remains missing and is not interpolated.

## Company-to-repository mapping

Mappings support organizations, specific repositories outside a primary organization, exclusions, repository-role weights, first-party/community classification, package names, and effective dates. Ambiguous mappings fail clearly instead of choosing arbitrarily.

## Metrics

Contributor metrics include active, new, returning, external contributors, growth, concentration, top-one/top-five shares, and a bus-factor proxy. Maintenance metrics include issue-open and issue-close velocity, backlog growth, stale issue share, PR backlog, and merge/close timing where available. Adoption metrics include stargazer, fork, package-download, dependent-package growth, adoption breadth, and repository/package agreement.

## Filtering and manipulation controls

The module separates bot activity and exposes diagnostics for bot-heavy histories, generated or automated activity, repository imports, lockfile-only updates, mass formatting, promotional star spikes, mirrors, forks, archived repositories, and documentation/example repositories with low product relevance.

## Scoring and confidence

The initial composite scorer weight for B1 is `0.85`, a moderate weight below core financial signals. The signal score rewards corroborated contributor, external-contributor, package, release, and breadth evidence, and penalizes backlog growth, high contributor concentration, bot-heavy activity, and star spikes without operational confirmation.

Confidence reflects freshness, mapped-repository coverage, meaningful active repositories, package evidence, cross-source agreement, bot/manipulation uncertainty, and mapping quality. Missing evidence reduces confidence or makes B1 unavailable; it is not converted into neutral evidence.

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

The first implementation is intentionally offline-first. It does not scrape GitHub, infer private engineering productivity, assign investment recommendations, or assert that every repository under a corporate organization is financially relevant.
