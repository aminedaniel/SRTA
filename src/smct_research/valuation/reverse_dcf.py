"""Deterministic, point-in-time reverse DCF calculations; no network access."""

from __future__ import annotations

import math
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from smct_research.core.models import normalize_utc


class ReverseDCFInputs(BaseModel):
    ticker: str
    valuation_date: datetime
    current_share_price: float = Field(gt=0)
    diluted_shares_outstanding: float
    cash_and_equivalents: float
    total_debt: float
    current_annual_revenue: float
    current_free_cash_flow: float
    current_fcf_margin: float
    stock_based_compensation: float | None = None
    current_revenue_growth: float | None = None
    historical_revenue_growth_median: float | None = None
    historical_fcf_margin_median: float | None = None
    expected_annual_dilution: float = Field(default=0, ge=0)
    tax_rate: float | None = Field(default=None, ge=0, le=1)
    market_capitalization: float | None = Field(default=None, ge=0)
    current_enterprise_value: float | None = Field(default=None, ge=0)
    provenance: dict[str, str] = Field(default_factory=dict)
    available_at: dict[str, datetime] = Field(default_factory=dict)

    @model_validator(mode="after")
    def valid(self) -> ReverseDCFInputs:
        self.ticker = self.ticker.upper().strip()
        self.valuation_date = normalize_utc(self.valuation_date)
        self.available_at = {k: normalize_utc(v) for k, v in self.available_at.items()}
        if self.diluted_shares_outstanding <= 0:
            raise ValueError("diluted_shares_outstanding must be positive")
        if self.current_annual_revenue <= 0:
            raise ValueError("current_annual_revenue must be positive")
        if self.cash_and_equivalents < 0 or self.total_debt < 0:
            raise ValueError("cash_and_equivalents and total_debt must be nonnegative")
        required_availability = {
            "current_share_price",
            "diluted_shares_outstanding",
            "cash_and_equivalents",
            "total_debt",
            "current_annual_revenue",
            "current_free_cash_flow",
            "current_fcf_margin",
        }
        missing_availability = required_availability - self.available_at.keys()
        if missing_availability:
            raise ValueError(
                "missing availability timestamps: " + ", ".join(sorted(missing_availability))
            )
        future = [k for k, v in self.available_at.items() if v > self.valuation_date]
        if future:
            raise ValueError(f"inputs unavailable at valuation date: {', '.join(sorted(future))}")
        return self

    @property
    def enterprise_value(self) -> float:
        return (
            self.current_enterprise_value
            if self.current_enterprise_value is not None
            else (
                (
                    self.market_capitalization
                    if self.market_capitalization is not None
                    else self.current_share_price * self.diluted_shares_outstanding
                )
                + self.total_debt
                - self.cash_and_equivalents
            )
        )


class DCFScenario(BaseModel):
    name: Literal["conservative", "base", "optimistic"] = "base"
    initial_revenue_growth: float
    terminal_revenue_growth: float
    initial_fcf_margin: float | None = None
    terminal_fcf_margin: float
    discount_rate: float
    terminal_growth_rate: float
    annual_dilution: float | None = Field(default=None, ge=0)
    explicit_forecast_years: int = Field(default=7, ge=5, le=10)
    revenue_growth_path: list[float] | None = None
    fcf_margin_path: list[float] | None = None


class ProjectedYear(BaseModel):
    year: int
    revenue_growth: float
    fcf_margin: float
    diluted_shares: float
    revenue: float
    free_cash_flow: float
    present_value_fcf: float


class DCFValuationResult(BaseModel):
    scenario: str
    projected_years: list[ProjectedYear] = Field(default_factory=list)
    present_value_explicit_fcf: float = 0
    terminal_value: float = 0
    present_value_terminal_value: float = 0
    enterprise_value: float = 0
    equity_value: float = 0
    diluted_value_per_share: float = 0
    upside_downside_percent: float = 0
    terminal_value_share: float = 0
    diagnostics: list[str] = Field(default_factory=list)
    valid: bool = True


