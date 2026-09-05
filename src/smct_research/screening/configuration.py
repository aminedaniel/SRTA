"""Shared discovery settings; JSON or the bundled two-level YAML subset."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from smct_research.scoring.composite import CompositeResearchScorer
from smct_research.screening.registry import default_registry
from smct_research.screening.universe import UniversePolicy


def load_settings(path: Path | None) -> tuple[UniversePolicy, CompositeResearchScorer]:
    if path is None:
        return UniversePolicy(), CompositeResearchScorer()
    text = path.read_text(encoding="utf-8")
    data: dict[str, Any]
    if path.suffix.lower() == ".json":
        data = json.loads(text)
    else:
        data = {}
        section: str | None = None
        for raw in text.splitlines():
            line = raw.split("#", 1)[0].rstrip()
            if not line:
                continue
            if not line.startswith(" ") and line.endswith(":"):
                section = line[:-1]
                if section in data:
                    raise ValueError(f"Duplicate configuration section: {section}")
                data[section] = {}
                continue
            if section is None or not line.startswith("  ") or ":" not in line:
                raise ValueError("Use JSON or the bundled flat two-level YAML configuration format")
            key, value = line.strip().split(":", 1)
            value = value.strip()
            if key in data[section]:
                raise ValueError(f"Duplicate configuration key: {key}")
            try:
                parsed = json.loads(value)
            except json.JSONDecodeError:
                if value.startswith("[") and value.endswith("]"):
                    parsed = [
                        item.strip().strip("\"'") for item in value[1:-1].split(",") if item.strip()
                    ]
                else:
                    parsed = value.strip("\"'")
            data[section][key] = parsed
    if not isinstance(data, dict):
        raise ValueError("Configuration must be an object")
    unknown = set(data) - {"universe", "signal_weights", "research"}
    if unknown:
        raise ValueError(
            f"Unknown configuration sections: {', '.join(sorted(unknown))}; use signal_weights for scoring"
        )
    policy = UniversePolicy.model_validate(data.get("universe", {}))
    weights = dict(CompositeResearchScorer().weights)
    overrides = data.get("signal_weights", {})
    if not isinstance(overrides, dict):
        raise ValueError("signal_weights must be a mapping")
    unknown_signals = set(overrides) - {s.id for s in default_registry().all()}
    if unknown_signals:
        raise ValueError(f"Unknown signal IDs: {', '.join(sorted(unknown_signals))}")
    for key, value in overrides.items():
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"Signal weight must be numeric: {key}")
        weights[key] = float(value)
    return policy, CompositeResearchScorer(weights)
