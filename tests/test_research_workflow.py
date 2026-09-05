from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest
from typer.testing import CliRunner

from smct_research.cli import app
from smct_research.core.models import FeatureSnapshot, SignalDirection, SignalResult
from smct_research.research.builder import build_report, report_id
from smct_research.research.changes import compare_runs
from smct_research.research.models import CompanyResearchReport, SignalAvailability
from smct_research.research.render import render_report
from smct_research.research.store import ResearchStore
from smct_research.research.thesis import (
    Catalyst,
    InvalidationRule,
    ThesisDocument,
    draft_thesis,
    review_thesis,
)
from smct_research.scoring.composite import CompositeResearchScorer
from smct_research.screening.io import load_feature_snapshots, load_universe
from smct_research.screening.registry import default_registry
from smct_research.screening.service import BatchEvaluationService
from smct_research.screening.universe import UniversePolicy

FIXTURE = Path("examples/screening")
STAMP = datetime(2026, 7, 17, tzinfo=UTC)


def reports_at(stamp: datetime = STAMP, *, snapshots=None):
    companies = load_universe(FIXTURE / "universe.json")
    snapshots = snapshots if snapshots is not None else load_feature_snapshots(FIXTURE / "features")
    registry, scorer = default_registry(), CompositeResearchScorer()
    rows = BatchEvaluationService(registry, scorer).evaluate(
        companies, snapshots, UniversePolicy(), stamp, include_ineligible=True
    )
    by_ticker = {c.ticker: c for c in companies}
    return [
        build_report(by_ticker[row.ticker], snapshots.get(row.ticker), row, registry, scorer)
        for row in rows
    ]


def test_report_reconciles_scores_and_preserves_missing_evidence():
    for report in reports_at():
        if report.composite_score is not None:
            assert report.composite_score == pytest.approx(
                50 + sum(a.weighted_contribution or 0 for a in report.signal_assessments)
            )
        missing = [
            a for a in report.signal_assessments if a.availability == SignalAvailability.UNAVAILABLE
        ]
        assert all(a.score is None and a.confidence is None for a in missing)
        assert set(report.unavailable_signals) == {a.signal_id for a in missing}
        assert report_id(report) == report_id(
            CompanyResearchReport.model_validate_json(report.model_dump_json())
        )
        assert report_id(report) in render_report(report)


def test_report_does_not_publish_future_valuation():
    company = load_universe(FIXTURE / "universe.json")[0]
    snapshot = FeatureSnapshot(
        ticker=company.ticker,
        as_of=STAMP + timedelta(days=1),
        values={"current_price": 100, "dcf_base_value_per_share": 200},
    )
    report = next(
        r
        for r in reports_at(snapshots={company.ticker: snapshot})
        if r.company.ticker == company.ticker
    )
    assert report.composite_score is None
    assert report.valuation.current_market_price is None
    assert report.valuation.reverse_dcf_base_value is None


def test_report_rejects_mismatched_ticker_and_source_hash_changes():
    companies = load_universe(FIXTURE / "universe.json")
    snapshots = load_feature_snapshots(FIXTURE / "features")
    registry, scorer = default_registry(), CompositeResearchScorer()
    row = BatchEvaluationService(registry, scorer).evaluate(
        companies, snapshots, UniversePolicy(), STAMP
    )[0]
    company = next(c for c in companies if c.ticker == row.ticker)
    with pytest.raises(ValueError, match="same ticker"):
        build_report(companies[-1], snapshots.get(company.ticker), row, registry, scorer)
    first = build_report(company, snapshots[company.ticker], row, registry, scorer)
    snapshots[company.ticker].values["unscored_observation"] = 123
    second = build_report(company, snapshots[company.ticker], row, registry, scorer)
    assert report_id(first) != report_id(second)


def test_saved_runs_are_idempotent_and_time_selectable(tmp_path):
    first, second = reports_at(), reports_at(STAMP + timedelta(days=7))
    with ResearchStore(tmp_path / "research.sqlite3") as store:
        identity = store.save_run(first, STAMP, {})
        assert identity == store.save_run(first, STAMP, {})
        store.save_run(second, STAMP + timedelta(days=7), {})
        assert store.report("ALPH", STAMP).as_of == STAMP
        assert store.run(STAMP)["id"] == identity
        assert store.report("ALPH").as_of > STAMP
        assert store.connection.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 2
        with pytest.raises(ValueError, match="duplicate"):
            store.save_run([first[0], first[0]], STAMP, {})


