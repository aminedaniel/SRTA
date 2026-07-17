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


def test_future_snapshot_and_source_are_not_evaluated() -> None:
    company = load_universe(FIXTURES / "universe.json")[0]
    values = {
        "ev_sales_current": 3,
        "ev_sales_3y_median": 8,
        "revenue_growth_current": 0.3,
        "revenue_growth_3y_median": 0.35,
    }
    evaluation = datetime(2026, 7, 17, tzinfo=UTC)
    future = datetime(2026, 7, 18, tzinfo=UTC)
    for snapshot in (
        FeatureSnapshot(ticker=company.ticker, as_of=future, values=values),
        FeatureSnapshot(
            ticker=company.ticker,
            as_of=evaluation,
            values=values,
            source_as_of={"financials": future},
        ),
    ):
        result = _service().evaluate(
            [company], {company.ticker: snapshot}, UniversePolicy(), evaluation
        )[0]
        assert result.signals_evaluated == 0
        assert result.signals_unavailable == 6
        assert result.composite_score is None
        assert result.point_in_time_eligibility_warnings


def test_timestamp_and_ticker_normalization_and_deterministic_evaluation() -> None:
    company = load_universe(FIXTURES / "universe.json")[0]
    snapshot = FeatureSnapshot(
        ticker=company.ticker.lower(),
        as_of="2026-07-01T00:00:00",  # type: ignore[arg-type]
        values={
            "ev_sales_current": 3,
            "ev_sales_3y_median": 8,
            "revenue_growth_current": 0.3,
            "revenue_growth_3y_median": 0.35,
        },
        source_as_of={"financials": "2026-07-01T01:00:00+01:00"},  # type: ignore[arg-type]
    )
    assert snapshot.ticker == company.ticker and snapshot.as_of.tzinfo == UTC
    assert snapshot.source_as_of["financials"] == datetime(2026, 7, 1, tzinfo=UTC)
    evaluation = datetime(2026, 7, 17)
    result = _service().evaluate(
        [company], {company.ticker: snapshot}, UniversePolicy(), evaluation
    )[0]
    assert result.evaluation_timestamp.tzinfo == UTC
    assert {item.evaluated_at for item in result.signal_results} == {result.evaluation_timestamp}


def test_unknown_classifications_and_duplicate_inputs_are_rejected(tmp_path: Path) -> None:
    company = load_universe(FIXTURES / "universe.json")[0]
    company.exchange = company.country = company.security_type = None
    reasons = UniversePolicy().exclusion_reasons(company)
    assert {"unknown_exchange", "unknown_country", "unknown_security_type"} <= set(reasons)
    duplicate = tmp_path / "duplicate.json"
    duplicate.write_text((FIXTURES / "universe.json").read_text().replace('"BETA"', '"ALPH"'))
    with pytest.raises(ValueError, match="Duplicate universe"):
        load_universe(duplicate)
    feature_dir = tmp_path / "features"
    feature_dir.mkdir()
    (feature_dir / "one.json").write_text((FIXTURES / "features" / "ALPH.json").read_text())
    (feature_dir / "two.json").write_text((FIXTURES / "features" / "ALPH.json").read_text())
    from smct_research.screening.io import load_feature_snapshots

    with pytest.raises(ValueError, match="Duplicate feature"):
        load_feature_snapshots(feature_dir)


def test_assembler_prevents_look_ahead_and_normalizes_timestamps() -> None:
    from smct_research.screening.features import FeatureSnapshotAssembler
    from smct_research.screening.models import FeatureAssemblyInput

    evidence = FeatureAssemblyInput(
        ticker="alph",
        as_of="2026-07-01T00:00:00",  # type: ignore[arg-type]
        source_as_of={"sec": "2026-07-02T00:00:00Z"},  # type: ignore[arg-type]
    )
    assembler = FeatureSnapshotAssembler()
    with pytest.raises(ValueError, match="cannot precede"):
        assembler.assemble(evidence, datetime(2026, 7, 1, tzinfo=UTC))
    snapshot = assembler.assemble(evidence, datetime(2026, 7, 2))
    assert snapshot.ticker == "ALPH" and snapshot.as_of == datetime(2026, 7, 2, tzinfo=UTC)


def test_supporting_explanations_are_positive_contribution_ranked() -> None:
    companies = load_universe(FIXTURES / "universe.json")
    from smct_research.screening.io import load_feature_snapshots

    result = _service().evaluate(
        companies, load_feature_snapshots(FIXTURES / "features"), UniversePolicy()
    )[0]
    weights = CompositeResearchScorer().weights
    expected = sorted(
        (item for item in result.signal_results if item.direction.value == "positive"),
        key=lambda item: (
            -(item.score * item.confidence * weights.get(item.signal_id, 1.0)),
            item.signal_id,
        ),
    )[:3]
    assert result.top_supporting_explanations == [
        f"{item.signal_id}: {item.thesis}" for item in expected
    ]
