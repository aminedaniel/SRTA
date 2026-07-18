from __future__ import annotations

from datetime import date, datetime, timedelta

from smct_research.core.models import normalize_utc

from .models import (
    ConsensusEstimate,
    EstimateDataQuality,
    EstimateMetric,
    EstimateRevision,
    EstimateRevisionFeatures,
)


def _change(
    current: ConsensusEstimate, prior: ConsensusEstimate, requested: int, as_of: datetime
) -> EstimateRevision:
    delta = current.consensus - prior.consensus
    conventional = delta / abs(prior.consensus) if abs(prior.consensus) > 1e-6 else None
    denominator = abs(current.consensus) + abs(prior.consensus)
    symmetric = 2 * delta / denominator if denominator > 1e-12 else 0.0
    crossing = current.consensus * prior.consensus < 0
    use = conventional is None or crossing or abs(conventional) > 5
    diagnostics = []
    if conventional is None:
        diagnostics.append("near_zero_denominator")
    if crossing:
        diagnostics.append(
            "negative_to_positive_transition"
            if prior.consensus < 0
            else "positive_to_negative_transition"
        )
    return EstimateRevision(
        requested_lookback_days=requested,
        actual_lookback_days=(as_of - prior.available_at).days,
        value=symmetric if use else conventional,
        conventional=conventional,
        symmetric=symmetric,
        used_symmetric=use,
        prior=prior,
        diagnostics=diagnostics,
    )


def _field_change(current: float | int | None, prior: float | int | None) -> float | int | None:
    return None if current is None or prior is None else current - prior


def calculate_features(
    records: list[ConsensusEstimate],
    ticker: str,
    metric: EstimateMetric,
    as_of: datetime,
    period_end: date | None = None,
    tolerance_days: int = 7,
    provider: str | None = None,
    basis=None,
    period_type=None,
) -> EstimateRevisionFeatures:
    as_of = normalize_utc(as_of)
    eligible = [
        x
        for x in records
        if x.ticker == ticker.upper() and x.metric == metric and x.available_at <= as_of
    ]
    if provider is not None:
        eligible = [x for x in eligible if x.provider == provider]
    if basis is not None:
        eligible = [x for x in eligible if x.basis == basis]
    if period_type is not None:
        eligible = [x for x in eligible if x.period_type == period_type]
    periods = {x.target_period_end for x in eligible}
    if period_end is None and len(periods) > 1:
        raise ValueError("period_end is required when multiple eligible target periods exist")
    eligible = [x for x in eligible if period_end is None or x.target_period_end == period_end]
    if not eligible:
        raise ValueError("no eligible consensus snapshots")
    identities = {x.identity for x in eligible}
    if len(identities) > 1:
        raise ValueError(
            "provider, basis, and period_type selection is required for multiple estimate series"
        )
    current = max(eligible, key=lambda x: (x.available_at, x.provider_record_id))
    series = [x for x in eligible if x.identity == current.identity]
    diagnostics = []
    # A rollover is only meaningful for the same provider/stable horizon label, never a comparison.
    if current.horizon_label and any(
        x.provider == current.provider
        and x.horizon_label == current.horizon_label
        and x.target_period_end != current.target_period_end
        for x in records
        if x.ticker == current.ticker and x.metric == metric and x.available_at <= as_of
    ):
        diagnostics.append("target_period_rollover")
    revisions = {}
    for days in (7, 30, 60, 90):
        cutoff = as_of - timedelta(days=days)
        options = [
            x for x in series if cutoff - timedelta(days=tolerance_days) <= x.available_at <= cutoff
        ]
        revisions[days] = (
            _change(current, max(options, key=lambda x: x.available_at), days, as_of)
            if options
            else EstimateRevision(
                requested_lookback_days=days, diagnostics=["missing_historical_comparison"]
            )
        )
    prior30 = revisions[30].prior
    prior60 = revisions[60].prior
    preceding = _change(prior30, prior60, 30, prior30.available_at) if prior30 and prior60 else None
    acceleration = (
        (revisions[30].value - preceding.value)
        if revisions[30].value is not None and preceding and preceding.value is not None
        else None
    )
    age = (as_of - current.available_at).days
    if age > 30:
        diagnostics.append("stale_consensus")
    if current.analyst_count is not None and current.analyst_count < 3:
        diagnostics.append("low_analyst_coverage")
    if (
        prior30
        and current.standard_deviation is not None
        and prior30.standard_deviation is not None
        and current.standard_deviation > prior30.standard_deviation * 1.5
    ):
        diagnostics.append("sharply_increasing_dispersion")
    ordered = sorted(series, key=lambda x: x.available_at)
    streak = 0
    for a, b in zip(reversed(ordered[:-1]), reversed(ordered[1:]), strict=False):
        sign = (b.consensus > a.consensus) - (b.consensus < a.consensus)
        if not sign or (streak and sign * (1 if streak > 0 else -1) < 0):
            break
        streak += sign
    coverage = sum(x.value is not None for x in revisions.values()) * 25
    quality = max(
        0.0,
        min(
            1.0,
            0.35
            + 0.1 * min(current.analyst_count or 0, 5)
            + 0.3 * coverage / 100
            - 0.01 * max(age - 7, 0)
            - 0.1 * bool(diagnostics),
        ),
    )
    sign_transition = next((d for d in revisions[30].diagnostics if "transition" in d), None)
    return EstimateRevisionFeatures(
        ticker=current.ticker,
        as_of=as_of,
        metric=metric,
        current=current,
        revisions=revisions,
        acceleration=acceleration,
        streak=streak,
        analyst_count_change_30d=_field_change(
            current.analyst_count, prior30.analyst_count if prior30 else None
        ),
        high_change_30d=_field_change(current.high, prior30.high if prior30 else None),
        low_change_30d=_field_change(current.low, prior30.low if prior30 else None),
        dispersion_change_30d=_field_change(
            current.standard_deviation, prior30.standard_deviation if prior30 else None
        ),
        breadth_change_30d=_field_change(
            current.estimate_breadth, prior30.estimate_breadth if prior30 else None
        ),
        days_since_latest_update=age,
        sign_transition=sign_transition,
        quality=EstimateDataQuality(
            score=quality, coverage_percentage=coverage, diagnostics=diagnostics
        ),
        diagnostics=diagnostics,
    )
