from __future__ import annotations

import json
from datetime import UTC, datetime

from typer.testing import CliRunner

from smct_research.cli import app, default_registry
from smct_research.research.render import render_markdown
from smct_research.research.report import ResearchReportBuilder, signal_weighted_contribution
from smct_research.scoring.composite import CompositeResearchScorer
from smct_research.screening.io import load_feature_snapshots, load_universe
from smct_research.screening.service import BatchEvaluationService
from smct_research.screening.universe import UniversePolicy


def _report(ticker: str = "ACME"):
    as_of = datetime(2026, 7, 17, tzinfo=UTC)
    companies = load_universe(__import__("pathlib").Path("examples/screening/universe.json"))
    snapshots = load_feature_snapshots(__import__("pathlib").Path("examples/screening/features"))
    registry = default_registry()
    scorer = CompositeResearchScorer()
    ranked = BatchEvaluationService(registry, scorer).evaluate(
        companies, snapshots, UniversePolicy(), as_of, True
    )
    company = next(c for c in companies if c.ticker == ticker)
    selected = next(r for r in ranked if r.ticker == ticker)
    return ResearchReportBuilder(scorer, list(registry.all())).build(
        company, snapshots[ticker], selected, as_of
    )


def test_report_json_markdown_and_id_are_deterministic() -> None:
    one = _report()
    two = _report()
    assert one.report_id == two.report_id
    assert one.model_dump_json() == two.model_dump_json()
    assert render_markdown(one) == render_markdown(two)
    assert json.loads(one.model_dump_json())["report_id"] == one.report_id


def test_evidence_semantics_and_valuation_missing_is_not_zero() -> None:
    report = _report("ACME")
    assert any(item.startswith("A1:") for item in report.supporting_evidence)
    assert "A3" not in report.valuation_summary.valuation_compression_evidence
    assert report.valuation_summary.reverse_dcf_base_value == 25.0
    assert report.valuation_summary.dilution_or_share_count_risk is not None
    assert report.catalysts == ()
    assert "No evidence-backed catalysts" in report.catalyst_summary
    markdown = render_markdown(report)
    assert "Missing valuation fields" in markdown
    assert "|" in markdown


def test_contribution_helper_preserves_negative_sign() -> None:
    from smct_research.core.models import SignalDirection, SignalResult

    result = SignalResult(
        signal_id="NEG",
        ticker="ACME",
        score=-20,
        confidence=0.5,
        direction=SignalDirection.NEGATIVE,
        thesis="negative evidence",
    )
    assert signal_weighted_contribution(result, 1.5) == -15


def test_cli_report_outputs_and_no_traceback(tmp_path) -> None:
    db = tmp_path / "research.duckdb"
    out = tmp_path / "report.json"
    md = tmp_path / "report.md"
    result = CliRunner().invoke(
        app,
        [
            "report",
            "examples/screening/universe.json",
            "examples/screening/features",
            "--ticker",
            "ACME",
            "--as-of",
            "2026-07-17T00:00:00Z",
            "--output-json",
            str(out),
            "--output-markdown",
            str(md),
            "--database",
            str(db),
            "--save-report",
            "--create-thesis",
        ],
    )
    assert result.exit_code == 0, result.output
    assert out.exists() and md.exists()
    listed = CliRunner().invoke(app, ["thesis-list", str(db), "--ticker", "ACME"])
    assert listed.exit_code == 0 and "draft" in listed.output
    shown = CliRunner().invoke(
        app, ["thesis-show", str(db), "--ticker", "ACME", "--as-of", "2026-07-17T00:00:00Z"]
    )
    assert shown.exit_code == 0 and "thesis_id" in shown.output
    bad = CliRunner().invoke(
        app,
        [
            "report",
            "examples/screening/universe.json",
            "examples/screening/features",
            "--ticker",
            "NOPE",
            "--as-of",
            "bad",
        ],
    )
    assert bad.exit_code != 0 and "Traceback" not in bad.output
