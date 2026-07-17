from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterable

from smct_research.core.models import FeatureSnapshot, SignalResult


class ResearchSignal(ABC):
    id: str
    name: str
    required_features: tuple[str, ...] = ()

    def validate(self, snapshot: FeatureSnapshot) -> None:
        missing = [name for name in self.required_features if snapshot.values.get(name) is None]
        if missing:
            raise KeyError(f"{self.id} missing required features: {', '.join(missing)}")

    @abstractmethod
    def evaluate(self, snapshot: FeatureSnapshot) -> SignalResult:
        raise NotImplementedError


class SignalRegistry:
    def __init__(self) -> None:
        self._signals: dict[str, ResearchSignal] = {}

    def register(self, signal: ResearchSignal) -> None:
        if signal.id in self._signals:
            raise ValueError(f"Duplicate signal id: {signal.id}")
        self._signals[signal.id] = signal

    def get(self, signal_id: str) -> ResearchSignal:
        return self._signals[signal_id]

    def all(self) -> Iterable[ResearchSignal]:
        return self._signals.values()

    def evaluate_all(self, snapshot: FeatureSnapshot) -> list[SignalResult]:
        results: list[SignalResult] = []
        for signal in self._signals.values():
            try:
                signal.validate(snapshot)
            except KeyError:
                continue
            results.append(signal.evaluate(snapshot))
        return results
