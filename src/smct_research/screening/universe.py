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
    eligible_security_types: set[str] = Field(default_factory=lambda: {"common_equity"})
    security_type_aliases: dict[str, str] = Field(
        default_factory=lambda: {
            "common": "common_equity",
            "common stock": "common_equity",
            "common_stock": "common_equity",
            "ordinary share": "common_equity",
            "ordinary_shares": "common_equity",
        }
    )

    def exclusion_reasons(self, company: Company) -> list[str]:
        """Return every reason a company cannot enter the screened universe."""
        reasons: list[str] = []
        if company.is_active is None:
            reasons.append("unknown_active_status")
        elif not company.is_active:
            reasons.append("inactive_security")
        if not company.country:
            reasons.append("unknown_country")
        elif company.country.upper() not in self.countries:
            reasons.append("not_us_listed")
        if not company.exchange or company.exchange.upper() not in self.allowed_exchanges:
            reasons.append(
                "unknown_exchange" if not company.exchange else "unsupported_or_otc_exchange"
            )
        if not company.security_type:
            reasons.append("unknown_security_type")
        elif (
            self._normalized_security_type(company.security_type)
            not in self.eligible_security_types
        ):
            reasons.append(
                f"unsupported_security_type:{self._normalized_security_type(company.security_type)}"
            )
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

    def _normalized_security_type(self, value: str) -> str:
        normalized = " ".join(value.lower().replace("_", " ").replace("-", " ").split())
        return self.security_type_aliases.get(normalized, normalized.replace(" ", "_"))
