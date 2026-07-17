from __future__ import annotations

from datetime import date, datetime

from smct_research.macro.models import MacroFeature, MacroObservation

# A 1% 13-week balance-sheet move is material; smaller moves are stable liquidity.
LIQUIDITY_MATERIALITY_THRESHOLD_PCT = 1.0
# A 10bp six-month EFFR move is material enough to label easing or tightening.
POLICY_MATERIALITY_THRESHOLD_PCT = 0.10


def derive_regime(observations: list[MacroObservation], as_of: date) -> dict[str, MacroFeature]:
    """Derive only from values that were public by ``as_of``; missing inputs stay unknown."""
    usable = [
        o
        for o in observations
        if o.point_in_time_eligible
        and o.point_in_time_available_on is not None
        and o.point_in_time_available_on <= as_of
    ]
    by_id: dict[str, list[MacroObservation]] = {}
    for item in usable:
        by_id.setdefault(item.series_id, []).append(item)
    for values in by_id.values():
        values.sort(
            key=lambda x: (
                x.observation_date,
                x.first_available_on,
                x.vintage_date or x.first_available_on,
            )
        )
    output: dict[str, MacroFeature] = {}

    def latest(name: str) -> MacroObservation | None:
        return by_id.get(name, [None])[-1]

    def put(name: str, value: float | str | None, *series: str) -> None:
        sources = [latest(series_id) for series_id in series]
        dates = [
            item.point_in_time_available_on
            for item in sources
            if item and item.point_in_time_available_on
        ]
        complete = len(dates) == len(series) and value is not None
        output[name] = MacroFeature(
            name=name,
            value=value,
            available_on=max(dates, default=as_of),
            quality_score=1.0 if complete else 0.0,
            source_series=list(series),
        )

    fed = latest("EFFR")
    if fed:
        put("effective_federal_funds_rate", fed.value, "EFFR")
    for months in (3, 6, 12):
        put(
            f"policy_rate_change_{months}m",
            _change(by_id.get("EFFR", []), fed, months * 30),
            "EFFR",
        )

    cpi = latest("CPIAUCSL")
    real_rate = _year_over_year_percent(by_id.get("CPIAUCSL", []), cpi) if cpi else None
    if fed:
        put(
            "real_policy_rate_estimate",
            fed.value - real_rate if real_rate is not None else None,
            "EFFR",
            "CPIAUCSL",
        )

    dgs2, dgs10, dgs3mo = latest("DGS2"), latest("DGS10"), latest("DGS3MO")
    if dgs2 and dgs10:
        put("treasury_2y_10y_spread", dgs10.value - dgs2.value, "DGS2", "DGS10")
    if dgs3mo and dgs10:
        put("treasury_3m_10y_spread", dgs10.value - dgs3mo.value, "DGS3MO", "DGS10")
    for weeks in (4, 13, 52):
        put(
            f"fed_total_assets_change_{weeks}w",
            _percentage_change(by_id.get("WALCL", []), latest("WALCL"), weeks * 7),
            "WALCL",
        )
    for feature, series in (
        ("reserve_balance_change_13w", "WRESBAL"),
        ("reverse_repo_change_13w", "RRPONTSYD"),
    ):
        put(feature, _percentage_change(by_id.get(series, []), latest(series), 91), series)

    asset_change = output["fed_total_assets_change_13w"].value
    emergency = latest("H41_EMERGENCY")
    if emergency and emergency.value > 0:
        liquidity = "emergency_liquidity"
    elif asset_change is None:
        liquidity = "unknown"
    elif float(asset_change) >= LIQUIDITY_MATERIALITY_THRESHOLD_PCT:
        liquidity = "expansion"
    elif float(asset_change) <= -LIQUIDITY_MATERIALITY_THRESHOLD_PCT:
        liquidity = "contraction"
    else:
        liquidity = "stable"
    put(
        "liquidity_regime",
        liquidity,
        *("H41_EMERGENCY",) if emergency and emergency.value > 0 else ("WALCL",),
    )

    policy_change = output["policy_rate_change_6m"].value
    if policy_change is None:
        monetary = "unknown"
    elif float(policy_change) < -POLICY_MATERIALITY_THRESHOLD_PCT:
        monetary = "easing"
    elif float(policy_change) > POLICY_MATERIALITY_THRESHOLD_PCT:
        monetary = "tightening"
    elif fed and fed.value >= 4:
        monetary = "restrictive_stable"
    else:
        monetary = "neutral"
    put("monetary_policy_regime", monetary, "EFFR")
    quality = sum(x.quality_score for x in output.values()) / len(output) if output else 0
    put("regime_confidence", quality)
    put("macro_data_quality_score", quality)
    return output


