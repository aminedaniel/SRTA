from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from smct_research.core.models import FeatureSnapshot
from smct_research.macro.models import MacroObservation
from smct_research.macro.regime import company_macro_sensitivity, derive_regime
from smct_research.providers.base import ProviderConfigurationError
from smct_research.providers.federal_reserve import FRED_SERIES, FederalReserveProvider
from smct_research.signals.fed_regime import FederalReserveRegimeSignal


def obs(
    series: str,
    day: str,
    value: float,
    available: str | None = None,
    *,
    unit: str = "percent",
    frequency: str = "weekly",
) -> MacroObservation:
    public = date.fromisoformat(available or day)
    return MacroObservation(
        series_id=series,
        observation_date=date.fromisoformat(day),
        publication_date=public,
        first_available_on=public,
        retrieved_at=datetime.now(UTC),
        value=value,
        unit=unit,
        source="fixture",
        frequency=frequency,
        provenance_url="fixture",
    )


def test_regime_uses_delayed_publication_not_observation_date() -> None:
    values = [
        obs("WALCL", "2024-01-01", 8000, "2024-01-04", unit="millions_usd"),
        obs("WALCL", "2024-04-01", 8160, "2024-04-11", unit="millions_usd"),
    ]
    assert derive_regime(values, date(2024, 4, 5))["liquidity_regime"].value == "unknown"
    assert derive_regime(values, date(2024, 4, 11))["liquidity_regime"].value == "expansion"


def test_real_policy_rate_uses_same_calendar_month_not_an_11_month_value() -> None:
    cpi = [
        obs(
            "CPIAUCSL",
            f"2023-{month:02d}-01",
            300 + month,
            unit="index_1982_84_100",
            frequency="monthly",
        )
        for month in range(3, 13)
    ]
    cpi += [
        obs("CPIAUCSL", "2023-02-01", 300, unit="index_1982_84_100", frequency="monthly"),
        obs("CPIAUCSL", "2024-01-01", 450, unit="index_1982_84_100", frequency="monthly"),
        obs("CPIAUCSL", "2024-02-01", 309, unit="index_1982_84_100", frequency="monthly"),
    ]
    result = derive_regime([obs("EFFR", "2024-02-01", 5.25), *cpi], date(2024, 2, 1))
    assert result["real_policy_rate_estimate"].value == pytest.approx(2.25)


def test_real_policy_rate_is_unknown_when_calendar_year_history_is_insufficient() -> None:
    values = [
        obs("EFFR", "2024-02-01", 5.25),
        obs("CPIAUCSL", "2023-03-01", 300, unit="index_1982_84_100", frequency="monthly"),
        obs("CPIAUCSL", "2024-02-01", 309, unit="index_1982_84_100", frequency="monthly"),
    ]
    feature = derive_regime(values, date(2024, 2, 1))["real_policy_rate_estimate"]
    assert feature.value is None and feature.quality_score == 0


def test_revised_observation_is_not_visible_before_its_vintage() -> None:
    initial, revised = (
        obs("EFFR", "2024-01-01", 5.0, "2024-01-02"),
        obs("EFFR", "2024-01-01", 5.2, "2024-02-01"),
    )
    assert (
        derive_regime([initial, revised], date(2024, 1, 15))["effective_federal_funds_rate"].value
        == 5.0
    )
    assert (
        derive_regime([initial, revised], date(2024, 2, 2))["effective_federal_funds_rate"].value
        == 5.2
    )


def test_current_fred_is_not_mislabeled_as_historical_availability() -> None:
    payload = {
        "observations": [{"date": "2024-01-01", "realtime_start": "2026-01-01", "value": "8000000"}]
    }
    item = FederalReserveProvider.normalize_current_fred(payload, "WALCL")[0]
    assert item.vintage_date == date(2026, 1, 1)
    assert item.publication_date is None and item.first_available_on is None
    assert not item.point_in_time_eligible


