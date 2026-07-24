from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from smct_research.cli import default_registry
from smct_research.core.models import ThesisStatus
from smct_research.research.report import ResearchReportBuilder
from smct_research.research.thesis import (
    ALLOWED_TRANSITIONS,
    create_initial_thesis_record,
    transition_thesis,
)
from smct_research.scoring.composite import CompositeResearchScorer
from smct_research.screening.io import load_feature_snapshots, load_universe
from smct_research.screening.service import BatchEvaluationService
from smct_research.screening.universe import UniversePolicy
from smct_research.storage.duckdb_store import LocalAnalyticalStore


def _report():
    as_of = datetime(2026, 7, 17, tzinfo=UTC)
    companies = load_universe(Path("examples/screening/universe.json"))
    snapshots = load_feature_snapshots(Path("examples/screening/features"))
    registry = default_registry()
    scorer = CompositeResearchScorer()
    ranked = BatchEvaluationService(registry, scorer).evaluate(
        companies, snapshots, UniversePolicy(), as_of, True
    )
    company = next(c for c in companies if c.ticker == "ACME")
    selected = next(r for r in ranked if r.ticker == "ACME")
    return ResearchReportBuilder(scorer, list(registry.all())).build(
        company, snapshots["ACME"], selected, as_of
    )


def test_initial_thesis_record_stable_and_draft() -> None:
    rec1 = create_initial_thesis_record(_report())
    rec2 = create_initial_thesis_record(_report())
    assert rec1.version == 1 and rec1.status == ThesisStatus.DRAFT
    assert rec1.thesis_id == rec2.thesis_id
    assert rec1.canonical_content_hash == rec2.canonical_content_hash
    assert rec1.thesis.supporting_evidence


def test_all_allowed_transitions_from_policy() -> None:
    base = create_initial_thesis_record(_report())
    t = datetime(2026, 7, 18, tzinfo=UTC)
    active = transition_thesis(base, ThesisStatus.ACTIVE, "review complete", t, t)
    assert active.version == 2
    for src, targets in ALLOWED_TRANSITIONS.items():
        if not targets:
            continue
        record = active.model_copy(update={"status": src, "version": 2, "prior_version": 1})
        for target in targets:
            assert transition_thesis(record, target, "reason", t, t).status == target


def test_disallowed_and_terminal_transitions() -> None:
    base = create_initial_thesis_record(_report())
    t = datetime(2026, 7, 18, tzinfo=UTC)
    with pytest.raises(ValueError):
        transition_thesis(base, ThesisStatus.STRENGTHENING, "bad", t, t)
    active = transition_thesis(base, ThesisStatus.ACTIVE, "review", t, t)
    terminal = transition_thesis(active, ThesisStatus.INVALIDATED, "invalidated", t, t)
    with pytest.raises(ValueError):
        transition_thesis(terminal, ThesisStatus.ACTIVE, "bad", t, t)
    with pytest.raises(ValueError):
        transition_thesis(active, ThesisStatus.WEAKENING, "", t, t)


def test_duckdb_round_trip_idempotency_and_point_in_time(tmp_path) -> None:
    report = _report()
    store = LocalAnalyticalStore(tmp_path / "r.duckdb")
    try:
        store.store_research_report(report)
        store.store_research_report(report)
        assert store.load_research_report(report.report_id).report_id == report.report_id
        rec = create_initial_thesis_record(report)
        store.store_thesis_record(rec)
        store.store_thesis_record(rec)
        active = transition_thesis(
            rec,
            ThesisStatus.ACTIVE,
            "review",
            datetime(2026, 7, 18, tzinfo=UTC),
            datetime(2026, 7, 19, tzinfo=UTC),
        )
        store.store_thesis_record(active)
        assert [r.version for r in store.load_thesis_history(ticker="ACME")] == [1, 2]
        assert store.load_latest_thesis(ticker="ACME").status == ThesisStatus.ACTIVE
        assert (
            store.load_thesis_as_of(datetime(2026, 7, 18, 12, tzinfo=UTC), ticker="ACME").status
            == ThesisStatus.DRAFT
        )
        assert (
            store.load_thesis_as_of(datetime(2026, 7, 19, tzinfo=UTC), ticker="ACME").status
            == ThesisStatus.ACTIVE
        )
        bad = active.model_copy(update={"version": 4, "prior_version": 3})
        with pytest.raises(ValueError):
            store.store_thesis_record(bad)
    finally:
        store.close()
