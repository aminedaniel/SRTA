from datetime import UTC, datetime, timedelta

import pytest

from smct_research.valuation.reverse_dcf import (
    DCFScenario,
    ReverseDCFInputs,
    sensitivity,
    solve_reverse_dcf,
    value_dcf,
)

NOW = datetime(2026, 1, 1, tzinfo=UTC)


def inputs(**overrides: object) -> ReverseDCFInputs:
    required = {
        "current_share_price",
        "diluted_shares_outstanding",
        "cash_and_equivalents",
        "total_debt",
        "current_annual_revenue",
        "current_free_cash_flow",
        "current_fcf_margin",
    }
    data: dict[str, object] = {
        "ticker": "test",
        "valuation_date": NOW,
        "current_share_price": 10,
        "diluted_shares_outstanding": 10,
        "cash_and_equivalents": 20,
        "total_debt": 10,
        "current_annual_revenue": 100,
        "current_free_cash_flow": 10,
        "current_fcf_margin": 0.1,
        "available_at": {name: NOW for name in required},
        "provenance": {name: "test" for name in required},
    }
    data.update(overrides)
    for name in overrides:
        if name not in {"ticker", "valuation_date", "provenance", "available_at"}:
            data["available_at"][name] = NOW  # type: ignore[index]
            data["provenance"][name] = "test"  # type: ignore[index]
    return ReverseDCFInputs.model_validate(data)


def scenario(**overrides: object) -> DCFScenario:
    data: dict[str, object] = {
        "name": "base",
        "initial_revenue_growth": 0.1,
        "terminal_revenue_growth": 0.1,
        "terminal_fcf_margin": 0.1,
        "discount_rate": 0.1,
        "terminal_growth_rate": 0.03,
        "explicit_forecast_years": 5,
    }
    data.update(overrides)
    return DCFScenario.model_validate(data)


def test_value_and_equity_bridge_and_dilution() -> None:
    result = value_dcf(inputs(), scenario())
    assert result.valid and result.equity_value == pytest.approx(result.enterprise_value + 10)
    assert result.projected_years[-1].diluted_shares == pytest.approx(10)
    diluted = value_dcf(inputs(expected_annual_dilution=0.1), scenario())
    assert diluted.projected_years[-1].diluted_shares == pytest.approx(10 * 1.1**5)
    assert diluted.diluted_value_per_share < result.diluted_value_per_share


def test_negative_fcf_transition_and_terminal_rules() -> None:
    result = value_dcf(
        inputs(current_free_cash_flow=-10, current_fcf_margin=-0.1),
        scenario(terminal_fcf_margin=0.2),
    )
    assert (
        result.valid
        and result.projected_years[0].free_cash_flow < 0 < result.projected_years[-1].free_cash_flow
    )
    with pytest.raises(ValueError, match="terminal_fcf_margin"):
        scenario(terminal_fcf_margin=0)


def test_monotonicity_and_sensitivity_is_deterministic() -> None:
    base = value_dcf(inputs(), scenario())
    assert (
        value_dcf(inputs(), scenario(discount_rate=0.12)).diluted_value_per_share
        < base.diluted_value_per_share
    )
    assert (
        value_dcf(inputs(), scenario(initial_revenue_growth=0.2)).diluted_value_per_share
        > base.diluted_value_per_share
    )
    one = sensitivity(inputs(), scenario(), [0.1], [0.03], [0.1], [0.1])
    assert one == sensitivity(inputs(), scenario(), [0.1], [0.03], [0.1], [0.1])


def test_reverse_solver_and_explicit_paths_diagnostic() -> None:
    reference = value_dcf(inputs(), scenario())
    solved = solve_reverse_dcf(
        inputs(current_enterprise_value=reference.enterprise_value), scenario(), tolerance=0.01
    )
    assert solved.converged_growth and solved.converged_margin
    rejected = solve_reverse_dcf(inputs(), scenario(revenue_growth_path=[0.1] * 5))
    assert "explicit projection paths" in rejected.diagnostics[0]


def test_input_dates_and_zero_history() -> None:
    assert (
        solve_reverse_dcf(inputs(historical_revenue_growth_median=0), scenario()).growth_gap
        is not None
    )
    with pytest.raises(ValueError, match="unavailable"):
        inputs(available_at={name: NOW + timedelta(days=1) for name in inputs().available_at})
    with pytest.raises(ValueError, match="missing availability"):
        inputs(available_at={})
