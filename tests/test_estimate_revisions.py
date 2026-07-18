from __future__ import annotations

import csv
import json
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest
from typer.testing import CliRunner

from smct_research.cli import app, default_registry
from smct_research.core.models import FeatureSnapshot, SignalDirection
from smct_research.estimates.models import (
    ConsensusEstimate,
    EstimateBasis,
    EstimateMetric,
    EstimatePeriod,
)
from smct_research.estimates.service import calculate_features
from smct_research.providers.base import ProviderResponseError
from smct_research.providers.estimates import OfflineEstimateProvider
from smct_research.scoring.composite import CompositeResearchScorer
from smct_research.screening.features import FeatureSnapshotAssembler
from smct_research.screening.models import FeatureAssemblyInput
from smct_research.signals.estimate_revision_velocity import ConsensusEstimateRevisionSignal
from smct_research.storage.duckdb_store import LocalAnalyticalStore


def ts(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def estimate(
    record_id: str,
    available_at: str,
    consensus: float,
    *,
    provider: str = "p1",
    metric: EstimateMetric = EstimateMetric.EPS,
    target_period_end: date = date(2026, 12, 31),
    period_type: EstimatePeriod = EstimatePeriod.FISCAL_YEAR,
    basis: EstimateBasis = EstimateBasis.NON_GAAP,
    unit: str = "USD/share",
    currency: str = "USD",
    horizon_label: str | None = "FY1",
    analyst_count: int | None = 5,
    high: float | None = None,
    low: float | None = None,
    standard_deviation: float | None = 0.10,
    estimate_breadth: float | None = 0.80,
) -> ConsensusEstimate:
    return ConsensusEstimate(
        ticker="ACME",
        metric=metric,
        target_period_end=target_period_end,
        period_type=period_type,
        horizon_label=horizon_label,
        consensus=consensus,
        unit=unit,
        currency=currency,
        basis=basis,
        analyst_count=analyst_count,
        high=high if high is not None else consensus + 0.50,
        low=low if low is not None else consensus - 0.50,
        standard_deviation=standard_deviation,
        estimate_breadth=estimate_breadth,
        provider=provider,
        provider_record_id=record_id,
        source_identifier=f"source:{provider}:{record_id}",
        published_at=ts(available_at) - timedelta(hours=1),
        retrieved_at=ts(available_at),
        available_at=ts(available_at),
    )


def history(**overrides: object) -> list[ConsensusEstimate]:
    values = [
        ("r90", "2026-01-01T00:00:00Z", 1.00),
        ("r60", "2026-01-31T00:00:00Z", 1.10),
        ("r30", "2026-03-02T00:00:00Z", 1.20),
        ("r07", "2026-03-25T00:00:00Z", 1.25),
        ("cur", "2026-04-01T00:00:00Z", 1.50),
        ("future", "2026-04-02T00:00:00Z", 9.99),
    ]
    return [
        estimate(record_id, available, consensus, **overrides)
        for record_id, available, consensus in values
    ]


def write_json(path: Path, records: list[ConsensusEstimate]) -> None:
    path.write_text(json.dumps([record.model_dump(mode="json") for record in records]))


def test_point_in_time_lookback_tolerance_and_no_future_selection() -> None:
    features = calculate_features(
        history(), "ACME", EstimateMetric.EPS, ts("2026-04-01T00:00:00Z"), tolerance_days=0
    )
    assert features.current.provider_record_id == "cur"
    assert features.revisions[30].prior and features.revisions[30].prior.provider_record_id == "r30"
    assert features.revisions[60].prior and features.revisions[60].prior.provider_record_id == "r60"
    assert features.revisions[90].prior and features.revisions[90].prior.provider_record_id == "r90"
    assert features.revisions[7].prior and features.revisions[7].prior.provider_record_id == "r07"
    assert features.current.consensus == 1.50

    sparse = [
        estimate("cur", "2026-04-01T00:00:00Z", 1.5),
        estimate("old", "2026-02-20T00:00:00Z", 1.0),
    ]
    missing = calculate_features(
        sparse, "ACME", EstimateMetric.EPS, ts("2026-04-01T00:00:00Z"), tolerance_days=3
    )
    assert missing.revisions[30].prior is None
    assert "missing_historical_comparison" in missing.revisions[30].diagnostics

    inside = calculate_features(
        sparse, "ACME", EstimateMetric.EPS, ts("2026-04-01T00:00:00Z"), tolerance_days=15
    )
    assert inside.revisions[30].prior and inside.revisions[30].prior.provider_record_id == "old"


def test_series_isolation_and_explicit_ambiguity() -> None:
    records = history() + history(provider="p2")
    with pytest.raises(ValueError, match="provider, basis, and period_type"):
        calculate_features(records, "ACME", EstimateMetric.EPS, ts("2026-04-01T00:00:00Z"))
    selected = calculate_features(
        records, "ACME", EstimateMetric.EPS, ts("2026-04-01T00:00:00Z"), provider="p2"
    )
    assert selected.current.provider == "p2"

    with pytest.raises(ValueError, match="period_end is required"):
        calculate_features(
            history() + history(target_period_end=date(2027, 12, 31)),
            "ACME",
            EstimateMetric.EPS,
            ts("2026-04-01T00:00:00Z"),
        )

    with pytest.raises(ValueError, match="provider, basis, and period_type"):
        calculate_features(
            history() + history(basis=EstimateBasis.GAAP),
            "ACME",
            EstimateMetric.EPS,
            ts("2026-04-01T00:00:00Z"),
        )

    with pytest.raises(ValueError, match="provider, basis, and period_type"):
        calculate_features(
            history() + history(period_type=EstimatePeriod.QUARTER),
            "ACME",
            EstimateMetric.EPS,
            ts("2026-04-01T00:00:00Z"),
        )


def test_rollover_diagnostic_is_scoped_to_stable_horizon_series() -> None:
    base = history()
    other_provider_rollover = estimate(
        "p2_roll", "2026-03-31T00:00:00Z", 2.0, provider="p2", target_period_end=date(2027, 12, 31)
    )
    features = calculate_features(
        base + [other_provider_rollover],
        "ACME",
        EstimateMetric.EPS,
        ts("2026-04-01T00:00:00Z"),
        provider="p1",
    )
    assert "target_period_rollover" not in features.diagnostics

    same_series_rollover = estimate(
        "roll", "2026-03-31T00:00:00Z", 2.0, target_period_end=date(2027, 12, 31)
    )
    features = calculate_features(
        base + [same_series_rollover],
        "ACME",
        EstimateMetric.EPS,
        ts("2026-04-01T00:00:00Z"),
        period_end=date(2026, 12, 31),
    )
    assert "target_period_rollover" in features.diagnostics


def test_revision_math_acceleration_streak_and_changes() -> None:
    records = [
        estimate(
            "r60",
            "2026-01-31T00:00:00Z",
            1.00,
            analyst_count=4,
            high=1.3,
            low=0.8,
            standard_deviation=0.20,
            estimate_breadth=0.50,
        ),
        estimate(
            "r30",
            "2026-03-02T00:00:00Z",
            1.10,
            analyst_count=5,
            high=1.5,
            low=0.9,
            standard_deviation=0.25,
            estimate_breadth=0.60,
        ),
        estimate(
            "cur",
            "2026-04-01T00:00:00Z",
            1.40,
            analyst_count=7,
            high=1.8,
            low=1.1,
            standard_deviation=0.30,
            estimate_breadth=0.75,
        ),
    ]
    features = calculate_features(records, "ACME", EstimateMetric.EPS, ts("2026-04-01T00:00:00Z"))
    assert features.revisions[30].value == pytest.approx((1.40 - 1.10) / 1.10)
    assert features.acceleration == pytest.approx(((1.40 - 1.10) / 1.10) - ((1.10 - 1.00) / 1.00))
    assert features.streak == 2
    assert features.analyst_count_change_30d == 2
    assert features.high_change_30d == pytest.approx(0.3)
    assert features.low_change_30d == pytest.approx(0.2)
    assert features.dispersion_change_30d == pytest.approx(0.05)
    assert features.dispersion_change_ratio_30d == pytest.approx(0.2)
    assert features.breadth_change_30d == pytest.approx(0.15)

    falling = [
        estimate("a", "2026-03-01T00:00:00Z", 1.3),
        estimate("b", "2026-03-15T00:00:00Z", 1.2),
        estimate("c", "2026-04-01T00:00:00Z", 1.1),
    ]
    assert (
        calculate_features(falling, "ACME", EstimateMetric.EPS, ts("2026-04-01T00:00:00Z")).streak
        == -2
    )


def test_symmetric_revisions_near_zero_and_sign_crossings() -> None:
    near_zero = calculate_features(
        [
            estimate("prior", "2026-03-02T00:00:00Z", 0.0),
            estimate("cur", "2026-04-01T00:00:00Z", 0.2),
        ],
        "ACME",
        EstimateMetric.EPS,
        ts("2026-04-01T00:00:00Z"),
    )
    assert near_zero.revisions[30].used_symmetric is True
    assert "near_zero_denominator" in near_zero.revisions[30].diagnostics

    crossing = calculate_features(
        [
            estimate("prior", "2026-03-02T00:00:00Z", -0.2),
            estimate("cur", "2026-04-01T00:00:00Z", 0.2),
        ],
        "ACME",
        EstimateMetric.EPS,
        ts("2026-04-01T00:00:00Z"),
    )
    assert crossing.sign_transition == "negative_to_positive_transition"
    assert crossing.revisions[30].used_symmetric is True


def test_normalized_dispersion_changes_scale_equivalently() -> None:
    small = calculate_features(
        [
            estimate("prior", "2026-03-02T00:00:00Z", 1, standard_deviation=0.10),
            estimate("cur", "2026-04-01T00:00:00Z", 1, standard_deviation=0.15),
        ],
        "ACME",
        EstimateMetric.EPS,
        ts("2026-04-01T00:00:00Z"),
    )
    large = calculate_features(
        [
            estimate("prior", "2026-03-02T00:00:00Z", 10, standard_deviation=1.00),
            estimate("cur", "2026-04-01T00:00:00Z", 10, standard_deviation=1.50),
        ],
        "ACME",
        EstimateMetric.EPS,
        ts("2026-04-01T00:00:00Z"),
    )
    assert small.dispersion_change_ratio_30d == pytest.approx(0.5)
    assert large.dispersion_change_ratio_30d == pytest.approx(0.5)
    signal = ConsensusEstimateRevisionSignal()
    assert signal.evaluate(snapshot_from_features(small)).score == pytest.approx(
        signal.evaluate(snapshot_from_features(large)).score
    )

    no_prior = calculate_features(
        [estimate("cur", "2026-04-01T00:00:00Z", 1, standard_deviation=0.15)],
        "ACME",
        EstimateMetric.EPS,
        ts("2026-04-01T00:00:00Z"),
    )
    assert no_prior.dispersion_change_ratio_30d is None

    zero_prior = calculate_features(
        [
            estimate("prior", "2026-03-02T00:00:00Z", 1, standard_deviation=0.0),
            estimate("cur", "2026-04-01T00:00:00Z", 1, standard_deviation=0.2),
        ],
        "ACME",
        EstimateMetric.EPS,
        ts("2026-04-01T00:00:00Z"),
    )
    assert zero_prior.dispersion_change_ratio_30d == pytest.approx(2.0)


def snapshot_from_features(features) -> FeatureSnapshot:
    assembler = FeatureSnapshotAssembler()
    evidence = FeatureAssemblyInput(ticker="ACME", as_of=ts("2026-04-01T00:00:00Z"))
    return assembler.assemble(assembler.add_estimate_revision_features(evidence, features))


def test_provenance_and_assembly_order_independence() -> None:
    eps = calculate_features(history(), "ACME", EstimateMetric.EPS, ts("2026-04-01T00:00:00Z"))
    revenue = calculate_features(
        history(metric=EstimateMetric.REVENUE, unit="USDm"),
        "ACME",
        EstimateMetric.REVENUE,
        ts("2026-04-01T00:00:00Z"),
    )
    assembler = FeatureSnapshotAssembler()
    evidence = FeatureAssemblyInput(ticker="ACME", as_of=ts("2026-04-01T00:00:00Z"))
    eps_then_revenue = assembler.assemble(
        assembler.add_estimate_revision_features(
            assembler.add_estimate_revision_features(evidence, eps), revenue
        )
    )
    revenue_then_eps = assembler.assemble(
        assembler.add_estimate_revision_features(
            assembler.add_estimate_revision_features(evidence, revenue), eps
        )
    )
    signal = ConsensusEstimateRevisionSignal()
    assert signal.evaluate(eps_then_revenue).score == signal.evaluate(revenue_then_eps).score
    assert "estimate_revision_quality_score" not in eps_then_revenue.values
    assert eps_then_revenue.values["eps_revision_quality_score"] == eps.quality.score
    assert eps_then_revenue.values["revenue_revision_quality_score"] == revenue.quality.score
    assert any(key.startswith("estimates:eps:30d") for key in eps_then_revenue.sources)
    assert any(key.startswith("estimates:eps:current") for key in eps_then_revenue.source_as_of)

    future_prior = eps.model_copy(
        update={
            "revisions": {
                30: eps.revisions[30].model_copy(
                    update={"prior": estimate("f", "2026-04-02T00:00:00Z", 1.0)}
                )
            }
        }
    )
    with pytest.raises(ValueError, match="newer than feature snapshot"):
        assembler.add_estimate_revision_features(evidence, future_prior)


def test_offline_provider_formats_and_duplicate_validation(tmp_path: Path) -> None:
    records = history()[:2]
    json_path = tmp_path / "estimates.json"
    write_json(json_path, records)
    assert len(OfflineEstimateProvider(json_path).fetch_estimate_history("ACME")) == 2

    object_path = tmp_path / "object.json"
    object_path.write_text(
        json.dumps({"records": [record.model_dump(mode="json") for record in records]})
    )
    assert len(OfflineEstimateProvider(object_path).fetch_estimate_history("ACME")) == 2

    jsonl_path = tmp_path / "estimates.jsonl"
    jsonl_path.write_text(
        "\n".join(json.dumps(record.model_dump(mode="json")) for record in records)
    )
    assert len(OfflineEstimateProvider(jsonl_path).fetch_estimate_history("ACME")) == 2

    csv_path = tmp_path / "estimates.csv"
    with csv_path.open("w", newline="") as handle:
        rows = [record.model_dump(mode="json") for record in records]
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    assert len(OfflineEstimateProvider(csv_path).fetch_estimate_history("ACME")) == 2

    scalar_path = tmp_path / "scalar.json"
    scalar_path.write_text("null")
    with pytest.raises(ProviderResponseError):
        OfflineEstimateProvider(scalar_path).fetch_estimate_history("ACME")

    duplicate_path = tmp_path / "duplicate.json"
    write_json(duplicate_path, [records[0], records[0]])
    assert len(OfflineEstimateProvider(duplicate_path).fetch_estimate_history("ACME")) == 1

    conflicting_id = records[0].model_copy(update={"consensus": records[0].consensus + 1})
    write_json(duplicate_path, [records[0], conflicting_id])
    with pytest.raises(ProviderResponseError, match="duplicate provider record ID"):
        OfflineEstimateProvider(duplicate_path).fetch_estimate_history("ACME")

    same_local_id_other_provider = records[0].model_copy(update={"provider": "p2"})
    write_json(duplicate_path, [records[0], same_local_id_other_provider])
    assert len(OfflineEstimateProvider(duplicate_path).fetch_estimate_history("ACME")) == 2

    logical_conflict = records[0].model_copy(update={"provider_record_id": "different"})
    write_json(duplicate_path, [records[0], logical_conflict])
    with pytest.raises(ProviderResponseError, match="logical"):
        OfflineEstimateProvider(duplicate_path).fetch_estimate_history("ACME")


def test_duckdb_persistence_round_trip_and_conflicts(tmp_path: Path) -> None:
    store = LocalAnalyticalStore(tmp_path / "local.duckdb")
    item = estimate("r1", "2026-04-01T00:00:00Z", 1.2, horizon_label="FY1", estimate_breadth=0.72)
    other_provider = item.model_copy(update={"provider": "p2"})
    store.store_estimate_snapshots([item, item, other_provider])
    loaded = store.load_estimate_snapshots()
    assert [x.model_dump(mode="json") for x in loaded] == [
        item.model_dump(mode="json"),
        other_provider.model_dump(mode="json"),
    ]
    with pytest.raises(ValueError, match="provider record ID"):
        store.store_estimate_snapshots([item.model_copy(update={"consensus": 2.0})])
    with pytest.raises(ValueError, match="logical"):
        store.store_estimate_snapshots([item.model_copy(update={"provider_record_id": "new-id"})])
    store.close()


def test_cli_selectors_json_csv_and_controlled_errors(tmp_path: Path) -> None:
    runner = CliRunner()
    path = tmp_path / "series.json"
    write_json(
        path,
        history()
        + history(provider="p2")
        + history(
            provider="p3",
            basis=EstimateBasis.GAAP,
            period_type=EstimatePeriod.QUARTER,
            target_period_end=date(2026, 3, 31),
        ),
    )

    ambiguous = runner.invoke(
        app, ["revisions", str(path), "--ticker", "ACME", "--as-of", "2026-04-01T00:00:00Z"]
    )
    assert ambiguous.exit_code != 0
    assert "required" in ambiguous.output or "provider, basis, and period_type" in ambiguous.output
    assert "Traceback" not in ambiguous.output

    output_json = tmp_path / "out.json"
    output_csv = tmp_path / "out.csv"
    selected = runner.invoke(
        app,
        [
            "revisions",
            str(path),
            "--ticker",
            "ACME",
            "--as-of",
            "2026-04-01T00:00:00Z",
            "--provider",
            "p3",
            "--basis",
            "gaap",
            "--period-type",
            "quarter",
            "--period-end",
            "2026-03-31",
            "--output-json",
            str(output_json),
            "--output-csv",
            str(output_csv),
        ],
    )
    assert selected.exit_code == 0, selected.output
    payload = json.loads(output_json.read_text())
    assert payload["current"]["provider"] == "p3"
    assert output_csv.read_text().startswith("ticker,metric,current,quality_score")

    invalid_date = runner.invoke(
        app, ["revisions", str(path), "--ticker", "ACME", "--as-of", "not-a-date"]
    )
    assert invalid_date.exit_code != 0
    assert "Invalid value" in invalid_date.output

    bad_path = tmp_path / "bad.json"
    bad_path.write_text("{")
    bad = runner.invoke(
        app, ["revisions", str(bad_path), "--ticker", "ACME", "--as-of", "2026-04-01T00:00:00Z"]
    )
    assert bad.exit_code != 0
    assert "invalid estimate file" in bad.output
    assert "Traceback" not in bad.output


def test_a3_metric_scoped_quality_scoring_and_composite_confidence() -> None:
    signal = ConsensusEstimateRevisionSignal()
    snapshot = FeatureSnapshot(
        ticker="ACME",
        values={
            "eps_revision_30d": 0.2,
            "eps_revision_quality_score": 0.5,
            "revenue_revision_30d": 0.1,
            "revenue_revision_quality_score": 0.01,
            "eps_revision_acceleration": 0.05,
            "eps_revision_streak": 2,
            "eps_dispersion_change_ratio_30d": -0.1,
            "eps_consensus_age_days": 2,
        },
    )
    result = signal.evaluate(snapshot)
    assert result.direction == SignalDirection.POSITIVE
    assert result.confidence == 0.5
    assert (
        signal.evaluate(
            snapshot.model_copy(
                update={"values": {**snapshot.values, "revenue_revision_quality_score": 1.0}}
            )
        ).confidence
        == 0.5
    )

    negative = signal.evaluate(
        snapshot.model_copy(
            update={
                "values": {
                    **snapshot.values,
                    "eps_revision_30d": -0.3,
                    "eps_revision_acceleration": -0.1,
                    "revenue_revision_30d": -0.1,
                    "eps_revision_streak": -2,
                    "eps_dispersion_change_ratio_30d": 0.5,
                }
            }
        )
    )
    assert negative.direction == SignalDirection.NEGATIVE

    neutral = signal.evaluate(
        snapshot.model_copy(
            update={"values": {"eps_revision_30d": 0.0, "eps_revision_quality_score": 0.8}}
        )
    )
    assert neutral.direction == SignalDirection.NEUTRAL

    composite = CompositeResearchScorer().score([result])
    assert composite.score == pytest.approx(50 + result.score / 2)
    assert default_registry().evaluate_all(FeatureSnapshot(ticker="MISS", values={})) == []
