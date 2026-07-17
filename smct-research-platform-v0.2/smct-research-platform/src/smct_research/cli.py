from __future__ import annotations

import json
from pathlib import Path

import typer

from smct_research.core.models import FeatureSnapshot
from smct_research.core.signal import SignalRegistry
from smct_research.scoring.composite import CompositeResearchScorer
from smct_research.signals.congressional_purchases import CongressionalPurchaseSignal
from smct_research.signals.fed_regime import FederalReserveRegimeSignal
from smct_research.signals.reddit_awareness import RedditAwarenessSignal
from smct_research.signals.valuation_compression import ValuationCompressionSignal

app = typer.Typer(no_args_is_help=True)


def default_registry() -> SignalRegistry:
    registry = SignalRegistry()
    registry.register(ValuationCompressionSignal())
    registry.register(RedditAwarenessSignal())
    registry.register(CongressionalPurchaseSignal())
    registry.register(FederalReserveRegimeSignal())
    return registry


@app.command()
def evaluate(path: Path) -> None:
    """Evaluate a JSON feature snapshot and print a research-priority score."""
    snapshot = FeatureSnapshot.model_validate_json(path.read_text())
    results = default_registry().evaluate_all(snapshot)
    composite = CompositeResearchScorer().score(results)
    typer.echo(json.dumps(composite.model_dump(), indent=2))
