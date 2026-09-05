"""Compare complete saved runs without treating absence as deterioration."""

from __future__ import annotations

from typing import Any

from smct_research.research.models import CompanyResearchReport
from smct_research.research.render import cell, number


def compare_runs(
    before: dict[str, Any], after: dict[str, Any], tickers: set[str] | None = None
) -> dict[str, Any]:
    if before["as_of"] >= after["as_of"]:
        raise ValueError("Change reports require two runs with strictly increasing timestamps")
    old = {r.company.ticker: r for r in before["reports"]}
    new = {r.company.ticker: r for r in after["reports"]}
    rows: list[dict[str, Any]] = []
    for ticker in sorted(
        (set(old) | set(new)) & (tickers if tickers is not None else set(old) | set(new))
    ):
        prior, current = old.get(ticker), new.get(ticker)
        row: dict[str, Any] = {
            "ticker": ticker,
            "state": "added" if prior is None else "removed" if current is None else "continued",
        }
        row.update(
            before_score=prior.composite_score if prior else None,
            after_score=current.composite_score if current else None,
            before_coverage=prior.feature_completeness_percentage if prior else None,
            after_coverage=current.feature_completeness_percentage if current else None,
        )
        comparable = bool(
            prior
            and current
            and _basis(prior) == _basis(current)
            and before["policy"] == after["policy"]
        )
        row["comparable"] = comparable
        row["score_change"] = (
            current.composite_score - prior.composite_score
            if comparable
            and prior
            and current
            and prior.composite_score is not None
            and current.composite_score is not None
            else None
        )
        if prior and current:
            previous = {s.signal_id: s for s in prior.signal_assessments}
            row["signal_changes"] = [
                s.signal_id
                for s in current.signal_assessments
                if s.model_dump(exclude={"evaluated_at"})
                != (
                    previous[s.signal_id].model_dump(exclude={"evaluated_at"})
                    if s.signal_id in previous
                    else None
                )
            ]
            row["new_warnings"] = sorted(
                set((*current.stale_evidence_warnings, *current.point_in_time_warnings))
                - set((*prior.stale_evidence_warnings, *prior.point_in_time_warnings))
            )
        rows.append(row)
    return {
        "before_run": before["id"],
        "after_run": after["id"],
        "before_as_of": before["as_of"],
        "after_as_of": after["as_of"],
        "companies": rows,
    }


def _basis(report: CompanyResearchReport) -> tuple[object, ...]:
    return (
        report.universe_eligible,
        report.unavailable_signals,
        report.provenance.get("model_version"),
        report.provenance.get("signal_weights"),
    )


def render_changes(changes: dict[str, Any]) -> str:
    lines = [
        "# Watchlist change report",
        "",
        f"{changes['before_as_of']} → {changes['after_as_of']}",
        "",
        "Scores are compared only when signal availability, scoring model and universe policy match. "
        "A company absent from a run is marked removed, not downgraded.",
        "",
        "| Ticker | State | Before | After | Change | Coverage before → after | Signals changed |",
        "| --- | --- | ---: | ---: | ---: | --- | --- |",
    ]
    for row in changes["companies"]:
        lines.append(
            f"| {cell(row['ticker'])} | {row['state']} | {number(row['before_score'])} | "
            f"{number(row['after_score'])} | {number(row['score_change'])} | "
            f"{number(row['before_coverage'])} → {number(row['after_coverage'])} | "
            f"{cell(', '.join(row.get('signal_changes', [])))} |"
        )
    return "\n".join(lines) + "\n"
