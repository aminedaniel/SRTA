from datetime import UTC, datetime
from pathlib import Path

import pytest

from smct_research.cli import default_registry
from smct_research.core.models import FeatureSnapshot
from smct_research.scoring.composite import CompositeResearchScorer
from smct_research.screening.io import load_universe, write_csv, write_json
from smct_research.screening.service import BatchEvaluationService
from smct_research.screening.universe import UniversePolicy

FIXTURES = Path("examples/screening")


def _service() -> BatchEvaluationService:
    return BatchEvaluationService(default_registry(), CompositeResearchScorer())


def test_universe_input_and_exclusion_reasons() -> None:
    companies = load_universe(FIXTURES / "universe.json")
    fund = next(company for company in companies if company.ticker == "FUND")
    assert "excluded_security_type:etf" in UniversePolicy().exclusion_reasons(fund)


def test_batch_ranking_missing_coverage_and_point_in_time() -> None:
    companies = load_universe(FIXTURES / "universe.json")
    snapshot = FeatureSnapshot(
        ticker="BETA",
        as_of=datetime(2026, 7, 1, tzinfo=UTC),
        values={
            "ev_sales_current": 3,
            "ev_sales_3y_median": 8,
            "revenue_growth_current": 0.3,
            "revenue_growth_3y_median": 0.35,
        },
    )
    results = _service().evaluate(
        companies, {"BETA": snapshot}, UniversePolicy(), datetime(2026, 7, 17, tzinfo=UTC)
    )
    beta = next(item for item in results if item.ticker == "BETA")
    assert beta.rank == 1 and beta.signals_evaluated == 1 and beta.signals_unavailable == 5
    assert beta.feature_completeness_percentage < 100


def test_deterministic_tie_order_and_empty_universe() -> None:
    assert _service().evaluate([], {}, UniversePolicy()) == []
    companies = load_universe(FIXTURES / "universe.json")[:2]
    values = {
        "ev_sales_current": 4,
        "ev_sales_3y_median": 8,
        "revenue_growth_current": 0.2,
        "revenue_growth_3y_median": 0.2,
    }
    snapshots = {
        company.ticker: FeatureSnapshot(ticker=company.ticker, values=values)
        for company in companies
    }
    assert [
        result.ticker for result in _service().evaluate(companies, snapshots, UniversePolicy())
    ] == ["ALPH", "BETA"]


def test_json_csv_output_and_malformed_input(tmp_path: Path) -> None:
    result = _service().evaluate([], {}, UniversePolicy())
    write_json(tmp_path / "result.json", result)
    write_csv(tmp_path / "result.csv", result)
    assert (tmp_path / "result.json").read_text() == "[]\n"
    assert "ticker" in (tmp_path / "result.csv").read_text()
    bad = tmp_path / "bad.json"
    bad.write_text("{}")
    with pytest.raises(ValueError):
        load_universe(bad)
