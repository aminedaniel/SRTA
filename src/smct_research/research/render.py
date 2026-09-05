"""Portable Markdown research artifacts. No generated investment assertions."""

from __future__ import annotations

from smct_research.research.builder import report_id
from smct_research.research.models import CompanyResearchReport, SignalAvailability


def cell(value: object) -> str:
    return str(value).replace("|", "\\|").replace("\r", " ").replace("\n", " ")


def number(value: float | None, *, percent: bool = False) -> str:
    if value is None:
        return "Unavailable"
    return f"{value:.1%}" if percent else f"{value:.2f}"


def render_report(report: CompanyResearchReport) -> str:
    lines = [
        f"# {cell(report.company.ticker)} — {cell(report.company.name)}",
        "",
        f"As of {report.as_of.isoformat()} · Research horizon: 24–36 months",
        "",
        report.executive_summary or "",
        "",
        "## Score and evidence",
        "",
        "| Signal | Availability | Score | Confidence | Composite contribution |",
        "| --- | --- | ---: | ---: | ---: |",
    ]
    for item in report.signal_assessments:
        lines.append(
            f"| {cell(item.signal_id)} · {cell(item.signal_name or '')} | "
            f"{item.availability or 'unavailable'} | {number(item.score)} | "
            f"{number(item.confidence, percent=True)} | {number(item.weighted_contribution)} |"
        )
    lines += [
        "",
        "Contributions are points above or below a 50-point baseline. "
        "Missing signals do not receive a neutral score. Coverage is the share of "
        "registered signals evaluated; confidence measures the evidence used, not the "
        "probability of investment success.",
        "",
    ]
    for title, items in (
        ("Supporting evidence", report.supporting_evidence),
        ("Contradictory evidence", report.contradictory_evidence),
        ("Context and corroboration", report.contextual_evidence),
        ("Risks", report.key_risks),
    ):
        lines += [
            f"## {title}",
            "",
            *([f"- {item}" for item in items] or ["No evidence supplied."]),
            "",
        ]
    lines += ["## Valuation scenarios", "", "| Measure | Value |", "| --- | ---: |"]
    valuation = report.valuation
    if valuation:
        for label, value in (
            ("Current price", valuation.current_market_price),
            ("Conservative value per share", valuation.reverse_dcf_conservative_value),
            ("Base value per share", valuation.reverse_dcf_base_value),
            ("Optimistic value per share", valuation.reverse_dcf_optimistic_value),
        ):
            lines.append(f"| {label} | {number(value)} |")
        lines += [
            f"| Implied revenue growth | {number(valuation.implied_revenue_growth, percent=True)} |",
            f"| Terminal-value dependence | {number(valuation.terminal_value_dependence, percent=True)} |",
        ]
    lines += [
        "",
        "Scenario values depend on the supplied inputs and assumptions. "
        "Missing valuations are left unavailable.",
        "",
        "## Research to complete",
        "",
        "- Write the variant perception: what is the market overlooking?",
        "- Record catalysts and dates in a thesis file.",
        "- Define measurable invalidation rules and review them after new filings.",
        "",
        "## Data quality",
        "",
    ]
    warnings = (
        *report.exclusion_reasons,
        *report.stale_evidence_warnings,
        *report.point_in_time_warnings,
        *report.missing_evidence,
    )
    lines += [f"- {item}" for item in warnings] or ["No diagnostics."]
    periods = report.provenance.get("financial_periods", {})
    if periods:
        lines += [
            "",
            "### Financial periods",
            "",
            "| Feature | Reporting period |",
            "| --- | --- |",
        ]
        lines += [
            f"| {cell(key.removeprefix('period:'))} | {cell(value)} |"
            for key, value in sorted(periods.items())
        ]
    lines += ["", "## Sources", "", "| Source | Reference | Available at |", "| --- | --- | --- |"]
    sources = report.provenance.get("sources", {})
    for key in sorted(set(sources) | set(report.source_timestamps)):
        stamp = report.source_timestamps.get(key)
        lines.append(
            f"| {cell(key)} | {cell(sources.get(key, 'Unspecified'))} | "
            f"{cell(stamp.isoformat() if stamp else 'Not supplied')} |"
        )
    lines += ["", "## Signal details", ""]
    for item in report.signal_assessments:
        if item.availability == SignalAvailability.AVAILABLE:
            lines += [
                f"### {cell(item.signal_id)} · {cell(item.signal_name or '')}",
                "",
                item.thesis or "",
                "",
                *[f"- {value}" for value in item.evidence or ()],
                "",
            ]
    lines += [f"Report ID: `{report_id(report)}`", ""]
    return "\n".join(lines)


def render_screen(reports: list[CompanyResearchReport]) -> str:
    lines = [
        "# SMCT Research — Discovery screen",
        "",
        "Research candidates for a 24–36 month horizon. Review evidence coverage and risks "
        "before interpreting the score. Imported examples are synthetic, not current market data.",
        "",
        "| Rank | Company | Score | Confidence | Coverage | Eligible | Warnings |",
        "| ---: | --- | ---: | ---: | ---: | --- | --- |",
    ]
    for report in reports:
        warnings = (
            *report.exclusion_reasons,
            *report.stale_evidence_warnings,
            *report.point_in_time_warnings,
        )
        lines.append(
            f"| {report.rank or '—'} | {cell(report.company.ticker)} · {cell(report.company.name)} | "
            f"{number(report.composite_score)} | {number(report.composite_confidence, percent=True)} | "
            f"{report.feature_completeness_percentage:.0f}% | {report.universe_eligible} | "
            f"{cell('; '.join(warnings))} |"
        )
    if not reports:
        lines += ["", "No companies matched these filters."]
    return "\n".join(lines) + "\n"
