"""Map SEC US-GAAP facts into immutable canonical observations and PIT features."""

from __future__ import annotations

from datetime import UTC, date, datetime
from math import isfinite
from typing import Any

from smct_research.financials.models import (
    FeatureValue,
    FilingMetadata,
    FinancialObservation,
    Provenance,
    ReportingPeriodType,
)

TAG_MAPPINGS: dict[str, tuple[str, ...]] = {
    "revenue": (
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "SalesRevenueNet",
        "Revenues",
    ),
    "gross_profit": ("GrossProfit",),
    "operating_income": ("OperatingIncomeLoss",),
    "net_income": ("NetIncomeLoss",),
    "operating_cash_flow": ("NetCashProvidedByUsedInOperatingActivities",),
    "capital_expenditures": ("PaymentsToAcquirePropertyPlantAndEquipment",),
    "cash_and_equivalents": ("CashAndCashEquivalentsAtCarryingValue",),
    "long_term_debt_current": (
        "LongTermDebtCurrent",
        "LongTermDebtAndFinanceLeaseObligationsCurrent",
    ),
    "long_term_debt_noncurrent": (
        "LongTermDebtNoncurrent",
        "LongTermDebtAndFinanceLeaseObligationsNoncurrent",
    ),
    "diluted_weighted_average_shares": ("WeightedAverageNumberOfDilutedSharesOutstanding",),
    "stock_based_compensation": ("ShareBasedCompensation",),
}


def normalize_company_facts(
    facts: dict[str, Any], *, retrieved_at: datetime | None = None
) -> list[FinancialObservation]:
    retrieved = retrieved_at or datetime.now(UTC)
    cik = str(facts.get("cik", "")).zfill(10)
    us_gaap = facts.get("facts", {}).get("us-gaap", {})
    observations: list[FinancialObservation] = []
    for metric, tags in TAG_MAPPINGS.items():
        seen: set[tuple[object, ...]] = set()
        for tag in tags:
            concept = us_gaap.get(tag, {})
            for unit, items in concept.get("units", {}).items():
                for item in items:
                    observation = _observation(metric, item, unit, cik, retrieved)
                    if observation is not None:
                        key = (
                            observation.period_start,
                            observation.period_end,
                            observation.filing.accession_number,
                            observation.unit,
                        )
                        if key in seen:
                            continue
                        seen.add(key)
                        observations.append(observation)
    return observations


def _observation(
    metric: str, item: dict[str, Any], unit: str, cik: str, retrieved: datetime
) -> FinancialObservation | None:
    try:
        form, filed, end, accession = (
            item["form"],
            date.fromisoformat(item["filed"]),
            date.fromisoformat(item["end"]),
            item["accn"],
        )
        value = float(item["val"])
        start = date.fromisoformat(item["start"]) if item.get("start") else None
    except (KeyError, TypeError, ValueError):
        return None
    if form not in {"10-K", "10-K/A", "10-Q", "10-Q/A"}:
        return None
    expected_unit = "shares" if metric == "diluted_weighted_average_shares" else "USD"
    if unit != expected_unit or not isfinite(value) or (start and start > end):
        return None
    period_type = (
        ReportingPeriodType.ANNUAL if form.startswith("10-K") else ReportingPeriodType.QUARTERLY
    )
    if start:
        days = (end - start).days + 1
        if 330 <= days <= 380:
            period_type = ReportingPeriodType.ANNUAL
        elif 60 <= days <= 120:
            period_type = ReportingPeriodType.QUARTERLY
        elif 121 <= days < 330:
            period_type = ReportingPeriodType.YEAR_TO_DATE
        else:
            return None
    filing = FilingMetadata(
        cik=cik,
        accession_number=accession,
        form=form,
        filed_at=filed,
        report_date=end,
        is_amendment=form.endswith("/A"),
    )
    return FinancialObservation(
        metric=metric,
        value=value,
        unit=unit,
        period_type=period_type,
        period_start=start,
        period_end=end,
        fiscal_year=item.get("fy"),
        fiscal_period=item.get("fp"),
        filing=filing,
        provenance=Provenance(
            source_url=f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json",
            retrieved_at=retrieved,
            filing_date=filed,
            publication_date=filed,
            raw_document_id=accession,
        ),
    )


