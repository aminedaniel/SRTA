from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
from math import inf, nan

import pytest
from pydantic import ValidationError

from smct_research.research import (
    CompanyIdentity,
    CompanyResearchReport,
    SignalAssessment,
    SignalAvailability,
    ValuationSummary,
)


def assessment(signal_id: str = "b") -> SignalAssessment:
    return SignalAssessment(
        signal_id=signal_id,
        signal_name="Signal",
        score=10,
        confidence=0.5,
        direction="positive",
        weighted_contribution=1.2,
        thesis="thesis",
        evidence=["e1"],
        risks=["r1"],
        metadata={"nested": {"k": "v"}, "items": ["a"]},
        evaluated_at=datetime(2026, 7, 24, 9, tzinfo=timezone(timedelta(hours=-4))),
        availability=SignalAvailability.AVAILABLE,
        stale_evidence_warnings=["stale"],
        point_in_time_warnings=["pit"],
    )


def company(**kwargs: object) -> CompanyIdentity:
    data = {"ticker": " acme ", "name": "Acme", "market_cap_usd": 1.0}
    data.update(kwargs)
    return CompanyIdentity(**data)


def report(**kwargs: object) -> CompanyResearchReport:
    data = {
        "schema_version": "1.0",
        "company": company(),
        "as_of": datetime(2026, 7, 24, 12),
        "universe_eligible": True,
        "feature_completeness_percentage": 75,
        "signal_assessments": [assessment("b"), assessment("a")],
        "supporting_evidence": ["s1", "s2"],
        "contradictory_evidence": ["c1", "c2"],
        "contextual_evidence": [],
        "key_risks": ["r1", "r2"],
        "catalysts": [],
        "invalidation_conditions": ["i1", "i2"],
        "provenance": {"source": "fixture", "nested": {"key": "value"}},
        "source_timestamps": {
            "src": datetime(2026, 7, 24, 9, tzinfo=timezone(timedelta(hours=-4)))
        },
    }
    data.update(kwargs)
    return CompanyResearchReport(**data)


def test_normalization() -> None:
    item = assessment("  sig-1  ")
    assert item.signal_id == "sig-1"
    assert item.evaluated_at == datetime(2026, 7, 24, 13, tzinfo=UTC)
    rpt = report()
    assert rpt.company.ticker == "ACME"
    assert rpt.as_of == datetime(2026, 7, 24, 12, tzinfo=UTC)
    assert rpt.source_timestamps["src"] == datetime(2026, 7, 24, 13, tzinfo=UTC)