def test_alfred_requires_release_calendar_and_uses_it_for_historical_pit() -> None:
    payload = {
        "observations": [{"date": "2024-01-01", "realtime_start": "2024-02-01", "value": "4.0"}]
    }
    with pytest.raises(ValueError, match="original public availability"):
        FederalReserveProvider.normalize_alfred(payload, "EFFR", availability_by_observation={})
    item = FederalReserveProvider.normalize_alfred(
        payload, "EFFR", availability_by_observation={date(2024, 1, 1): date(2024, 1, 2)}
    )[0]
    assert item.first_available_on == date(2024, 1, 2) and item.point_in_time_eligible


def test_fred_cache_ttl_refreshes_and_preserves_snapshots(tmp_path: Path) -> None:
    calls = 0

    def transport(url: str, headers: dict[str, str]) -> bytes:
        nonlocal calls
        calls += 1
        return f'{{"version": {calls}}}'.encode()

    provider = FederalReserveProvider(
        tmp_path,
        api_key="key",
        user_agent="SMCT ops@example.org",
        cache_ttl=timedelta(seconds=0),
        transport=transport,
    )
    assert provider.fred_series("EFFR")["version"] == 1
    assert provider.fred_series("EFFR")["version"] == 2
    assert provider.fred_series("EFFR", refresh=True)["version"] == 3
    assert len(list((tmp_path / "fred/snapshots").glob("EFFR-*.json"))) == 3


def test_cached_fred_fixture_is_deterministic_without_credentials(tmp_path: Path) -> None:
    path = tmp_path / "fred/EFFR.json"
    path.parent.mkdir(parents=True)
    path.write_text('{"cached": true}')
    assert FederalReserveProvider(tmp_path).fred_series("EFFR") == {"cached": True}


def test_liquidity_quality_ignores_missing_inactive_emergency_series() -> None:
    stable = [
        obs("WALCL", "2024-01-01", 8000, unit="millions_usd"),
        obs("WALCL", "2024-04-01", 8039, unit="millions_usd"),
    ]
    result = derive_regime(stable, date(2024, 4, 1))
    assert (
        result["liquidity_regime"].value == "stable"
        and result["liquidity_regime"].quality_score == 1
    )
    emergency = derive_regime(
        [*stable, obs("H41_EMERGENCY", "2024-04-01", 1, unit="millions_usd")], date(2024, 4, 1)
    )
    assert emergency["liquidity_regime"].value == "emergency_liquidity"


def test_series_registry_and_user_agent_validation() -> None:
    assert (
        FRED_SERIES["T10Y2Y"].unit == "percentage_points"
        and FRED_SERIES["RRPONTSYD"].frequency == "daily"
    )
    with pytest.raises(ProviderConfigurationError, match="contact email"):
        FederalReserveProvider(
            Path("/tmp/no-cache"), api_key="key", user_agent="SMCT Research"
        ).fred_series("EFFR")


def test_company_sensitivity_uses_refinancing_ratio_not_raw_company_size() -> None:
    base = {
        "net_cash_or_debt": -1,
        "total_debt": 100,
        "debt_due_12m": 25,
        "interest_expense_burden": 0.03,
    }
    large = {
        key: value * 1_000_000 if key in {"total_debt", "debt_due_12m"} else value
        for key, value in base.items()
    }
    assert company_macro_sensitivity(base) == company_macro_sensitivity(large)
    assert company_macro_sensitivity(
        {**base, "floating_rate_exposure": 0.8}
    ) > company_macro_sensitivity(base)


def test_regime_signal_is_bounded_research_context() -> None:
    snapshot = FeatureSnapshot(
        ticker="TEST",
        values={
            "liquidity_regime": "contraction",
            "monetary_policy_regime": "tightening",
            "regime_confidence": 1,
            "net_cash_or_debt": -1,
            "free_cash_flow": -1,
            "external_financing_dependency": 1,
        },
    )
    result = FederalReserveRegimeSignal().evaluate(snapshot)
    assert -15 <= result.score <= 15 and "not trade instructions" in result.risks[0]
