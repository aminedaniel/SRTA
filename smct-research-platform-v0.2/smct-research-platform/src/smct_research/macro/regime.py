from __future__ import annotations

from datetime import date

from smct_research.macro.models import MacroFeature, MacroObservation

H41_SERIES = {
    "total_assets": "WALCL",
    "treasuries": "TREAST",
    "agency_mbs": "WSHOMCB",
    "reserve_balances": "WRESBAL",
    "reverse_repos": "RRPONTSYD",
    "emergency_lending": "H41_EMERGENCY",
}


def derive_regime(observations: list[MacroObservation], as_of: date) -> dict[str, MacroFeature]:
    usable = [o for o in observations if o.available_on <= as_of]
    by_id: dict[str, list[MacroObservation]] = {}
    for item in usable:
        by_id.setdefault(item.series_id, []).append(item)
    for values in by_id.values():
        values.sort(key=lambda x: x.observation_date)
    output: dict[str, MacroFeature] = {}

    def latest(name: str) -> MacroObservation | None:
        return by_id.get(name, [None])[-1]

    def put(name: str, value: float | str | None, *series: str) -> None:
        source_observations = [latest(x) for x in series]
        dates = [item.available_on for item in source_observations if item is not None]
        output[name] = MacroFeature(
            name=name,
            value=value,
            available_on=max(dates, default=as_of),
            quality_score=len(dates) / len(series) if series else 0,
            source_series=list(series),
        )

    fed = latest("EFFR")
    if fed:
        put("effective_federal_funds_rate", fed.value, "EFFR")
    for months in (3, 6, 12):
        change = _change(by_id.get("EFFR", []), fed, months * 30)
        put(f"policy_rate_change_{months}m", change, "EFFR")
    cpi = latest("CPIAUCSL")
    if fed and cpi:
        put(
            "real_policy_rate_estimate",
            fed.value - _annual_change(by_id["CPIAUCSL"], cpi),
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
            _change(by_id.get("WALCL", []), latest("WALCL"), weeks * 7),
            "WALCL",
        )
    for feature, series in (
        ("reserve_balance_change_13w", "WRESBAL"),
        ("reverse_repo_change_13w", "RRPONTSYD"),
    ):
        put(feature, _change(by_id.get(series, []), latest(series), 91), series)
    assets = output.get("fed_total_assets_change_13w")
    policy = output.get("policy_rate_change_6m")
    liquidity = (
        "expansion"
        if assets and assets.value is not None and float(assets.value) > 0
        else "contraction"
    )
    monetary = (
        "easing"
        if policy and policy.value is not None and float(policy.value) < -0.1
        else "tightening"
        if policy and policy.value is not None and float(policy.value) > 0.1
        else "restrictive_stable"
        if fed and fed.value >= 4
        else "neutral"
    )
    put("liquidity_regime", liquidity, "WALCL")
    put("monetary_policy_regime", monetary, "EFFR")
    quality = sum(x.quality_score for x in output.values()) / len(output) if output else 0
    put("regime_confidence", quality)
    put("macro_data_quality_score", quality)
    return output


def company_macro_sensitivity(values: dict[str, float | int | str | bool | None]) -> float:
    """Bounded context score: debt/refinancing/funding dependence increase sensitivity."""
    net_debt = float(values.get("net_cash_or_debt") or 0) < 0
    interest_burden = float(values.get("interest_expense_burden") or 0)
    coverage = float(values.get("interest_coverage") or 99)
    floating = float(values.get("floating_rate_exposure") or 0)
    due = sum(float(values.get(f"debt_due_{m}m") or 0) for m in (12, 24, 36))
    financing = float(values.get("external_financing_dependency") or 0)
    duration = float(values.get("valuation_duration_proxy") or 0)
    fcf_negative = float(values.get("free_cash_flow") or 0) < 0
    raw = (
        20 * net_debt
        + 20 * fcf_negative
        + min(20, interest_burden * 100)
        + min(15, floating * 100)
        + min(15, due / 1_000_000)
        + min(15, financing * 100)
        + min(10, duration * 10)
        + (10 if coverage < 2 else 0)
    )
    return min(100.0, raw)


def _change(
    items: list[MacroObservation], latest: MacroObservation | None, days: int
) -> float | None:
    if latest is None:
        return None
    prior = [x for x in items if (latest.observation_date - x.observation_date).days >= days]
    return latest.value - prior[-1].value if prior else None


def _annual_change(items: list[MacroObservation], latest: MacroObservation) -> float:
    change = _change(items, latest, 330)
    return change or 0.0
