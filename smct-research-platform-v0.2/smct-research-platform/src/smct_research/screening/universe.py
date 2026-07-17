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

    def includes(self, company: Company) -> bool:
        if not company.is_active or company.country not in self.countries:
            return False
        if not self.minimum_market_cap_usd <= company.market_cap_usd <= self.maximum_market_cap_usd:
            return False
        if (
            company.average_daily_dollar_volume is not None
            and company.average_daily_dollar_volume < self.minimum_daily_dollar_volume
        ):
            return False
        text = f"{company.sector} {company.industry or ''}".lower()
        return any(keyword in text for keyword in self.technology_keywords)
