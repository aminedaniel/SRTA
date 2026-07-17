from __future__ import annotations

from pydantic import BaseModel, Field

from smct_research.core.models import Company


class UniversePolicy(BaseModel):
    minimum_market_cap_usd: float = Field(default=300_000_000, gt=0)
    maximum_market_cap_usd: float = Field(default=20_000_000_000, gt=0)
    minimum_daily_dollar_volume: float = Field(default=2_000_000, ge=0)
    countries: set[str] = Field(default_factory=lambda: {"US"})
    technology_keywords: set[str] = Field(
        default_factory=lambda: {
            "technology",
            "software",
            "semiconductor",
            "cybersecurity",
            "fintech",
            "cloud",
            "data",
            "internet",
            "electronic",
        }
    )
    allowed_exchanges: set[str] = Field(default_factory=lambda: {"NASDAQ", "NYSE", "NYSEAMERICAN"})
    excluded_security_types: set[str] = Field(
        default_factory=lambda: {"etf", "fund", "warrant", "preferred", "otc"}
    )

    def exclusion_reasons(self, company: Company) -> list[str]:
        """Return every reason a company cannot enter the screened universe."""
        reasons: list[str] = []
        if not company.is_active:
            reasons.append("inactive_security")
        if company.country.upper() not in self.countries:
            reasons.append("not_us_listed")
        if not company.exchange or company.exchange.upper() not in self.allowed_exchanges:
            reasons.append("unsupported_or_otc_exchange")
        if company.security_type.lower() in self.excluded_security_types:
            reasons.append(f"excluded_security_type:{company.security_type.lower()}")
        if company.market_cap_usd < self.minimum_market_cap_usd:
            reasons.append("market_cap_below_minimum")
        if company.market_cap_usd > self.maximum_market_cap_usd:
            reasons.append("market_cap_above_maximum")
        if company.average_daily_dollar_volume is None:
            reasons.append("missing_average_daily_dollar_volume")
        elif company.average_daily_dollar_volume < self.minimum_daily_dollar_volume:
            reasons.append("average_daily_dollar_volume_below_minimum")
        text = f"{company.sector} {company.industry or ''}".lower()
        if not any(keyword in text for keyword in self.technology_keywords):
            reasons.append("outside_technology_focus")
        return reasons

    def includes(self, company: Company) -> bool:
        return not self.exclusion_reasons(company)
