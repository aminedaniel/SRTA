from datetime import date

from smct_research.core.models import (
    CongressionalChamber,
    CongressionalOwner,
    CongressionalTransaction,
    CongressionalTransactionType,
)


def test_congressional_transaction_midpoint_and_lag() -> None:
    transaction = CongressionalTransaction(
        filer_name="Example Filer",
        chamber=CongressionalChamber.HOUSE,
        owner=CongressionalOwner.SPOUSE,
        ticker="test",
        transaction_type=CongressionalTransactionType.PURCHASE,
        transaction_date=date(2026, 1, 1),
        disclosure_date=date(2026, 1, 31),
        amount_low_usd=15_001,
        amount_high_usd=50_000,
        source_url="https://example.test/disclosure",
    )

    assert transaction.ticker == "TEST"
    assert transaction.estimated_amount_usd == 32_500.5
    assert transaction.disclosure_lag_days == 30