class ReverseDCFResult(BaseModel):
    implied_revenue_cagr: float | None = None
    implied_terminal_fcf_margin: float | None = None
    growth_gap: float | None = None
    margin_gap: float | None = None
    diagnostics: list[str] = Field(default_factory=list)
    converged_growth: bool = False
    converged_margin: bool = False


class DCFSensitivityResult(BaseModel):
    discount_rate: float
    terminal_growth_rate: float
    terminal_fcf_margin: float
    revenue_cagr: float
    value_per_share: float | None = None
    diagnostics: list[str] = Field(default_factory=list)


def _path(initial: float, terminal: float, years: int, supplied: list[float] | None) -> list[float]:
    if supplied is not None:
        if len(supplied) != years:
            raise ValueError("explicit projection path length must equal forecast years")
        return supplied
    return [initial + (terminal - initial) * year / years for year in range(1, years + 1)]


def value_dcf(inputs: ReverseDCFInputs, scenario: DCFScenario) -> DCFValuationResult:
    """Value a scenario using end-of-year FCF and a Gordon-growth terminal value."""
    try:
        if scenario.discount_rate <= scenario.terminal_growth_rate:
            raise ValueError("discount_rate must exceed terminal_growth_rate")
        initial_margin = scenario.initial_fcf_margin
        if initial_margin is None:
            initial_margin = inputs.current_fcf_margin
        growths = _path(
            scenario.initial_revenue_growth,
            scenario.terminal_revenue_growth,
            scenario.explicit_forecast_years,
            scenario.revenue_growth_path,
        )
        margins = _path(
            initial_margin,
            scenario.terminal_fcf_margin,
            scenario.explicit_forecast_years,
            scenario.fcf_margin_path,
        )
        revenue = inputs.current_annual_revenue
        shares = inputs.diluted_shares_outstanding
        projected: list[ProjectedYear] = []
        for year, (growth, margin) in enumerate(zip(growths, margins, strict=True), 1):
            revenue *= 1 + growth
            shares *= 1 + (
                scenario.annual_dilution
                if scenario.annual_dilution is not None
                else inputs.expected_annual_dilution
            )
            fcf = revenue * margin
            projected.append(
                ProjectedYear(
                    year=year,
                    revenue_growth=growth,
                    fcf_margin=margin,
                    diluted_shares=shares,
                    revenue=revenue,
                    free_cash_flow=fcf,
                    present_value_fcf=fcf / (1 + scenario.discount_rate) ** year,
                )
            )
        pv_explicit = sum(item.present_value_fcf for item in projected)
        terminal = (
            projected[-1].free_cash_flow
            * (1 + scenario.terminal_growth_rate)
            / (scenario.discount_rate - scenario.terminal_growth_rate)
        )
        if projected[-1].free_cash_flow <= 0 or not math.isfinite(projected[-1].free_cash_flow):
            raise ValueError("terminal free cash flow must be positive and finite")
        if not math.isfinite(terminal):
            raise ValueError("terminal value is not finite")
        pv_terminal = terminal / (1 + scenario.discount_rate) ** scenario.explicit_forecast_years
        ev = pv_explicit + pv_terminal
        equity = ev + inputs.cash_and_equivalents - inputs.total_debt
        per_share = equity / projected[-1].diluted_shares
        return DCFValuationResult(
            scenario=scenario.name,
            projected_years=projected,
            present_value_explicit_fcf=pv_explicit,
            terminal_value=terminal,
            present_value_terminal_value=pv_terminal,
            enterprise_value=ev,
            equity_value=equity,
            diluted_value_per_share=per_share,
            upside_downside_percent=per_share / inputs.current_share_price - 1,
            terminal_value_share=pv_terminal / ev if ev else 0,
        )
    except (ArithmeticError, ValueError) as error:
        return DCFValuationResult(scenario=scenario.name, valid=False, diagnostics=[str(error)])