def derive_regime_as_retrieved(
    observations: list[MacroObservation], retrieved_at: datetime
) -> dict[str, MacroFeature]:
    """Evaluate a current snapshot as retrieved, without claiming historical availability.

    Current FRED values may only participate when their retrieval timestamp is no later
    than the requested snapshot timestamp.  This is suitable for monitoring and is not
    a substitute for ALFRED-backed historical evaluation.
    """
    snapshot_date = retrieved_at.date()
    visible: list[MacroObservation] = []
    for observation in observations:
        if observation.retrieved_at > retrieved_at:
            continue
        if observation.source == "fred_current":
            visible.append(
                observation.model_copy(
                    update={
                        "point_in_time_eligible": True,
                        "publication_date": snapshot_date,
                        "first_available_on": snapshot_date,
                        "vintage_date": snapshot_date,
                    }
                )
            )
        else:
            visible.append(observation)
    return derive_regime(visible, snapshot_date)


def company_macro_sensitivity(values: dict[str, float | int | str | bool | None]) -> float:
    """Score normalized leverage and refinancing inputs; never mix raw dollar amounts."""
    net_debt = float(values.get("net_cash_or_debt") or 0) < 0
    interest_burden = float(values.get("interest_expense_burden") or 0)
    coverage = float(values.get("interest_coverage") or 99)
    floating = float(values.get("floating_rate_exposure") or 0)
    total_debt = float(values.get("total_debt") or 0)
    due = sum(float(values.get(f"debt_due_{m}m") or 0) for m in (12, 24, 36))
    refinancing_ratio = due / total_debt if total_debt > 0 else 0
    financing = float(values.get("external_financing_dependency") or 0)
    duration = float(values.get("valuation_duration_proxy") or 0)
    fcf_negative = float(values.get("free_cash_flow") or 0) < 0
    raw = (
        20 * net_debt
        + 20 * fcf_negative
        + min(20, interest_burden * 100)
        + min(15, floating * 100)
        + min(15, refinancing_ratio * 15)
        + min(15, financing * 100)
        + min(10, duration * 10)
        + (10 if coverage < 2 else 0)
    )
    return min(100.0, raw)


def _change(
    items: list[MacroObservation], current: MacroObservation | None, days: int
) -> float | None:
    if current is None:
        return None
    prior = [
        item for item in items if (current.observation_date - item.observation_date).days >= days
    ]
    return current.value - prior[-1].value if prior else None


def _percentage_change(
    items: list[MacroObservation], current: MacroObservation | None, days: int
) -> float | None:
    if current is None:
        return None
    prior = [
        item
        for item in items
        if (current.observation_date - item.observation_date).days >= days and item.value != 0
    ]
    return (current.value / prior[-1].value - 1) * 100 if prior else None


def _year_over_year_percent(
    items: list[MacroObservation], current: MacroObservation | None
) -> float | None:
    """Use the same calendar month a year earlier, allowing only a 7-day date offset."""
    if current is None:
        return None
    target = date(current.observation_date.year - 1, current.observation_date.month, 1)
    same_month = [
        item
        for item in items
        if item.observation_date.year == target.year
        and item.observation_date.month == target.month
        and item.value != 0
    ]
    if same_month:
        prior = min(same_month, key=lambda item: abs((item.observation_date - target).days))
    else:
        nearby = [
            item
            for item in items
            if item.value != 0 and abs((item.observation_date - target).days) <= 7
        ]
        if not nearby:
            return None
        prior = min(nearby, key=lambda item: abs((item.observation_date - target).days))
    return (current.value / prior.value - 1) * 100
