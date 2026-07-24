from __future__ import annotations

import json
from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel

from smct_research.core.models import normalize_utc
from smct_research.research.models import CompanyResearchReport, FrozenDict


def _esc(text: object) -> str:
    escaped = str(text).replace("\\", "\\\\").replace("|", "\\|").replace("\n", " ")
    for char in ("*", "_", "`", "[", "]", "<", ">", "#"):
        escaped = escaped.replace(char, "\\" + char)
    return escaped.strip()


def _bullets(items: tuple[str, ...] | list[str]) -> str:
    return "\n".join(f"- {_esc(i)}" for i in items) if items else "- Missing"


def _json_ready(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return _json_ready(value.model_dump(mode="python"))
    if isinstance(value, datetime):
        return normalize_utc(value).isoformat().replace("+00:00", "Z")
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, (dict, FrozenDict)):
        return {
            str(k): _json_ready(v) for k, v in sorted(value.items(), key=lambda item: str(item[0]))
        }
    if isinstance(value, (list, tuple)):
        return [_json_ready(v) for v in value]
    return value


def serialize_report_json(report: CompanyResearchReport) -> str:
    """Serialize reports deterministically while preserving ranked list order.

    The returned JSON always ends with a single newline. Report models already canonicalize
    unordered fields; ranked fields are emitted in model order.
    """
    return (
        json.dumps(_json_ready(report), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        + "\n"
    )


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
        _esc(report.executive_summary),
        "",
        "## Variant perception",
        _esc(report.variant_perception),
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
