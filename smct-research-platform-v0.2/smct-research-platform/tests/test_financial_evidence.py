import json
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

pytest.importorskip("duckdb")

from smct_research.financials.normalize import derive_features, normalize_company_facts
from smct_research.providers.base import ProviderConfigurationError
from smct_research.providers.sec_edgar import SecEdgarProvider
from smct_research.storage.duckdb_store import LocalAnalyticalStore

FIXTURE = Path(__file__).parent / "fixtures/sec/companyfacts.json"


def test_normalizes_amendment_and_prevents_lookahead() -> None:
    facts = json.loads(FIXTURE.read_text())
    observations = normalize_company_facts(facts, retrieved_at=datetime(2024, 3, 2, tzinfo=UTC))
    assert any(x.filing.is_superseded for x in observations if x.metric == "revenue")
    before_amendment = derive_features(observations, date(2024, 2, 25))
    assert before_amendment["revenue"].value == 100
    after_amendment = derive_features(observations, date(2024, 3, 2))
    assert after_amendment["revenue"].value == 110
    assert after_amendment["free_cash_flow"].value == 15
    assert after_amendment["missing_data_quality_score"].value < 1


def test_provider_uses_cache_without_network(tmp_path: Path) -> None:
    calls = 0

    def transport(url: str, headers: dict[str, str]) -> bytes:
        nonlocal calls
        calls += 1
        return FIXTURE.read_bytes()

    provider = SecEdgarProvider("SMCT test@example.com", tmp_path, transport=transport)
    assert provider.company_facts("1234")["cik"] == "1234"
    provider.company_facts(1234)
    assert calls == 1


def test_provider_requires_compliant_user_agent(tmp_path: Path) -> None:
    with pytest.raises(ProviderConfigurationError):
        SecEdgarProvider("SMCT", tmp_path)


def test_local_store_exports_parquet(tmp_path: Path) -> None:
    observations = normalize_company_facts(json.loads(FIXTURE.read_text()))
    store = LocalAnalyticalStore(tmp_path / "evidence.duckdb")
    store.store_observations(observations)
    destination = tmp_path / "features.parquet"
    store.export_features_parquet(derive_features(observations, date(2024, 5, 2)), destination)
    store.close()
    assert destination.exists()
