from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest
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


def test_report_identity_uses_full_content_and_canonicalizes_unordered_fields() -> None:
    one = _report("ACME")
    data = one.model_dump(mode="python")
    data["provenance"] = {"z": "last", **data["provenance"]}
    from smct_research.research.models import (
        CompanyResearchReport,
        report_content_hash,
        report_id_for_payload,
    )

    data["report_id"] = report_id_for_payload(data)
    data["canonical_content_hash"] = report_content_hash(data)
    changed = CompanyResearchReport.model_validate(data)
    assert changed.report_id != one.report_id
    assert changed.canonical_content_hash != one.canonical_content_hash

    reordered = one.model_dump(mode="python")
    reordered["signal_assessments"] = tuple(reversed(reordered["signal_assessments"]))
    reordered["report_id"] = report_id_for_payload(reordered)
    reordered["canonical_content_hash"] = report_content_hash(reordered)
    assert (
        CompanyResearchReport.model_validate(reordered).model_dump_json() == one.model_dump_json()
    )

    reordered_ranked = one.model_dump(mode="python")
    reordered_ranked["supporting_evidence"] = tuple(
        reversed(reordered_ranked["supporting_evidence"])
    )
    reordered_ranked["report_id"] = report_id_for_payload(reordered_ranked)
    reordered_ranked["canonical_content_hash"] = report_content_hash(reordered_ranked)
    assert (
        CompanyResearchReport.model_validate(reordered_ranked).supporting_evidence
        != one.supporting_evidence
    )


def test_model_validation_and_input_mapping_defensive_copy() -> None:
    from pydantic import ValidationError

    from smct_research.research.models import (
        CompanyResearchReport,
        SignalAssessment,
        ValuationSummary,
    )

    metadata = {"nested": {"items": ["b", "a"]}}
    assessment = SignalAssessment(signal_id="X", score=1, confidence=0.5, metadata=metadata)
    metadata["nested"]["items"].append("c")
    assert assessment.metadata == {"nested": {"items": ("a", "b")}}
    with pytest.raises(ValidationError):
        SignalAssessment(signal_id="X", score=101, confidence=0.5)
    with pytest.raises(ValidationError):
        ValuationSummary(current_market_price=-1)
    report = _report("ACME")
    data = report.model_dump(mode="python")
    data["signal_assessments"] = (report.signal_assessments[0], report.signal_assessments[0])
    with pytest.raises(ValidationError):
        CompanyResearchReport.model_validate(data)
    data = report.model_dump(mode="python")
    data["universe_eligible"] = False
    data["exclusion_reasons"] = ()
    with pytest.raises(ValidationError):
        CompanyResearchReport.model_validate(data)


def test_unknown_signal_weight_is_explicitly_unavailable() -> None:
    report = _report("ACME")
    m1 = next(item for item in report.signal_assessments if item.signal_id == "M1")
    assert m1.weighted_contribution is None


def test_cli_persistence_requirements_and_json_list(tmp_path) -> None:
    runner = CliRunner()
    out = runner.invoke(
        app,
        [
            "report",
            "examples/screening/universe.json",
            "examples/screening/features",
            "--ticker",
            "ACME",
            "--as-of",
            "2026-07-17T00:00:00Z",
            "--save-report",
        ],
    )
    assert (
        out.exit_code != 0
        and "--save-report requires --database" in out.output
        and "Traceback" not in out.output
    )
    db = tmp_path / "research.duckdb"
    assert (
        runner.invoke(
            app,
            [
                "report",
                "examples/screening/universe.json",
                "examples/screening/features",
                "--ticker",
                "ACME",
                "--as-of",
                "2026-07-17T00:00:00Z",
                "--database",
                str(db),
                "--save-report",
                "--create-thesis",
            ],
        ).exit_code
        == 0
    )
    json_out = tmp_path / "theses.json"
    listed = runner.invoke(
        app, ["thesis-list", str(db), "--ticker", "ACME", "--output-json", str(json_out)]
    )
    assert listed.exit_code == 0 and "source_report_id=" in listed.output
    assert json.loads(json_out.read_text())[0]["ticker"] == "ACME"


def test_ranked_order_serializer_markdown_and_unknown_weight_diagnostic() -> None:
    from smct_research.research.render import serialize_report_json

    report = _report("ACME")
    assert report.missing_evidence and "M1: composite weight unavailable" in report.missing_evidence
    assert all(not item.startswith("M1:") for item in report.supporting_evidence)
    assert report.contextual_evidence
    support = list(report.supporting_evidence)
    assert support.index("A1: EV/Sales is 62% below its three-year median.") < support.index(
        "E2: Estimated disclosed purchase value: $500,000."
    )
    rendered_json = serialize_report_json(report)
    assert rendered_json.endswith("\n")
    assert rendered_json.index("A1: EV/Sales") < rendered_json.index("E2: Estimated")
    markdown = render_markdown(report)
    assert markdown.index("A1: EV/Sales") < markdown.index("E2: Estimated")
    assert "M1: composite weight unavailable" in markdown


def test_model_owned_mappings_are_immutable_and_hash_stable() -> None:
    from smct_research.research.models import report_content_hash

    report = _report("ACME")
    before = report_content_hash(report)
    with pytest.raises(TypeError):
        report.provenance["source"] = "changed"
    with pytest.raises(TypeError):
        report.universe_metadata["sector"] = "changed"
    with pytest.raises(TypeError):
        report.source_timestamps["financials"] = report.as_of
    with pytest.raises(TypeError):
        report.signal_assessments[0].metadata["new"] = "changed"
    assert report_content_hash(report) == before


def test_markdown_escaping_special_content() -> None:
    from smct_research.research.models import SignalAssessment

    data = _report("ACME").model_dump(mode="python")
    assessment = SignalAssessment.model_validate(data["signal_assessments"][0])
    data["signal_assessments"] = (
        assessment.model_copy(
            update={
                "signal_name": "Pipe | * _ ` [ ] < > #",
                "thesis": "Thesis | * _ ` [ ] < > #\nnext",
                "evidence": ("Evidence | * _ ` [ ] < > #\nnext",),
                "risks": ("Risk | * _ ` [ ] < > #\nnext",),
            }
        ),
    ) + data["signal_assessments"][1:]
    from smct_research.research.models import (
        CompanyResearchReport,
        report_content_hash,
        report_id_for_payload,
    )

    data["provenance"] = {"prov|*`#": "value|*`#"}
    data["report_id"] = report_id_for_payload(data)
    data["canonical_content_hash"] = report_content_hash(data)
    markdown = render_markdown(CompanyResearchReport.model_validate(data))
    assert "Pipe \\| \\* \\_ \\` \\[ \\] \\< \\> \\#" in markdown
    assert markdown.endswith("\n")
    assert "### Source timestamps" in markdown
