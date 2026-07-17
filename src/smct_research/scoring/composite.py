from __future__ import annotations

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
        self.weights = weights or {"A1": 1.25, "F1": 0.75, "E2": 0.90, "E3": 0.85, "I1": 0.25}

    def score(self, results: list[SignalResult]) -> ResearchScore:
        if not results:
            raise ValueError("At least one signal result is required")
        ticker = results[0].ticker
        if any(result.ticker != ticker for result in results):
            raise ValueError("Cannot combine signal results from multiple tickers")

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

        normalized = weighted_sum / weight_total if weight_total else 0.0
        research_score = max(0.0, min(100.0, 50 + normalized / 2))
        return ResearchScore(
            ticker=ticker,
            score=research_score,
            confidence=confidence_sum / len(results),
            positive_signals=positives,
            negative_signals=negatives,
            explanations=explanations,
        )
