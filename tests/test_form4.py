from datetime import date
from pathlib import Path

from smct_research.core.models import FeatureSnapshot
from smct_research.form4 import (
    Form4Transaction,
    deduplicate_form4_transactions,
    form4_rolling_features,
    parse_form4_filing,
    parse_form4_xml,
)
from smct_research.signals.form4_cluster_buying import Form4ClusterBuyingSignal


def row(
    name: str,
    transaction_date: date,
    filing_date: date,
    *,
    value: float = 50_000,
    amendment: bool = False,
) -> Form4Transaction:
    return Form4Transaction(
        issuer_cik="123",
        ticker="acme",
        insider_name=name,
        reporting_owner_cik=f"owner-{name}",
        report_date=transaction_date,
        insider_role="director",
        is_director=True,
        transaction_date=transaction_date,
        filing_date=filing_date,
        transaction_code="P",
        acquired_disposed="A",
        shares=value / 10,
        price_per_share=10,
        transaction_value=value,
        ownership_nature="D",
        accession_number=f"a-{name}-{filing_date}",
        is_amendment=amendment,
    )


def test_parser_preserves_purchase_and_excludes_exercise_and_grant() -> None:
    records = parse_form4_xml(
        (Path("tests/fixtures/sec/form4_purchase.xml")).read_bytes(),
        filing_date=date(2026, 7, 11),
        accession_number="0001",
    )
    assert len(records) == 1
    assert records[0].ticker == "ACME" and records[0].transaction_value == 25_500
    assert records[0].is_officer and records[0].is_director and records[0].ownership_nature == "D"
    assert records[0].reporting_owner_cik == "789"


def test_amendments_and_point_in_time_deduplication() -> None:
    original = row("Ada", date(2026, 7, 10), date(2026, 7, 11))
    amended = row("Ada", date(2026, 7, 10), date(2026, 7, 12), amendment=True)
    assert len(deduplicate_form4_transactions([original, amended], date(2026, 7, 11))) == 1
    assert deduplicate_form4_transactions([original], date(2026, 7, 10)) == []


def test_features_cluster_boundaries_and_stale_filings() -> None:
    as_of = date(2026, 7, 17)
    rows = [
        row("Ada", date(2026, 7, 10), date(2026, 7, 11)),
        row("Bob", date(2026, 7, 10), date(2026, 7, 11)),
        row("Cam", date(2026, 7, 10), date(2026, 7, 11)),
        row("Dan", date(2026, 6, 17), date(2026, 6, 18)),
    ]
    features = form4_rolling_features(rows, as_of=as_of, market_cap_usd=100_000_000)
    assert features["form4_cluster_buying_7d"] is True
    assert features["form4_unique_insiders_buying_30d"] == 4
    assert features["form4_unique_insiders_buying_7d"] == 3


def test_signal_has_no_score_for_isolated_and_caps_score() -> None:
    values = {
        "form4_unique_insiders_buying_7d": 3,
        "form4_aggregate_purchase_value_7d": 10_000_000,
        "form4_purchase_value_market_cap_ratio_7d": 0.1,
        "form4_largest_individual_purchase_7d": 5_000_000,
        "form4_officer_director_10pct_participants_7d": 3,
        "form4_repeated_purchase_insiders_7d": 2,
        "form4_filing_age_days": 0,
        "form4_cluster_buying_7d": True,
    }
    result = Form4ClusterBuyingSignal().evaluate(FeatureSnapshot(ticker="ACME", values=values))
    assert 0 < result.score <= 12
    values["form4_cluster_buying_7d"] = False
    assert (
        Form4ClusterBuyingSignal().evaluate(FeatureSnapshot(ticker="ACME", values=values)).score
        == 0
    )