def test_thesis_revisions_prevent_lost_edits_and_wrong_report_links(tmp_path):
    reports = reports_at()
    with ResearchStore(tmp_path / "research.sqlite3") as store:
        store.save_run(reports, STAMP, {})
        draft = draft_thesis(reports[0])
        first = store.save_thesis(draft)
        assert first.revision == 1
        with pytest.raises(ValueError, match="revision conflict"):
            store.save_thesis(draft)
        second = store.save_thesis(first.model_copy(update={"change_note": "Updated evidence"}))
        assert second.revision == 2 and second.created_at == first.created_at
        assert store.thesis(first.ticker, 1).change_note == "Initial draft"
        wrong = draft_thesis(reports[1]).model_copy(update={"report_id": first.report_id})
        with pytest.raises(ValueError, match="this ticker"):
            store.save_thesis(wrong)


def test_active_thesis_requires_reasoning_and_ordered_scenarios():
    value = draft_thesis(reports_at()[0]).model_dump()
    value.update(status="active")
    with pytest.raises(ValueError, match="variant perception"):
        ThesisDocument.model_validate(value)
    value.update(status="draft", valuation_low=200, valuation_base=100)
    with pytest.raises(ValueError, match="ordered"):
        ThesisDocument.model_validate(value)


def test_rules_and_due_catalysts_do_not_change_thesis_status():
    thesis = draft_thesis(reports_at()[0])
    thesis.rules = [
        InvalidationRule(
            feature="yoy_revenue_growth",
            operator="lt",
            threshold=0,
            description="Revenue is shrinking",
        )
    ]
    thesis.catalysts = [Catalyst(description="Earnings review", due_date=date(2026, 7, 16))]
    snapshot = FeatureSnapshot(
        ticker=thesis.ticker, as_of=STAMP, values={"yoy_revenue_growth": -0.1}
    )
    review = review_thesis(thesis, snapshot, STAMP)
    assert review["rule_alerts"][0]["state"] == "triggered"
    assert len(review["due_catalysts"]) == 1
    assert thesis.status.value == "draft"
    for altered in [
        None,
        snapshot.model_copy(update={"as_of": STAMP + timedelta(days=1)}),
        snapshot.model_copy(update={"as_of": STAMP - timedelta(days=91)}),
    ]:
        assert review_thesis(thesis, altered, STAMP)["rule_alerts"][0]["state"] == "unavailable"


def test_change_report_distinguishes_coverage_change_and_removed_company(tmp_path):
    snapshots = load_feature_snapshots(FIXTURE / "features")
    first = reports_at()
    snapshots.pop("ALPH")
    second = [
        r
        for r in reports_at(STAMP + timedelta(days=7), snapshots=snapshots)
        if r.company.ticker != "BETA"
    ]
    with ResearchStore(tmp_path / "research.sqlite3") as store:
        store.save_run(first, STAMP, {})
        store.save_run(second, STAMP + timedelta(days=7), {})
        changes = compare_runs(store.run(STAMP), store.run())
        rows = {r["ticker"]: r for r in changes["companies"]}
        assert rows["ALPH"]["score_change"] is None and not rows["ALPH"]["comparable"]
        assert rows["BETA"]["state"] == "removed" and rows["BETA"]["score_change"] is None
        assert compare_runs(store.run(STAMP), store.run(), set())["companies"] == []
        with pytest.raises(ValueError, match="increasing"):
            compare_runs(store.run(), store.run())


def test_bad_numbers_duplicate_scores_and_negative_valuation():
    snapshot = FeatureSnapshot(ticker="TEST", values={"x": float("nan")})
    with pytest.raises(ValueError, match="finite"):
        snapshot.require_float("x")
    with pytest.raises(ValueError, match="nonnegative"):
        CompositeResearchScorer({"A1": -1})
    result = SignalResult(
        ticker="TEST",
        signal_id="A1",
        score=5,
        confidence=1,
        direction=SignalDirection.POSITIVE,
        thesis="fixture",
    )
    with pytest.raises(ValueError, match="duplicate"):
        CompositeResearchScorer().score([result, result])
    from smct_research.signals.valuation_compression import ValuationCompressionSignal

    snapshot.values = {
        "ev_sales_current": 12,
        "ev_sales_3y_median": 4,
        "revenue_growth_current": 0.1,
        "revenue_growth_3y_median": 0.2,
    }
    assert ValuationCompressionSignal().evaluate(snapshot).direction == SignalDirection.NEGATIVE


