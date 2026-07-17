from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from smct_research.core.models import FeatureSnapshot
from smct_research.macro.models import MacroObservation
from smct_research.macro.regime import company_macro_sensitivity, derive_regime
from smct_research.providers.base import ProviderConfigurationError
from smct_research.providers.federal_reserve import FederalReserveProvider
from smct_research.signals.fed_regime import FederalReserveRegimeSignal


def obs(series: str, day: str, value: float, available: str | None = None) -> MacroObservation:
    return MacroObservation(
        series_id=series,
        observation_date=date.fromisoformat(day),
        available_on=date.fromisoformat(available or day),
        retrieved_at=datetime.now(UTC),
        value=value,
        unit="percent",
        source="fixture",
        frequency="weekly",
        provenance_url="fixture",
    )


def test_regime_is_point_in_time_and_handles_missing_series() -> None:
    values = [
        obs("EFFR", "2024-01-01", 5),
        obs("EFFR", "2024-07-01", 5.5),
        obs("WALCL", "2024-01-01", 8000),
        obs("WALCL", "2024-07-01", 8100, "2024-07-10"),
    ]
    before_release = derive_regime(values, date(2024, 7, 5))
    assert before_release["liquidity_regime"].value == "contraction"
    after_release = derive_regime(values, date(2024, 7, 10))
    assert after_release["liquidity_regime"].value == "expansion"
    assert after_release["macro_data_quality_score"].value is not None


def test_alfred_fixture_cache_and_revision(tmp_path: Path) -> None:
    payload = (
        b'{"observations":[{"date":"2024-01-01","realtime_start":"2024-02-01","value":"4.0"}]}'
    )
    provider = FederalReserveProvider(tmp_path, api_key="key", transport=lambda u, h: payload)
    first = provider.alfred_series("EFFR", "2024-02-01")
    second = provider.alfred_series("EFFR", "2024-02-01")
    normalized = provider.normalize_fred(first, "EFFR", vintage=True)
    assert second == first and normalized[0].available_on == date(2024, 2, 1)
    assert normalized[0].revision_status.value == "revised"


def test_uncached_fred_requires_key_and_h41_keeps_emergency_facility(tmp_path: Path) -> None:
    with pytest.raises(ProviderConfigurationError):
        FederalReserveProvider(tmp_path).fred_series("EFFR")
    release = FederalReserveProvider.normalize_h41(
        {
            "observations": [
                {
                    "series_id": "H41_EMERGENCY",
                    "date": "2023-03-15",
                    "value": "100",
                    "unit": "millions_usd",
                }
            ]
        },
        "fixture",
        datetime(2023, 3, 16, tzinfo=UTC),
    )
    assert release.observations[0].series_id == "H41_EMERGENCY"


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
    assert company_macro_sensitivity(snapshot.values) > 0


def test_regime_fixtures_cover_all_classifications() -> None:
    import json

    fixture = Path(__file__).parent / "fixtures/macro/regimes.json"
    regimes = json.loads(fixture.read_text())
    assert {item["policy"] for item in regimes.values()} == {
        "tightening",
        "restrictive_stable",
        "easing",
        "neutral",
    }
    assert {item["liquidity"] for item in regimes.values()} == {"expansion", "contraction"}
