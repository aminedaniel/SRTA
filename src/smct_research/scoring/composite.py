from __future__ import annotations

from math import isfinite

from pydantic import BaseModel, Field

from smct_research.core.models import SignalDirection, SignalResult


class ResearchScore(BaseModel):
    ticker: str
    score: float = Field(ge=0, le=100)
    confidence: float = Field(ge=0, le=1)
    positive_signals: list[str]
    negative_signals: list[str]
    explanations: list[str]


class CompositeResearchScorer:
    def __init__(self, weights: dict[str, float] | None = None) -> None:
        self.weights = (
            weights
            if weights is not None
            else {
                "A1": 1.25,
                "A2": 1.10,
                "A3": 1.00,
                "B1": 0.85,
                "F1": 0.75,
                "E2": 0.90,
                "E3": 0.85,
                "I1": 0.25,
                "Q1": 1.15,
            }
        )
        if any(not isfinite(value) or value < 0 for value in self.weights.values()):
            raise ValueError("Signal weights must be finite and nonnegative")

    def score(self, results: list[SignalResult]) -> ResearchScore:
        if not results:
            raise ValueError("At least one signal result is required")
        ticker = results[0].ticker
        if any(result.ticker != ticker for result in results):
            raise ValueError("Cannot combine signal results from multiple tickers")
        if len({result.signal_id for result in results}) != len(results):
            raise ValueError("Cannot combine duplicate signal IDs")

        weighted_sum = 0.0
        weight_total = 0.0
        confidence_sum = 0.0
        positives: list[str] = []
        negatives: list[str] = []
        explanations: list[str] = []

        for result in results:
            weight = self.weights.get(result.signal_id, 1.0)
            weighted_sum += result.score * result.confidence * weight
            weight_total += result.confidence * weight
            confidence_sum += result.confidence
            explanations.append(f"{result.signal_id}: {result.thesis}")
            if result.direction == SignalDirection.POSITIVE:
                positives.append(result.signal_id)
            elif result.direction == SignalDirection.NEGATIVE:
                negatives.append(result.signal_id)

        if weight_total == 0:
            raise ValueError("No signal has positive confidence and weight")
        normalized = weighted_sum / weight_total
        research_score = max(0.0, min(100.0, 50 + normalized / 2))
        return ResearchScore(
            ticker=ticker,
            score=research_score,
            confidence=confidence_sum / len(results),
            positive_signals=positives,
            negative_signals=negatives,
            explanations=explanations,
        )