def test_cli_screen_to_report_to_thesis_to_changes(tmp_path):
    runner = CliRunner()
    database = str(tmp_path / "research.sqlite3")
    output = tmp_path / "reports"
    args = [
        "research",
        str(FIXTURE / "universe.json"),
        str(FIXTURE / "features"),
        "--db",
        database,
        "--output-dir",
        str(output),
        "--top",
        "1",
    ]
    first = runner.invoke(app, [*args, "--as-of", STAMP.isoformat()])
    assert first.exit_code == 0, first.output
    assert (output / "index.md").exists() and len(list((output / "companies").glob("*.md"))) == 1
    with ResearchStore(Path(database)) as store:
        assert len(store.run()["reports"]) == 6  # A display filter must not truncate history.
    report = runner.invoke(app, ["report", "ALPH", "--db", database, "--json"])
    assert report.exit_code == 0, report.output
    assert json.loads(report.output)["company"]["ticker"] == "ALPH"
    draft = tmp_path / "thesis.json"
    assert (
        runner.invoke(app, ["thesis", "init", "ALPH", str(draft), "--db", database]).exit_code == 0
    )
    saved = runner.invoke(app, ["thesis", "save", str(draft), "--db", database])
    assert saved.exit_code == 0, saved.output
    assert json.loads(draft.read_text())["revision"] == 1
    second = runner.invoke(app, [*args, "--as-of", (STAMP + timedelta(days=7)).isoformat()])
    assert second.exit_code == 0, second.output
    changed = runner.invoke(
        app,
        ["changes", "--since", STAMP.isoformat(), "--db", database, "--json", "--watchlist-only"],
    )
    assert changed.exit_code == 0, changed.output
    assert [row["ticker"] for row in json.loads(changed.output)["companies"]] == ["ALPH"]


def test_cli_errors_are_actionable(tmp_path):
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "research",
            str(FIXTURE / "universe.json"),
            "missing-features",
            "--db",
            str(tmp_path / "db.sqlite3"),
        ],
    )
    assert result.exit_code != 0 and "Features directory does not exist" in result.output
    empty = tmp_path / "empty.json"
    empty.write_text(FeatureSnapshot(ticker="TEST", values={}).model_dump_json())
    result = runner.invoke(app, ["evaluate", str(empty)])
    assert result.exit_code != 0 and "No signals have enough evidence" in result.output


def test_settings_work_in_both_commands_and_reject_unknown_signals(tmp_path):
    from smct_research.screening.configuration import load_settings

    policy, scorer = load_settings(Path("config/research.yaml"))
    assert scorer.weights["Q1"] == 1.15 and policy.maximum_market_cap_usd == 20e9
    config = tmp_path / "settings.json"
    config.write_text(json.dumps({"signal_weights": {"A1": 0}}))
    runner = CliRunner()
    for command in ("screen", "research"):
        args = [
            command,
            str(FIXTURE / "universe.json"),
            str(FIXTURE / "features"),
            "--as-of",
            STAMP.isoformat(),
            "--config",
            str(config),
        ]
        if command == "research":
            args.extend(
                ["--output-dir", str(tmp_path / "output"), "--db", str(tmp_path / "r.sqlite3")]
            )
        result = runner.invoke(app, args)
        assert result.exit_code == 0, result.output
    config.write_text(json.dumps({"signal_weights": {"unknown": 1}}))
    with pytest.raises(ValueError, match="Unknown signal"):
        load_settings(config)


def test_zero_weight_evidence_is_not_a_neutral_score():
    scorer = CompositeResearchScorer({s.id: 0 for s in default_registry().all()})
    rows = BatchEvaluationService(default_registry(), scorer).evaluate(
        load_universe(FIXTURE / "universe.json"),
        load_feature_snapshots(FIXTURE / "features"),
        UniversePolicy(),
        STAMP,
    )
    assert all(row.composite_score is None and row.rank is None for row in rows)


def test_csv_text_is_safe_to_open_in_a_spreadsheet(tmp_path):
    import csv

    from smct_research.screening.io import write_csv

    companies = load_universe(FIXTURE / "universe.json")
    companies[0].company_name = '=HYPERLINK("https://example.com","company")'
    rows = BatchEvaluationService(default_registry(), CompositeResearchScorer()).evaluate(
        companies, load_feature_snapshots(FIXTURE / "features"), UniversePolicy(), STAMP
    )
    path = tmp_path / "output" / "ranked.csv"
    write_csv(path, rows)
    with path.open(newline="") as handle:
        row = next(item for item in csv.DictReader(handle) if item["ticker"] == companies[0].ticker)
    assert row["company_name"].startswith("'=")


def test_policy_serialization_is_order_independent():
    first = UniversePolicy(technology_keywords={"software", "technology"})
    second = UniversePolicy(technology_keywords={"technology", "software"})
    assert first.model_dump(mode="json") == second.model_dump(mode="json")
    assert first.model_dump(mode="json")["technology_keywords"] == ["software", "technology"]
