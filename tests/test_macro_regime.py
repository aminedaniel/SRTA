from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from smct_research.core.models import FeatureSnapshot
from smct_research.macro.models import MacroObservation
from smct_research.macro.regime import company_macro_sensitivity, derive_regime
from smct_research.providers.base import ProviderConfigurationError
from smct_research.providers.federal_reserve import FederalReserveProvider
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


def test_real_policy_rate_uses_trailing_cpi_year_over_year_percentage() -> None:
    values = [
        obs("EFFR", "2024-02-01", 5.25),
        obs("CPIAUCSL", "2023-02-01", 300, unit="index_1982_84_100", frequency="monthly"),
        obs("CPIAUCSL", "2024-02-01", 309, unit="index_1982_84_100", frequency="monthly"),
    ]
    result = derive_regime(values, date(2024, 2, 1))
    assert result["real_policy_rate_estimate"].value == pytest.approx(2.25)


def test_real_policy_rate_is_unknown_when_cpi_history_is_insufficient() -> None:
    values = [
        obs("EFFR", "2024-02-01", 5.25),
        obs("CPIAUCSL", "2024-02-01", 309, unit="index_1982_84_100", frequency="monthly"),
    ]
    assert derive_regime(values, date(2024, 2, 1))["real_policy_rate_estimate"].value is None


def test_revised_observation_is_not_visible_before_its_vintage() -> None:
    initial = obs("EFFR", "2024-01-01", 5.0, "2024-01-02")
    revised = obs("EFFR", "2024-01-01", 5.2, "2024-02-01")
    assert (
        derive_regime([initial, revised], date(2024, 1, 15))["effective_federal_funds_rate"].value
        == 5.0
    )
    assert (
        derive_regime([initial, revised], date(2024, 2, 2))["effective_federal_funds_rate"].value
        == 5.2
    )


def test_fred_registry_preserves_units_and_release_dates() -> None:
    payload = {
        "observations": [
            {
                "date": "2024-01-01",
                "realtime_start": "2024-01-05",
                "release_date": "2024-01-04",
                "value": "8000000",
            }
        ]
    }
    item = FederalReserveProvider.normalize_fred(payload, "WALCL")[0]
    assert (item.unit, item.frequency, item.publication_date, item.first_available_on) == (
        "millions_usd",
        "weekly",
        date(2024, 1, 4),
        date(2024, 1, 5),
    )


def test_alfred_fixture_cache_revision_and_user_agent_validation(tmp_path: Path) -> None:
    payload = (
        b'{"observations":[{"date":"2024-01-01","realtime_start":"2024-02-01","value":"4.0"}]}'
    )
    provider = FederalReserveProvider(
        tmp_path,
        api_key="key",
        user_agent="Test Research ops@example.org",
        transport=lambda u, h: payload,
    )
    first = provider.alfred_series("EFFR", "2024-02-01")
    assert provider.alfred_series("EFFR", "2024-02-01") == first
    normalized = provider.normalize_fred(first, "EFFR", vintage=True)
    assert normalized[0].revision_status.value == "revised"
    with pytest.raises(ProviderConfigurationError):
        FederalReserveProvider(tmp_path / "empty", api_key="key").fred_series("EFFR")


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
    assert -15 <= result.score <= 15
    assert "not trade instructions" in result.risks[0]
