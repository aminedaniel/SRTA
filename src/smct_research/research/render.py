from __future__ import annotations

from smct_research.research.models import CompanyResearchReport


def _esc(text: object) -> str:
    return str(text).replace("\\", "\\\\").replace("|", "\\|").replace("\n", " ").strip()


def _bullets(items: tuple[str, ...] | list[str]) -> str:
    return "\n".join(f"- {_esc(i)}" for i in items) if items else "- Missing"


def render_markdown(report: CompanyResearchReport) -> str:
    lines: list[str] = [f"# {report.ticker} — Company Research Report", ""]
    lines += [
        "## Research snapshot",
        f"- Report ID: {report.report_id}",
        f"- As of: {report.as_of.isoformat()}",
        f"- Company: {_esc(report.company_name)}",
        f"- Universe eligible: {report.universe_eligible}",
        f"- Exclusion reasons: {', '.join(report.exclusion_reasons) if report.exclusion_reasons else 'Missing'}",
        "",
    ]
    lines += [
        "## Executive summary",
        report.executive_summary,
        "",
        "## Variant perception",
        report.variant_perception,
        "",
    ]
    lines += [
        "## Research score",
        f"- Rank: {report.rank if report.rank is not None else 'Missing'}",
        f"- Composite score: {report.composite_research_score if report.composite_research_score is not None else 'Missing'}",
        f"- Composite confidence: {report.composite_confidence if report.composite_confidence is not None else 'Missing'}",
        f"- Feature completeness: {report.feature_completeness:.1f}%",
        "",
    ]
    lines += [
        "## Signal assessment",
        "| Signal | Direction | Score | Confidence | Weighted contribution | Assessment |",
        "|---|---:|---:|---:|---:|---|",
    ]
    for a in report.signal_assessments:
        lines.append(
            f"| {_esc(a.signal_name or a.signal_id)} | {_esc(a.direction.value if a.direction else a.availability)} | {_esc(a.score if a.score is not None else 'Unavailable')} | {_esc(a.confidence if a.confidence is not None else 'Unavailable')} | {_esc(a.weighted_contribution if a.weighted_contribution is not None else 'Unavailable')} | {_esc(a.thesis or a.availability)} |"
        )
    v = report.valuation_summary
    lines += [
        "",
        "## Valuation and market expectations",
        f"- Current market price: {v.current_market_price if v.current_market_price is not None else 'Missing'}",
        f"- Reverse-DCF conservative/base/optimistic: {v.reverse_dcf_conservative_value if v.reverse_dcf_conservative_value is not None else 'Missing'} / {v.reverse_dcf_base_value if v.reverse_dcf_base_value is not None else 'Missing'} / {v.reverse_dcf_optimistic_value if v.reverse_dcf_optimistic_value is not None else 'Missing'}",
        f"- Implied revenue growth: {v.implied_revenue_growth if v.implied_revenue_growth is not None else 'Missing'}",
        f"- Implied terminal margin: {v.implied_terminal_margin if v.implied_terminal_margin is not None else 'Missing'}",
        f"- Implied expectations gap: {v.implied_expectations_gap if v.implied_expectations_gap is not None else 'Missing'}",
        f"- Terminal-value dependence: {v.terminal_value_dependence if v.terminal_value_dependence is not None else 'Missing'}",
        f"- Dilution/share-count risk: {v.dilution_or_share_count_risk or 'Missing'}",
        "- Missing valuation fields:",
        _bullets(v.missing_valuation_fields),
        "",
    ]
    for title, items in [
        ("Supporting evidence", report.supporting_evidence),
        ("Contradictory evidence", report.contradictory_evidence),
        ("Catalysts", report.catalysts),
        ("Contextual evidence", report.contextual_evidence),
        ("Key risks", report.key_risks),
        ("Invalidation conditions", report.invalidation_conditions),
    ]:
        lines += [f"## {title}", _bullets(items), ""]
        if title == "Catalysts" and not items:
            lines[-2] = "- " + report.catalyst_summary
    lines += [
        "## Missing and stale evidence",
        "### Missing evidence",
        _bullets(report.missing_evidence),
        "### Unavailable signals",
        _bullets(report.unavailable_signals),
        "### Stale-evidence warnings",
        _bullets(report.stale_evidence_warnings),
        "### Point-in-time warnings",
        _bullets(report.point_in_time_warnings),
        "",
    ]
    lines += ["## Provenance"] + [
        f"- {_esc(k)}: {_esc(v)}" for k, v in sorted(report.provenance.items())
    ]
    lines += ["### Source timestamps"] + [
        f"- {_esc(k)}: {_esc(v.isoformat())}" for k, v in sorted(report.source_timestamps.items())
    ]
    lines.append("")
    return "\n".join(lines)