@pytest.mark.parametrize(
    ("factory", "kwargs"),
    [
        (SignalAssessment, {"signal_id": "   "}),
        (CompanyIdentity, {"ticker": " ", "name": "Acme"}),
        (CompanyIdentity, {"ticker": "ACME", "name": " "}),
        (SignalAssessment, {"signal_id": "x", "score": 101}),
        (SignalAssessment, {"signal_id": "x", "confidence": 1.1}),
        (
            CompanyResearchReport,
            {
                "company": company(),
                "schema_version": "1",
                "as_of": datetime.now(UTC),
                "universe_eligible": True,
                "feature_completeness_percentage": 1,
                "composite_score": 101,
            },
        ),
        (
            CompanyResearchReport,
            {
                "company": company(),
                "schema_version": "1",
                "as_of": datetime.now(UTC),
                "universe_eligible": True,
                "feature_completeness_percentage": 1,
                "composite_confidence": 1.1,
            },
        ),
        (
            CompanyResearchReport,
            {
                "company": company(),
                "schema_version": "1",
                "as_of": datetime.now(UTC),
                "universe_eligible": True,
                "feature_completeness_percentage": 101,
            },
        ),
        (
            CompanyResearchReport,
            {
                "company": company(),
                "schema_version": "1",
                "as_of": datetime.now(UTC),
                "universe_eligible": True,
                "feature_completeness_percentage": 1,
                "rank": 0,
            },
        ),
        (SignalAssessment, {"signal_id": "x", "weighted_contribution": inf}),
        (SignalAssessment, {"signal_id": "x", "score": nan}),
        (ValuationSummary, {"current_market_price": -1}),
        (ValuationSummary, {"reverse_dcf_base_value": -1}),
        (CompanyIdentity, {"ticker": "ACME", "name": "Acme", "market_cap_usd": -1}),
    ],
)
def test_validation_rejections(factory: type, kwargs: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        factory(**kwargs)


def test_report_validation_rejections() -> None:
    with pytest.raises(ValidationError, match="duplicate signal IDs"):
        report(signal_assessments=[assessment("a"), assessment(" a ")])
    with pytest.raises(ValidationError, match="duplicate signal IDs"):
        report(unavailable_signals=["a", " a "])
    with pytest.raises(ValidationError, match="exclusion reason"):
        report(universe_eligible=False, exclusion_reasons=[])


def test_missing_data_semantics() -> None:
    valuation = ValuationSummary()
    assert valuation.current_market_price is None
    assert valuation.reverse_dcf_base_value is None
    identity = company(sector=None, industry=None, exchange=None, country=None, cik=None)
    rpt = report(company=identity, valuation=None)
    assert rpt.valuation is None
    assert rpt.catalysts == ()
    assert rpt.contextual_evidence == ()
    assert rpt.company.sector is None and rpt.company.cik is None


def test_immutability() -> None:
    source = {"source": "original", "nested": {"key": "value"}, "items": ["a"]}
    rpt = report(provenance=source)
    with pytest.raises(TypeError):
        rpt.provenance["source"] = "changed"
    with pytest.raises(TypeError):
        rpt.provenance["nested"]["key"] = "changed"
    assert isinstance(rpt.provenance["items"], tuple)
    source["source"] = "changed"
    source["nested"]["key"] = "changed"
    source["items"].append("b")
    assert rpt.provenance["source"] == "original"
    assert rpt.provenance["nested"]["key"] == "value"
    assert rpt.provenance["items"] == ("a",)
    with pytest.raises(ValidationError):
        rpt.company.ticker = "NEW"
    with pytest.raises(AttributeError):
        rpt.supporting_evidence.append("new")
    item = assessment()
    with pytest.raises(TypeError):
        item.metadata["nested"]["k"] = "changed"
    assert isinstance(item.metadata["items"], tuple)


def test_ordering() -> None:
    rpt = report(signal_assessments=[assessment("c"), assessment("a"), assessment("b")])
    assert [item.signal_id for item in rpt.signal_assessments] == ["a", "b", "c"]
    assert rpt.supporting_evidence == ("s1", "s2")
    assert rpt.contradictory_evidence == ("c1", "c2")
    assert rpt.key_risks == ("r1", "r2")
    assert rpt.invalidation_conditions == ("i1", "i2")


def test_frozen_mapping_blocks_all_common_mutation_paths() -> None:
    rpt = report(provenance={"x": 0, "nested": {"k": "v"}, "items": ["a"]})
    mapping = rpt.provenance
    with pytest.raises(TypeError):
        mapping["x"] = 1  # type: ignore[index]
    with pytest.raises(TypeError):
        del mapping["x"]  # type: ignore[attr-defined]
    for method_call in (
        lambda: mapping.update({"x": 1}),  # type: ignore[attr-defined]
        lambda: mapping.setdefault("x", 1),  # type: ignore[attr-defined]
        lambda: mapping.pop("x"),  # type: ignore[attr-defined]
        lambda: mapping.popitem(),  # type: ignore[attr-defined]
        lambda: mapping.clear(),  # type: ignore[attr-defined]
    ):
        with pytest.raises(AttributeError):
            method_call()

    before = dict(mapping.items())
    with pytest.raises(ValidationError):
        exec('rpt.provenance |= {"new": "value"}', {"rpt": rpt})
    assert dict(rpt.provenance.items()) == before
    with pytest.raises(TypeError):
        rpt.provenance["nested"]["k"] = "changed"
    assert rpt.provenance["items"] == ("a",)


@pytest.mark.parametrize("direction", ["positive", "negative", "neutral"])
def test_signal_direction_accepts_existing_enum_values(direction: str) -> None:
    item = SignalAssessment(signal_id="A1", direction=direction)
    assert item.direction is not None
    assert item.direction.value == direction


def test_signal_direction_rejects_invalid_and_preserves_none() -> None:
    assert SignalAssessment(signal_id="A1", direction=None).direction is None
    with pytest.raises(ValidationError):
        SignalAssessment(signal_id="A1", direction="sideways")


@pytest.mark.parametrize(
    ("factory", "kwargs"),
    [
        (SignalAssessment, {"signal_id": "A1", "evidence": "not a collection"}),
        (SignalAssessment, {"signal_id": "A1", "risks": b"not a collection"}),
        (SignalAssessment, {"signal_id": "A1", "stale_evidence_warnings": "stale"}),
        (SignalAssessment, {"signal_id": "A1", "point_in_time_warnings": "pit"}),
        (ValuationSummary, {"valuation_compression_evidence": "evidence"}),
        (ValuationSummary, {"missing_valuation_fields": b"field"}),
        (CompanyResearchReport, {**report().model_dump(), "supporting_evidence": "support"}),
        (CompanyResearchReport, {**report().model_dump(), "signal_assessments": "A1"}),
        (CompanyResearchReport, {**report().model_dump(), "unavailable_signals": "A1"}),
    ],
)
def test_bare_strings_are_rejected_for_collection_fields(
    factory: type, kwargs: dict[str, object]
) -> None:
    with pytest.raises(ValidationError, match="collection fields"):
        factory(**kwargs)


def test_ticker_length_convention() -> None:
    assert CompanyIdentity(ticker=" lowercase ", name="Acme").ticker == "LOWERCASE"
    assert CompanyIdentity(ticker="ABCDEFGHIJKL", name="Acme").ticker == "ABCDEFGHIJKL"
    with pytest.raises(ValidationError):
        CompanyIdentity(ticker="ABCDEFGHIJKLM", name="Acme")


def test_unavailable_signal_ids_are_trimmed_and_blank_or_duplicate_rejected() -> None:
    assert report(unavailable_signals=[" A1 "]).unavailable_signals == ("A1",)
    with pytest.raises(ValidationError, match="blank signal IDs"):
        report(unavailable_signals=["   "])
    with pytest.raises(ValidationError, match="duplicate signal IDs"):
        report(unavailable_signals=["A1", " A1 "])


def test_pydantic_json_round_trip_and_schema_with_immutable_mappings() -> None:
    rpt = report(
        provenance={"source": "fixture", "nested": {"key": "value"}, "items": ["a"]},
        signal_assessments=[assessment("A1")],
    )
    payload = rpt.model_dump_json()
    loaded = CompanyResearchReport.model_validate_json(payload)
    assert loaded.model_dump() == rpt.model_dump()
    assert loaded.provenance == rpt.provenance
    with pytest.raises(TypeError):
        loaded.provenance["nested"]["key"] = "changed"
    assert loaded.provenance["items"] == ("a",)
    assert loaded.as_of.tzinfo == UTC
    assert loaded.source_timestamps["src"].tzinfo == UTC
    assert CompanyResearchReport.model_json_schema()
    assert SignalAssessment.model_json_schema()
    loaded_assessment = loaded.signal_assessments[0]
    assert loaded_assessment.metadata is not None
    assert loaded_assessment.metadata["items"] == ("a",)