def _solve(
    inputs: ReverseDCFInputs,
    scenario: DCFScenario,
    target: float,
    lower: float,
    upper: float,
    tolerance: float,
    max_iterations: int,
    kind: str,
) -> tuple[float | None, str | None]:
    def residual(x: float) -> float:
        data = scenario.model_copy(deep=True)
        if kind == "growth":
            data.initial_revenue_growth = x
            data.terminal_revenue_growth = x
        else:
            data.terminal_fcf_margin = x
        result = value_dcf(inputs, data)
        return (result.enterprise_value - target) if result.valid else math.nan

    low, high = residual(lower), residual(upper)
    if not math.isfinite(low) or not math.isfinite(high) or low * high > 0:
        return None, f"target not bracketed by {kind} bounds"
    for _ in range(max_iterations):
        mid = (lower + upper) / 2
        value = residual(mid)
        if not math.isfinite(value):
            return None, f"invalid {kind} valuation during solve"
        if abs(value) <= tolerance:
            return mid, None
        if low * value <= 0:
            upper = mid
            high = value
        else:
            lower = mid
            low = value
    return None, f"{kind} solver did not converge within {max_iterations} iterations"


def solve_reverse_dcf(
    inputs: ReverseDCFInputs,
    scenario: DCFScenario,
    *,
    growth_bounds: tuple[float, float] = (-0.30, 0.60),
    margin_bounds: tuple[float, float] = (0.001, 0.60),
    tolerance: float = 1.0,
    max_iterations: int = 100,
) -> ReverseDCFResult:
    if tolerance <= 0 or max_iterations <= 0:
        return ReverseDCFResult(diagnostics=["tolerance and max_iterations must be positive"])
    if scenario.revenue_growth_path is not None or scenario.fcf_margin_path is not None:
        return ReverseDCFResult(
            diagnostics=["reverse solving is incompatible with explicit projection paths"]
        )
    target = inputs.enterprise_value
    growth, growth_error = _solve(
        inputs, scenario, target, *growth_bounds, tolerance, max_iterations, "growth"
    )
    margin, margin_error = _solve(
        inputs, scenario, target, *margin_bounds, tolerance, max_iterations, "margin"
    )
    grounded_growth = (
        inputs.historical_revenue_growth_median
        if inputs.historical_revenue_growth_median is not None
        else inputs.current_revenue_growth
    )
    grounded_margin = (
        inputs.historical_fcf_margin_median
        if inputs.historical_fcf_margin_median is not None
        else inputs.current_fcf_margin
    )
    return ReverseDCFResult(
        implied_revenue_cagr=growth,
        implied_terminal_fcf_margin=margin,
        growth_gap=growth - grounded_growth
        if growth is not None and grounded_growth is not None
        else None,
        margin_gap=margin - grounded_margin if margin is not None else None,
        converged_growth=growth is not None,
        converged_margin=margin is not None,
        diagnostics=[error for error in (growth_error, margin_error) if error],
    )


def sensitivity(
    inputs: ReverseDCFInputs,
    scenario: DCFScenario,
    discount_rates: list[float],
    terminal_growth_rates: list[float],
    terminal_margins: list[float],
    revenue_cagrs: list[float],
) -> list[DCFSensitivityResult]:
    results = []
    for rate in discount_rates:
        for growth in terminal_growth_rates:
            for margin in terminal_margins:
                for cagr in revenue_cagrs:
                    tested = scenario.model_copy(
                        update={
                            "discount_rate": rate,
                            "terminal_growth_rate": growth,
                            "terminal_fcf_margin": margin,
                            "initial_revenue_growth": cagr,
                            "terminal_revenue_growth": cagr,
                        }
                    )
                    value = value_dcf(inputs, tested)
                    results.append(
                        DCFSensitivityResult(
                            discount_rate=rate,
                            terminal_growth_rate=growth,
                            terminal_fcf_margin=margin,
                            revenue_cagr=cagr,
                            value_per_share=value.diluted_value_per_share if value.valid else None,
                            diagnostics=value.diagnostics,
                        )
                    )
    return results
