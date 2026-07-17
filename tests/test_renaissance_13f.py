from datetime import date
from pathlib import Path

from smct_research.core.models import FeatureSnapshot
from smct_research.institutional_13f import (
    AmendmentType,
    Form13FHolding,
    derive_position_activity,
    disclosure_lag_decay,
    latest_public_filings,
    parse_amendment_type,
    parse_information_table,
)
from smct_research.providers.sec_edgar import Renaissance13FEdgarProvider
from smct_research.signals.renaissance_public_equity import RenaissancePublicEquityActivitySignal

FIXTURES = Path(__file__).parent / "fixtures/sec"


def holding(
    quarter: date,
    filed: date,
    ticker: str,
    shares: int,
    value: int,
    amendment_type: AmendmentType = AmendmentType.NONE,
) -> Form13FHolding:
    return Form13FHolding(
        quarter, filed, f"x-{filed}", amendment_type, ticker + " Inc", ticker, ticker, shares, value
    )


def test_parser_aggregates_reportable_equity_rows_and_maps_ticker() -> None:
    rows = parse_information_table(
        (FIXTURES / "renaissance_information_table.xml").read_bytes(),
        reporting_quarter=date(2024, 3, 31),
        filing_date=date(2024, 5, 15),
        accession_number="x",
        ticker_for_cusip=lambda c: {"123456789": "EX"}.get(c),
    )
    assert len(rows) == 1
    assert rows[0].ticker == "EX"
    assert rows[0].shares == 1_500 and rows[0].reported_value_usd == 150_000


def test_amendment_types_apply_restated_and_new_holding_reports_correctly() -> None:
    original = holding(date(2024, 3, 31), date(2024, 5, 15), "EX", 100, 10_000)
    supplement = holding(
        date(2024, 3, 31), date(2024, 6, 1), "NEW", 150, 15_000, AmendmentType.NEW_HOLDINGS
    )
    assert {
        row.ticker for row in latest_public_filings([original, supplement], date(2024, 6, 1))
    } == {"EX", "NEW"}
    restated = holding(
        date(2024, 3, 31), date(2024, 6, 2), "NEW", 200, 20_000, AmendmentType.RESTATEMENT
    )
    assert [
        row.ticker
        for row in latest_public_filings([original, supplement, restated], date(2024, 6, 2))
    ] == ["NEW"]
    assert (
        parse_amendment_type("13F-HR/A", (FIXTURES / "renaissance_cover.xml").read_bytes())
        == AmendmentType.NEW_HOLDINGS
    )


def test_new_increase_unchanged_exit_stale_and_lookahead_prevention() -> None:
    q1 = holding(date(2023, 12, 31), date(2024, 2, 14), "EX", 100, 10_000)
    q2 = holding(date(2024, 3, 31), date(2024, 5, 15), "EX", 160, 20_000)
    q2_other = holding(date(2024, 3, 31), date(2024, 5, 15), "OT", 100, 5_000)
    assert derive_position_activity([q1, q2], ticker="EX", as_of=date(2024, 5, 1)).status == "new"
    increased = derive_position_activity([q1, q2, q2_other], ticker="EX", as_of=date(2024, 5, 15))
    assert increased.status == "increased" and increased.share_change_percent == 60
    unchanged = derive_position_activity(
        [q1, holding(date(2024, 3, 31), date(2024, 5, 15), "EX", 100, 10_000)],
        ticker="EX",
        as_of=date(2024, 5, 15),
    )
    assert unchanged.status == "unchanged"
    exited = derive_position_activity([q1, q2_other], ticker="EX", as_of=date(2024, 5, 15))
    assert exited.status == "exited" and exited.position_size_percentile == 0
    assert all(value is not None for value in exited.feature_values().values())
    stale = derive_position_activity([q1], ticker="EX", as_of=date(2025, 2, 14))
    assert disclosure_lag_decay(stale) < 0.1


def test_provider_ingests_edgar_information_tables_end_to_end(tmp_path: Path) -> None:
    submissions = (FIXTURES / "renaissance_submissions.json").read_bytes()
    index = (FIXTURES / "renaissance_index.json").read_bytes()
    table = (FIXTURES / "renaissance_information_table.xml").read_bytes()
    cover = (FIXTURES / "renaissance_cover.xml").read_bytes()

    def transport(url: str, _headers: dict[str, str]) -> bytes:
        if url.endswith("submissions/CIK0001037389.json"):
            return submissions
        if url.endswith("index.json"):
            return index
        if url.endswith("infotable.xml"):
            return table
        return cover

    provider = Renaissance13FEdgarProvider("SMCT test@example.com", tmp_path, transport=transport)
    rows = provider.renaissance_13f_holdings(lambda cusip: "EX" if cusip == "123456789" else None)
    assert len(rows) == 2
    assert rows[0].amendment_type is AmendmentType.NONE
    assert rows[1].amendment_type is AmendmentType.NEW_HOLDINGS
    assert all(row.ticker == "EX" and row.shares == 1_500 for row in rows)


def test_signal_can_evaluate_safe_new_and_exit_features() -> None:
    for status, change in (("new", 0), ("exited", -100)):
        snapshot = FeatureSnapshot(
            ticker="EX",
            values={
                "renaissance_13f_status": status,
                "renaissance_13f_share_change_percent": change,
                "renaissance_13f_consecutive_quarters_held": 0,
                "renaissance_13f_position_size_percentile": 0,
                "renaissance_13f_disclosure_age_days": 0,
                "renaissance_13f_lag_decay": 1,
            },
        )
        result = RenaissancePublicEquityActivitySignal().evaluate(snapshot)
        assert abs(result.score) <= 5
