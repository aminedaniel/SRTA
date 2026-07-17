from datetime import date
from pathlib import Path

from smct_research.core.models import FeatureSnapshot
from smct_research.form4 import (
    Form4Transaction,
    deduplicate_form4_transactions,
    form4_rolling_features,
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
        "form4_aggregate_purchase_value_30d": 10_000_000,
        "form4_purchase_value_market_cap_ratio_30d": 0.1,
        "form4_largest_individual_purchase_30d": 5_000_000,
        "form4_officer_director_10pct_participants_30d": 3,
        "form4_repeated_purchase_insiders_30d": 2,
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
