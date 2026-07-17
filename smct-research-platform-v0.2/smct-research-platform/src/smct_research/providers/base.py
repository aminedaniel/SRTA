"""Provider contracts; adapters own I/O and signals remain deterministic."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass
from time import sleep
from typing import Any, Protocol


class ProviderError(Exception):
    """Base error for an external data adapter."""


class ProviderConfigurationError(ProviderError):
    pass


class ProviderRequestError(ProviderError):
    pass


class ProviderRateLimitError(ProviderError):
    pass


class ProviderResponseError(ProviderError):
    pass


class RateLimiter(Protocol):
    def acquire(self) -> None: ...


class RetryPolicy(Protocol):
    def run(self, operation: Callable[[], Any]) -> Any: ...


@dataclass(frozen=True)
class FixedRetryPolicy:
    attempts: int = 3
    delay_seconds: float = 0.5

    def run(self, operation: Callable[[], Any]) -> Any:
        last_error: ProviderRequestError | None = None
        for attempt in range(self.attempts):
            try:
                return operation()
            except ProviderRequestError as error:
                last_error = error
                if attempt + 1 < self.attempts:
                    sleep(self.delay_seconds * (attempt + 1))
        assert last_error is not None
        raise last_error


class DataProvider(ABC):
    """Base interface for source adapters. Implementations must preserve raw evidence."""

    provider_id: str

    @abstractmethod
    def fetch(self, identifier: str) -> dict[str, Any]:
        """Fetch one raw provider document, optionally from its local cache."""
        raise NotImplementedError
