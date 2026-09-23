"""Out-of-sample cohort evaluation using dated universes and public snapshots."""

from __future__ import annotations

import csv
import json
from bisect import bisect_left, bisect_right
from datetime import UTC, date, datetime, timedelta
from math import isfinite, sqrt
from pathlib import Path
from typing import Any

from smct_research.scoring.composite import CompositeResearchScorer
from smct_research.screening.io import load_feature_snapshots, load_universe
from smct_research.screening.registry import default_registry
from smct_research.screening.service import BatchEvaluationService
from smct_research.screening.universe import UniversePolicy


def _price_book(path: Path) -> dict[str, tuple[list[date], list[float]]]:
    raw: dict[str, dict[date, float]] = {}
    with path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            ticker, day = row["ticker"].upper().strip(), date.fromisoformat(row["date"])
            price = float(row["adjusted_close"])
            if not isfinite(price) or price <= 0 or day in raw.setdefault(ticker, {}):
                raise ValueError(f"Invalid or duplicate price: {ticker} {day}")
            raw[ticker][day] = price
    return {
        ticker: ([d for d, _ in sorted(series.items())], [p for _, p in sorted(series.items())])
        for ticker, series in raw.items()
    }


def _return(
    book: dict[str, tuple[list[date], list[float]]], ticker: str, start: date, months: int
) -> tuple[float, date, date] | None:
    if ticker not in book:
        return None
    dates, values = book[ticker]
    entry = bisect_right(dates, start)
    if entry == len(dates) or dates[entry] > start + timedelta(days=7):
        return None
    year = start.year + (start.month - 1 + months) // 12
    month = (start.month - 1 + months) % 12 + 1
    day = min(start.day, 28)
    target = date(year, month, day)
    exit_index = bisect_left(dates, target)
    if exit_index == len(dates) or dates[exit_index] > target + timedelta(days=7):
        return None
    return values[exit_index] / values[entry] - 1, dates[entry], dates[exit_index]


def validate_history(
    universes: Path,
    snapshots: Path,
    prices: Path,
    *,
    benchmark: str = "VOO",
    coverage: float = 50,
    round_trip_cost: float = 0.005,
) -> dict[str, Any]:
    """Use only as-of inputs; retain unscorable and censored cohorts in diagnostics.

    Directory names are ISO dates, and each universe must be a *historical* file.
    No weights or thresholds are fitted to the test period.
    """
    book = _price_book(prices)
    records: list[dict[str, Any]] = []
    if not 0 <= round_trip_cost < 1:
        raise ValueError("Round-trip cost must be in [0, 1)")
    for universe_file in sorted(universes.glob("*.json")):
        cohort = date.fromisoformat(universe_file.stem)
        as_of = datetime.combine(cohort, datetime.max.time(), UTC)
        feature_dir = snapshots / cohort.isoformat()
        if not feature_dir.is_dir():
            raise ValueError(f"Missing historical snapshots for {cohort}")
        companies = load_universe(universe_file)
        features = load_feature_snapshots(feature_dir)
        rankings = BatchEvaluationService(default_registry(), CompositeResearchScorer()).evaluate(
            companies, features, UniversePolicy(), as_of
        )
        ready = [
            r
            for r in rankings
            if r.rank
            and r.feature_completeness_percentage >= coverage
            and not r.point_in_time_eligibility_warnings
            and not r.stale_evidence_warnings
            and "source_availability_not_supplied" not in r.signal_diagnostics
        ]
        # The historical universe is required: today’s survivors must never stand in for it.
        for months in (12, 24, 36):
            comparison = _return(book, benchmark, cohort, months)
            selected = ready[:2]
            picked = [_return(book, row.ticker, cohort, months) for row in selected]
            reason = (
                "no_eligible_picks"
                if not selected
                else "incomplete_top_two"
                if len(selected) != 2 or any(x is None for x in picked)
                else "missing_benchmark"
                if comparison is None
                else None
            )
            if comparison is None and reason is None:
                reason = "missing_benchmark"
            if (
                reason is None
                and comparison is not None
                and any(
                    x is not None and (x[1] != comparison[1] or x[2] != comparison[2])
                    for x in picked
                )
            ):
                reason = "misaligned_quotes"
            record: dict[str, Any] = {
                "as_of": cohort.isoformat(),
                "months": months,
                "universe_size": len(companies),
                "eligible_count": len(ready),
                "picks": [r.ticker for r in selected],
                "status": reason or "complete",
            }
            if reason is None:
                assert comparison is not None and all(x is not None for x in picked)
                returns = [x[0] for x in picked if x is not None]
                gross = sum(returns) / len(returns)
                record.update(
                    gross_return=gross,
                    net_return=gross - round_trip_cost,
                    benchmark_return=comparison[0],
                    excess_return=gross - round_trip_cost - comparison[0],
                    entry_dates=[x[1].isoformat() for x in picked if x],
                    exit_dates=[x[2].isoformat() for x in picked if x],
                )
            records.append(record)
    if not records:
        raise ValueError("No historical dated universes found")
    summary: dict[str, object] = {}
    for months in (12, 24, 36):
        complete = [r for r in records if r["months"] == months and r["status"] == "complete"]
        excess = [float(r["excess_return"]) for r in complete]
        mean = sum(excess) / len(excess) if excess else None
        # Overlapping weekly cohorts are dependent. Use non-overlapping entry years
        # for the significance diagnostic, never count them as independent trials.
        independent = []
        last_end = date.min
        for record in complete:
            begin = date.fromisoformat(str(record["entry_dates"][0]))
            end = max(date.fromisoformat(x) for x in record["exit_dates"])
            if begin > last_end:
                independent.append(float(record["excess_return"]))
                last_end = end
        if len(independent) >= 30:
            avg = sum(independent) / len(independent)
            variance = sum((x - avg) ** 2 for x in independent) / (len(independent) - 1)
            t_stat = avg / sqrt(variance / len(independent)) if variance > 0 else None
        else:
            t_stat = None
        summary[str(months)] = {
            "complete_cohorts": len(complete),
            "mean_net_excess_return": mean,
            "independent_cohorts": len(independent),
            "independent_t_stat": t_stat,
            "alpha_established": bool(t_stat is not None and t_stat >= 2 and avg > 0),
        }
    return {
        "benchmark": benchmark,
        "round_trip_cost": round_trip_cost,
        "coverage_threshold": coverage,
        "summary": summary,
        "cohorts": records,
        "caveats": [
            "Adjusted close may exclude cash dividends; use total-return data when available.",
            "Historical universe must include delisted names and contemporaneous market caps.",
            "No hyperparameter selection on the evaluation period is permitted.",
        ],
    }


def write_validation(path: Path, result: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, indent=2, allow_nan=False) + "\n", encoding="utf-8")
