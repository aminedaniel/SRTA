from datetime import date
from pathlib import Path

from smct_research.core.models import FeatureSnapshot
from smct_research.institutional_13f import (
    Form13FHolding,
    derive_position_activity,
    disclosure_lag_decay,
    latest_public_filings,
    parse_information_table,
)
from smct_research.signals.renaissance_public_equity import RenaissancePublicEquityActivitySignal

FIXTURE = Path(__file__).parent / "fixtures/sec/renaissance_information_table.xml"


def holding(
    quarter: date, filed: date, ticker: str, shares: int, value: int, amendment: bool = False
) -> Form13FHolding:
    return Form13FHolding(
        quarter, filed, f"x-{filed}", amendment, ticker + " Inc", ticker, ticker, shares, value
    )


def test_parser_preserves_13f_evidence_and_ticker_mapping() -> None:
    rows = parse_information_table(
        FIXTURE.read_bytes(),
        reporting_quarter=date(2024, 3, 31),
        filing_date=date(2024, 5, 15),
        accession_number="x",
        is_amendment=False,
        ticker_for_cusip=lambda c: {"123456789": "EX"}.get(c),
    )
    assert rows[0].ticker == "EX"
    assert rows[0].reported_value_usd == 100_000
    assert rows[0].reporting_quarter == date(2024, 3, 31)


def test_amendment_is_not_visible_before_its_filing_date() -> None:
    original = holding(date(2024, 3, 31), date(2024, 5, 15), "EX", 100, 10_000)
    amended = holding(date(2024, 3, 31), date(2024, 6, 1), "EX", 150, 15_000, True)
    assert latest_public_filings([original, amended], date(2024, 5, 31))[0].shares == 100
    assert latest_public_filings([original, amended], date(2024, 6, 1))[0].shares == 150


def test_new_increase_exit_stale_and_lookahead_prevention() -> None:
    q1 = holding(date(2023, 12, 31), date(2024, 2, 14), "EX", 100, 10_000)
    q2 = holding(date(2024, 3, 31), date(2024, 5, 15), "EX", 160, 20_000)
    q2_other = holding(date(2024, 3, 31), date(2024, 5, 15), "OT", 100, 5_000)
    # Q2 cannot influence testing prior to its filing date.
    assert derive_position_activity([q1, q2], ticker="EX", as_of=date(2024, 5, 1)).status == "new"
    increased = derive_position_activity([q1, q2, q2_other], ticker="EX", as_of=date(2024, 5, 15))
    assert increased.status == "increased" and increased.share_change_percent == 60
    assert increased.consecutive_quarters_held == 2 and increased.position_size_percentile == 100
    exited = derive_position_activity([q1, q2_other], ticker="EX", as_of=date(2024, 5, 15))
    assert exited.status == "exited"
    stale = derive_position_activity([q1], ticker="EX", as_of=date(2025, 2, 14))
    assert disclosure_lag_decay(stale) < 0.1


def test_signal_is_capped_at_weak_effect() -> None:
    snapshot = FeatureSnapshot(
        ticker="EX",
        values={
            "renaissance_13f_status": "new",
            "renaissance_13f_share_change_percent": 100,
            "renaissance_13f_consecutive_quarters_held": 8,
            "renaissance_13f_position_size_percentile": 100,
            "renaissance_13f_disclosure_age_days": 0,
            "renaissance_13f_lag_decay": 1,
        },
    )
    result = RenaissancePublicEquityActivitySignal().evaluate(snapshot)
    assert 0 < result.score <= 5
    assert "cannot identify Medallion" in result.risks[0]