def test_amendment_replaces_the_entire_original_filing_and_events_not_rows() -> None:
    original_a = row("Ada", date(2026, 7, 10), date(2026, 7, 11), value=50_000)
    original_b = row("Ada", date(2026, 7, 9), date(2026, 7, 11), value=75_000)
    amended = row("Ada", date(2026, 7, 10), date(2026, 7, 12), value=60_000, amendment=True)
    amended.report_date = original_a.report_date
    original_b.report_date = original_a.report_date
    visible = deduplicate_form4_transactions([original_a, original_b, amended], date(2026, 7, 12))
    assert [transaction.transaction_value for transaction in visible] == [60_000]
    duplicate_ownership_row = amended.model_copy(update={"ownership_nature": "I"})
    features = form4_rolling_features(
        [amended, duplicate_ownership_row], as_of=date(2026, 7, 12), market_cap_usd=1_000_000
    )
    assert features["form4_repeated_purchase_insiders_30d"] == 0


def test_joint_filing_is_excluded_conservatively() -> None:
    payload = Path("tests/fixtures/sec/form4_purchase.xml").read_text()
    extra_owner = (
        "</reportingOwner><reportingOwner><reportingOwnerId>"
        "<rptOwnerCik>2</rptOwnerCik><rptOwnerName>Joint Owner</rptOwnerName>"
        "</reportingOwnerId></reportingOwner><nonDerivativeTable>"
    )
    joint = payload.replace("</reportingOwner><nonDerivativeTable>", extra_owner)
    assert (
        parse_form4_xml(joint.encode(), filing_date=date(2026, 7, 11), accession_number="joint")
        == []
    )


def test_metadata_only_amendment_removes_prior_purchase_signal() -> None:
    original = row("Ada", date(2026, 7, 10), date(2026, 7, 11), value=50_000)
    amendment_xml = (
        Path("tests/fixtures/sec/form4_purchase.xml")
        .read_bytes()
        .replace(b"<transactionCode>P</transactionCode>", b"<transactionCode>A</transactionCode>")
    )
    amendment = parse_form4_filing(
        amendment_xml, filing_date=date(2026, 7, 12), accession_number="amended", is_amendment=True
    )
    original.report_date = amendment.report_date
    original.reporting_owner_cik = amendment.reporting_owner_ciks[0]
    original.issuer_cik = amendment.issuer_cik
    assert amendment.transactions == []
    assert deduplicate_form4_transactions([original], date(2026, 7, 12), [amendment]) == []


def test_identity_and_score_inputs_are_limited_to_triggered_cluster() -> None:
    as_of = date(2026, 7, 17)
    recent = [row("Same name", date(2026, 7, 10), date(2026, 7, 11)) for _ in range(3)]
    recent[0].reporting_owner_cik, recent[1].reporting_owner_cik, recent[2].reporting_owner_cik = (
        "1",
        "2",
        "3",
    )
    older = row("Older", date(2026, 6, 20), date(2026, 6, 21), value=9_000_000)
    features = form4_rolling_features(recent + [older], as_of=as_of, market_cap_usd=100_000_000)
    assert features["form4_unique_insiders_buying_7d"] == 3
    assert features["form4_aggregate_purchase_value_7d"] == 150_000
    assert features["form4_largest_individual_purchase_7d"] == 50_000


def test_latest_nonqualifying_amendment_removes_prior_qualifying_amendment() -> None:
    payload = Path("tests/fixtures/sec/form4_purchase.xml").read_bytes()
    original = parse_form4_filing(payload, filing_date=date(2026, 7, 11), accession_number="001")
    qualifying_amendment = parse_form4_filing(
        payload, filing_date=date(2026, 7, 12), accession_number="002", is_amendment=True
    )
    nonqualifying_amendment = parse_form4_filing(
        payload.replace(
            b"<transactionCode>P</transactionCode>", b"<transactionCode>A</transactionCode>"
        ),
        filing_date=date(2026, 7, 13),
        accession_number="003",
        is_amendment=True,
    )
    assert qualifying_amendment.transactions and not nonqualifying_amendment.transactions
    assert (
        deduplicate_form4_transactions(
            original.transactions + qualifying_amendment.transactions,
            date(2026, 7, 13),
            [original, qualifying_amendment, nonqualifying_amendment],
        )
        == []
    )