def derive_features(
    observations: list[FinancialObservation], as_of: date
) -> dict[str, FeatureValue]:
    """Return only values public by ``as_of``; amendments remain distinct in storage."""
    usable = [o for o in observations if o.available_on <= as_of and o.period_end <= as_of]
    latest: dict[str, FinancialObservation] = {}
    for observation in usable:
        current = latest.get(observation.metric)
        if current is None or _order(observation) > _order(current):
            latest[observation.metric] = observation
    output: dict[str, FeatureValue] = {}
    for metric, observation in latest.items():
        output[metric] = FeatureValue(
            name=metric,
            value=observation.value,
            as_of=observation.period_end,
            available_on=observation.available_on,
            quality_score=1,
            source_accessions=[observation.filing.accession_number],
            period_start=observation.period_start,
            period_end=observation.period_end,
        )
    if "operating_cash_flow" in output and "capital_expenditures" in output:
        a, b = output["operating_cash_flow"], output["capital_expenditures"]
        assert a.value is not None and b.value is not None
        if _same_period(a, b):
            output["free_cash_flow"] = _derived("free_cash_flow", a.value - abs(b.value), a, b)
    _ratio(output, "gross_margin", "gross_profit", "revenue")
    _ratio(output, "operating_margin", "operating_income", "revenue")
    _ratio(output, "free_cash_flow_margin", "free_cash_flow", "revenue")
    _ratio(output, "stock_based_compensation_pct_revenue", "stock_based_compensation", "revenue")
    if "long_term_debt_current" in output and "long_term_debt_noncurrent" in output:
        current_debt, noncurrent = (
            output["long_term_debt_current"],
            output["long_term_debt_noncurrent"],
        )
        if _same_period(current_debt, noncurrent):
            assert current_debt.value is not None and noncurrent.value is not None
            output["reported_long_term_debt"] = _derived(
                "reported_long_term_debt",
                current_debt.value + noncurrent.value,
                current_debt,
                noncurrent,
            )
    for metric in ("revenue", "diluted_weighted_average_shares"):
        prior = _prior_year(usable, metric, latest.get(metric))
        if prior and latest.get(metric) and prior.value:
            name = "yoy_revenue_growth" if metric == "revenue" else "yoy_diluted_share_growth"
            current_feature = output[metric]
            assert current_feature.value is not None
            prior_feature = FeatureValue(
                name=metric,
                value=prior.value,
                as_of=prior.period_end,
                available_on=prior.available_on,
                quality_score=1,
                source_accessions=[prior.filing.accession_number],
                period_start=prior.period_start,
                period_end=prior.period_end,
            )
            output[name] = _derived(
                name, current_feature.value / prior.value - 1, current_feature, prior_feature
            )
    if latest:
        most_recent = max(item.available_on for item in latest.values())
        output["filing_recency_days"] = FeatureValue(
            name="filing_recency_days",
            value=float((as_of - most_recent).days),
            as_of=as_of,
            available_on=most_recent,
            quality_score=1,
        )
    required = set(TAG_MAPPINGS)
    output["missing_data_quality_score"] = FeatureValue(
        name="missing_data_quality_score",
        value=len(required & set(output)) / len(required),
        as_of=as_of,
        available_on=as_of,
        quality_score=1,
    )
    return output


def _prior_year(
    items: list[FinancialObservation], metric: str, latest: FinancialObservation | None
) -> FinancialObservation | None:
    if latest is None:
        return None
    candidates = [
        x
        for x in items
        if x.metric == metric
        and x.period_type == latest.period_type
        and 350 <= (latest.period_end - x.period_end).days <= 380
        and abs(_duration(x) - _duration(latest)) <= 14
    ]
    return max(candidates, key=_order, default=None)


def _duration(item: FinancialObservation) -> int:
    return (item.period_end - item.period_start).days + 1 if item.period_start else 0


def _order(item: FinancialObservation) -> tuple[date, int, date]:
    # Prefer the discrete quarter to a YTD duration at the same end date.
    return item.period_end, -_duration(item), item.filing.filed_at


def _same_period(a: FeatureValue, b: FeatureValue) -> bool:
    return (a.period_start, a.period_end) == (b.period_start, b.period_end)


def _derived(name: str, value: float, *sources: FeatureValue) -> FeatureValue:
    return FeatureValue(
        name=name,
        value=value,
        as_of=max(x.as_of for x in sources),
        available_on=max(x.available_on for x in sources),
        quality_score=min(x.quality_score for x in sources),
        source_accessions=[accession for x in sources for accession in x.source_accessions],
        period_start=sources[0].period_start,
        period_end=sources[0].period_end,
    )


def _ratio(output: dict[str, FeatureValue], name: str, numerator: str, denominator: str) -> None:
    if (
        numerator in output
        and denominator in output
        and output[denominator].value
        and _same_period(output[numerator], output[denominator])
    ):
        numerator_value = output[numerator].value
        denominator_value = output[denominator].value
        assert numerator_value is not None and denominator_value is not None
        output[name] = _derived(
            name,
            numerator_value / denominator_value,
            output[numerator],
            output[denominator],
        )
