from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest
from typer.testing import CliRunner

from smct_research.cli import app
from smct_research.core.models import FeatureSnapshot
from smct_research.financials.models import ReportingPeriodType
from smct_research.financials.normalize import derive_features, normalize_company_facts
from smct_research.providers.base import ProviderResponseError
from smct_research.providers.sec_edgar import SecEdgarProvider
from smct_research.signals.financial_quality import FinancialQualitySignal

FIXTURE = Path("tests/fixtures/sec/companyfacts.json")


def fact(value, start="2024-04-01", end="2024-06-30", form="10-Q", filed="2024-08-01"):
    return {"val": value, "start": start, "end": end, "form": form, "filed": filed, "accn": filed}


def payload(revenue, cashflow=None):
    gaap = {
        "Revenues": {"units": {"USD": revenue}},
        "OperatingIncomeLoss": {"units": {"USD": [fact(20)]}},
    }
    if cashflow:
        gaap["NetCashProvidedByUsedInOperatingActivities"] = {"units": {"USD": cashflow}}
        gaap["PaymentsToAcquirePropertyPlantAndEquipment"] = {
            "units": {"USD": [fact(10, start="2024-01-01")]}
        }
    return {"cik": "1234", "facts": {"us-gaap": gaap}}


def test_ytd_quarter_and_annual_comparatives_are_not_mixed():
    raw = payload(
        [
            fact(100),
            fact(180, start="2024-01-01"),
            fact(80, start="2023-04-01", end="2023-06-30"),
            fact(70, start="2023-07-01", end="2023-09-30"),
        ],
        [fact(50, start="2024-01-01")],
    )
    observations = normalize_company_facts(raw)
    assert any(o.period_type == ReportingPeriodType.YEAR_TO_DATE for o in observations)
    features = derive_features(observations, date(2024, 8, 2))
    assert features["revenue"].value == 100
    assert features["yoy_revenue_growth"].value == pytest.approx(0.25)
    assert features["operating_margin"].value == pytest.approx(0.20)
    assert features["free_cash_flow"].value == 40
    assert "free_cash_flow_margin" not in features
    # A quarter included in a 10-K is still a quarter, not an annual duration.
    item = normalize_company_facts(payload([fact(100, form="10-K")]))[0]
    assert item.period_type == ReportingPeriodType.QUARTERLY


def test_debt_components_not_mislabeled_as_total_and_units_filtered():
    raw = json.loads(FIXTURE.read_text())
    raw["facts"]["us-gaap"]["Revenues"]["units"]["EUR"] = [fact(999999)]
    features = derive_features(normalize_company_facts(raw), date(2024, 8, 2))
    assert features["revenue"].value == 110
    assert "total_debt" not in features and "net_cash_or_debt" not in features
    assert "reported_long_term_debt" not in features
    raw["facts"]["us-gaap"]["LongTermDebtNoncurrent"] = {
        "units": {
            "USD": [
                {
                    "val": 50,
                    "end": "2023-12-31",
                    "form": "10-K",
                    "filed": "2024-02-20",
                    "accn": "0001",
                }
            ]
        }
    }
    features = derive_features(normalize_company_facts(raw), date(2024, 8, 2))
    assert features["reported_long_term_debt"].value == 60


def test_provider_refreshes_cache_and_rejects_nonobject(tmp_path):
    calls = []

    def transport(url, headers):
        calls.append(headers)
        return FIXTURE.read_bytes()

    first = SecEdgarProvider("SMCT test@example.com", tmp_path, transport=transport)
    first.company_facts("1234")
    first.company_facts("1234")
    SecEdgarProvider(
        "SMCT test@example.com", tmp_path, transport=transport, refresh=True
    ).company_facts("1234")
    assert len(calls) == 2 and calls[0]["Accept-Encoding"] == "identity"
    with pytest.raises(ProviderResponseError, match="object"):
        SecEdgarProvider(
            "SMCT test@example.com", tmp_path, refresh=True, transport=lambda *_: b"[]"
        ).company_facts("1234")


def test_quality_signal_missing_and_negative_cash_flow():
    signal = FinancialQualitySignal()
    snap = FeatureSnapshot(
        ticker="TEST", values={"yoy_revenue_growth": 0.2, "operating_margin": 0.2}
    )
    first = signal.evaluate(snap)
    assert first.metadata["observed_components"] == 2
    assert first.confidence == pytest.approx(0.6)
    snap.values.update(
        free_cash_flow_margin=-0.5,
        yoy_diluted_share_growth=0.3,
        stock_based_compensation_pct_revenue=0.5,
    )
    second = signal.evaluate(snap)
    assert second.score < first.score


def test_offline_sec_to_ranked_research_end_to_end(tmp_path):
    raw = tmp_path / "raw"
    raw.mkdir()
    (raw / "CIK0000001234.json").write_bytes(FIXTURE.read_bytes())
    universe = tmp_path / "universe.json"
    universe.write_text(
        json.dumps(
            [
                {
                    "ticker": "TEST",
                    "cik": "1234",
                    "name": "Fixture company",
                    "market_cap_usd": 1e9,
                    "sector": "Technology",
                    "country": "US",
                    "exchange": "NASDAQ",
                    "is_active": True,
                    "security_type": "common_stock",
                    "average_daily_dollar_volume": 5e6,
                }
            ]
        )
    )
    features = tmp_path / "features"
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "ingest-sec",
            str(universe),
            "--raw-directory",
            str(raw),
            "--as-of",
            "2024-03-02",
            "--output-dir",
            str(features),
            "--evidence-db",
            str(tmp_path / "e.duckdb"),
        ],
    )
    assert result.exit_code == 0, result.output
    snapshot = FeatureSnapshot.model_validate_json((features / "TEST.json").read_text())
    assert snapshot.values["revenue"] == 110
    assert all(stamp <= snapshot.as_of for stamp in snapshot.source_as_of.values())
    result = runner.invoke(
        app,
        [
            "research",
            str(universe),
            str(features),
            "--as-of",
            "2024-03-02",
            "--output-dir",
            str(tmp_path / "report"),
            "--db",
            str(tmp_path / "r.sqlite3"),
        ],
    )
    assert result.exit_code == 0, result.output
    ranked = json.loads((tmp_path / "report/ranked.json").read_text())[0]
    assert ranked["composite_score"] is not None
    assert "Q1" in {signal["signal_id"] for signal in ranked["signal_results"]}


def test_live_import_requires_contact_config(tmp_path, monkeypatch):
    monkeypatch.delenv("SMCT_SEC_USER_AGENT", raising=False)
    result = CliRunner().invoke(
        app,
        [
            "ingest-sec",
            "examples/screening/universe.json",
            "--evidence-db",
            str(tmp_path / "e.duckdb"),
        ],
    )
    assert result.exit_code != 0 and "contact" in result.output and "email" in result.output
