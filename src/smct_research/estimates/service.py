from __future__ import annotations

from datetime import datetime, timedelta

from smct_research.core.models import normalize_utc

from .models import (
    ConsensusEstimate,
    EstimateDataQuality,
    EstimateMetric,
    EstimateRevision,
    EstimateRevisionFeatures,
)


def calculate_features(
    records: list[ConsensusEstimate],
    ticker: str,
    metric: EstimateMetric,
    as_of: datetime,
    period_end=None,
    tolerance_days: int = 7,
) -> EstimateRevisionFeatures:
    as_of = normalize_utc(as_of)
    eligible = [
        x
        for x in records
        if x.ticker == ticker.upper()
        and x.metric == metric
        and x.available_at <= as_of
        and (period_end is None or x.target_period_end == period_end)
    ]
    if not eligible:
        raise ValueError("no eligible consensus snapshots")
    current = max(eligible, key=lambda x: x.available_at)
    series = [x for x in eligible if x.identity == current.identity]
    diagnostics = []
    if any(x.target_period_end != current.target_period_end for x in eligible):
        diagnostics.append("target_period_rollover")
    revisions = {}
    for days in (7, 30, 60, 90):
        cutoff = as_of - timedelta(days=days)
        options = [
            x
            for x in series
            if x.available_at <= cutoff
            and x.available_at >= cutoff - timedelta(days=tolerance_days)
        ]
        if not options:
            revisions[days] = EstimateRevision(
                requested_lookback_days=days, diagnostics=["missing_historical_comparison"]
            )
            continue
        prior = max(options, key=lambda x: x.available_at)
        delta = current.consensus - prior.consensus
        conventional = delta / abs(prior.consensus) if abs(prior.consensus) > 1e-6 else None
        symmetric = (
            2 * delta / (abs(current.consensus) + abs(prior.consensus))
            if abs(current.consensus) + abs(prior.consensus) > 1e-12
            else 0.0
        )
        crossing = prior.consensus * current.consensus < 0
        use = conventional is None or crossing or abs(conventional) > 5
        d = []
        if conventional is None:
            d.append("near_zero_denominator")
        if crossing:
            d.append(
                "negative_to_positive_transition"
                if prior.consensus < 0
                else "positive_to_negative_transition"
            )
        revisions[days] = EstimateRevision(
            requested_lookback_days=days,
            actual_lookback_days=(as_of - prior.available_at).days,
            value=symmetric if use else conventional,
            conventional=conventional,
            symmetric=symmetric,
            used_symmetric=use,
            diagnostics=d,
        )
    age = (as_of - current.available_at).days
    if age > 30:
        diagnostics.append("stale_consensus")
    if current.analyst_count is not None and current.analyst_count < 3:
        diagnostics.append("low_analyst_coverage")
    r30 = revisions[30].value
    r60 = revisions[60].value
    accel = (r30 - r60) if r30 is not None and r60 is not None else None
    ordered = sorted(series, key=lambda x: x.available_at)
    signs = []
    for a, b in zip(ordered, ordered[1:], strict=False):
        signs.append((b.consensus > a.consensus) - (b.consensus < a.consensus))
    streak = 0
    for sign in reversed(signs):
        if not sign or (streak and sign * (1 if streak > 0 else -1) < 0):
            break
        streak += sign
    coverage = sum(x.value is not None for x in revisions.values()) / 4 * 100
    score = max(
        0.0,
        min(
            1.0,
            0.35
            + 0.1 * min(current.analyst_count or 0, 5)
            + 0.3 * (coverage / 100)
            - 0.01 * max(age - 7, 0)
            - 0.1 * bool(diagnostics),
        ),
    )
    return EstimateRevisionFeatures(
        ticker=current.ticker,
        as_of=as_of,
        metric=metric,
        current=current,
        revisions=revisions,
        acceleration=accel,
        streak=streak,
        quality=EstimateDataQuality(
            score=score, coverage_percentage=coverage, diagnostics=diagnostics
        ),
        diagnostics=diagnostics,
    )
