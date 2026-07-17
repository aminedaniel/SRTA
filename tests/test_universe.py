from smct_research.core.models import Company
from smct_research.screening.universe import UniversePolicy


def test_includes_liquid_small_cap_software_company() -> None:
    company = Company(
        ticker="TEST",
        name="Test Software",
        market_cap_usd=2_000_000_000,
        sector="Technology",
        industry="Application Software",
        exchange="NASDAQ",
        country="US",
        security_type="common_equity",
        average_daily_dollar_volume=5_000_000,
    )
    assert UniversePolicy().includes(company)


def test_excludes_large_cap() -> None:
    company = Company(
        ticker="MEGA",
        name="Mega Tech",
        market_cap_usd=200_000_000_000,
        sector="Technology",
        exchange="NASDAQ",
        country="US",
        security_type="common_equity",
        average_daily_dollar_volume=50_000_000,
    )
    assert not UniversePolicy().includes(company)
