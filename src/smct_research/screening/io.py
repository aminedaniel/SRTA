from __future__ import annotations

import csv
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from smct_research.core.models import FeatureSnapshot
from smct_research.screening.models import RankedResult, UniverseEntry


def load_universe(path: Path) -> list[UniverseEntry]:
    """Load canonical universe rows from a JSON array or header-based CSV file."""
    if path.suffix.lower() == ".json":
        payload: Any = json.loads(path.read_text())
        if isinstance(payload, dict):
            if "companies" not in payload and "universe" not in payload:
                raise ValueError("Universe JSON object must contain a companies or universe array")
            payload = payload.get("companies", payload.get("universe"))
        if not isinstance(payload, list):
            raise ValueError("Universe JSON must be an array or contain a companies array")
    elif path.suffix.lower() == ".csv":
        with path.open(newline="") as handle:
            payload = list(csv.DictReader(handle))
    else:
        raise ValueError("Universe input must be a .json or .csv file")
    return [UniverseEntry.model_validate(_normalize_company(row)) for row in payload]


def load_feature_snapshots(
    directory: Path, as_of: datetime | None = None
) -> dict[str, FeatureSnapshot]:
    if not directory.is_dir():
        raise ValueError(f"Features directory does not exist: {directory}")
    snapshots: dict[str, FeatureSnapshot] = {}
    for path in sorted(directory.glob("*.json")):
        snapshot = FeatureSnapshot.model_validate_json(path.read_text())
        snapshots[snapshot.ticker] = snapshot
    return snapshots


def write_json(path: Path, results: list[RankedResult]) -> None:
    path.write_text(
        json.dumps([result.model_dump(mode="json") for result in results], indent=2) + "\n"
    )


def write_csv(path: Path, results: list[RankedResult]) -> None:
    rows = [_flat_row(result) for result in results]
    fields = list(rows[0]) if rows else ["rank", "ticker", "company_name", "composite_score"]
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def _normalize_company(row: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(row)
    aliases = {
        "company name": "name",
        "company_name": "name",
        "market capitalization": "market_cap_usd",
        "market_cap": "market_cap_usd",
        "average daily dollar volume": "average_daily_dollar_volume",
        "average_daily_volume": "average_daily_dollar_volume",
        "active/inactive status": "is_active",
        "active": "is_active",
        "CIK": "cik",
    }
    for old, new in aliases.items():
        if old in normalized and new not in normalized:
            normalized[new] = normalized[old]
    for key in ("market_cap_usd", "average_daily_dollar_volume"):
        if normalized.get(key) == "":
            normalized[key] = None
    if isinstance(normalized.get("is_active"), str):
        normalized["is_active"] = normalized["is_active"].strip().lower() in {
            "true",
            "1",
            "yes",
            "active",
        }
    return normalized


def _flat_row(result: RankedResult) -> dict[str, str | int | float | None]:
    return {
        "rank": result.rank,
        "ticker": result.ticker,
        "company_name": result.company_name,
        "composite_score": result.composite_score,
        "composite_confidence": result.composite_confidence,
        "universe_eligible": result.universe_eligible,
        "exclusion_reasons": "; ".join(result.exclusion_reasons),
        "positive_signals": "; ".join(result.positive_signals),
        "negative_signals": "; ".join(result.negative_signals),
        "unavailable_signals": "; ".join(result.unavailable_signals),
        "top_supporting_explanations": " | ".join(result.top_supporting_explanations),
        "evaluation_timestamp": result.evaluation_timestamp.isoformat(),
        "signals_evaluated": result.signals_evaluated,
        "signals_unavailable": result.signals_unavailable,
        "feature_completeness_percentage": result.feature_completeness_percentage,
        "stale_evidence_warnings": "; ".join(result.stale_evidence_warnings),
        "point_in_time_eligibility_warnings": "; ".join(result.point_in_time_eligibility_warnings),
    }
