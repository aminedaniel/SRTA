"""Point-in-time ingestion of dated market and licensed/manual research exports.

Input data are local exports: this module never invents observations or calls a vendor.
"""

from __future__ import annotations

import csv
from collections import defaultdict
from datetime import UTC, date, datetime, time, timedelta
from math import isfinite, sqrt
from pathlib import Path

from smct_research.core.models import FeatureSnapshot, normalize_utc


def _rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def _available(day: str) -> datetime:
    # Daily closing observations only become available on the next UTC day.
    return datetime.combine(date.fromisoformat(day) + timedelta(days=1), time.min, UTC)


def _ema(values: list[float], length: int) -> float:
    result = sum(values[:length]) / length
    for value in values[length:]:
        result += (value - result) * 2 / (length + 1)
    return result


def _rsi(values: list[float]) -> float:
    changes = [b - a for a, b in zip(values[-15:-1], values[-14:], strict=True)]
    gain = sum(max(0, x) for x in changes) / 14
    loss = sum(max(0, -x) for x in changes) / 14
    return 100 if loss == 0 else 100 - 100 / (1 + gain / loss)


def _width(values: list[float]) -> float:
    mean = sum(values) / 20
    std = sqrt(sum((value - mean) ** 2 for value in values) / 20)
    return 4 * std / mean


def enrich(
    base: dict[str, FeatureSnapshot],
    as_of: datetime,
    *,
    prices: Path | None = None,
    multiples: Path | None = None,
    evidence: Path | None = None,
) -> dict[str, FeatureSnapshot]:
    """Merge only public observations into a snapshot, preserving source timestamps.

    prices columns: ticker,date,adjusted_close; multiples: ticker,date,forward_pe;
    evidence: ticker,feature,value,available_at,source (ISO 8601 timestamp).
    """
    cutoff = normalize_utc(as_of)
    output: dict[str, FeatureSnapshot] = {}
    for ticker, snapshot in base.items():
        if snapshot.as_of > cutoff or any(t > cutoff for t in snapshot.source_as_of.values()):
            raise ValueError(f"Future base evidence for {ticker}")
        output[ticker] = snapshot.model_copy(deep=True, update={"as_of": cutoff})

    def put(ticker: str, key: str, value: float, available: datetime, source: str) -> None:
        if ticker not in output or available > cutoff:
            return
        if not source or not key:
            raise ValueError("Each observation needs a source and feature name")
        snapshot = output[ticker]
        # Never silently replace SEC evidence with an unrelated vendor field.
        if key in snapshot.values:
            raise ValueError(f"Duplicate feature for {ticker}: {key}")
        snapshot.values[key] = value
        source_key = f"feature:{key}"
        snapshot.sources[source_key] = source
        snapshot.source_as_of[source_key] = available

    if prices:
        history: dict[str, dict[date, float]] = defaultdict(dict)
        for row in _rows(prices):
            ticker = row["ticker"].strip().upper()
            day = date.fromisoformat(row["date"])
            price = float(row["adjusted_close"])
            if not isfinite(price) or price <= 0:
                raise ValueError("Adjusted closes must be positive")
            if _available(row["date"]) <= cutoff:
                if day in history[ticker]:
                    raise ValueError(f"Duplicate adjusted close: {ticker} {day}")
                history[ticker][day] = price
        for ticker, daily in history.items():
            ordered = sorted(daily.items())
            if not ordered or (cutoff.date() - ordered[-1][0]).days > 7:
                continue
            values = [price for _, price in ordered]
            available = _available(ordered[-1][0].isoformat())
            if len(values) >= 200:
                put(ticker, "close", values[-1], available, str(prices))
                put(ticker, "ema_200", _ema(values, 200), available, str(prices))
                put(ticker, "rsi_14", _rsi(values), available, str(prices))
            if len(values) >= 253:
                put(ticker, "momentum_12_1", values[-22] / values[-253] - 1, available, str(prices))
            if len(values) >= 119:
                widths = [
                    _width(values[i - 19 : i + 1]) for i in range(len(values) - 100, len(values))
                ]
                current = widths[-1]
                put(
                    ticker,
                    "bollinger_width_percentile_100d",
                    100 * sum(w <= current for w in widths) / len(widths),
                    available,
                    str(prices),
                )

    if multiples:
        history_pe: dict[str, dict[date, float]] = defaultdict(dict)
        for row in _rows(multiples):
            day = date.fromisoformat(row["date"])
            pe = float(row["forward_pe"])
            if not isfinite(pe) or pe <= 0:
                continue  # negative/zero consensus EPS is not a meaningful P/E
            if _available(row["date"]) <= cutoff and day >= cutoff.date() - timedelta(days=1830):
                ticker = row["ticker"].strip().upper()
                if day in history_pe[ticker]:
                    raise ValueError(f"Duplicate multiple: {ticker} {day}")
                history_pe[ticker][day] = pe
        for ticker, daily in history_pe.items():
            ordered = sorted(daily.items())
            if (
                len(ordered) < 24
                or (ordered[-1][0] - ordered[0][0]).days < 1460
                or (cutoff.date() - ordered[-1][0]).days > 45
            ):
                continue
            values = [value for _, value in ordered]
            mean = sum(values) / len(values)
            std = sqrt(sum((value - mean) ** 2 for value in values) / len(values))
            if std > 0:
                put(
                    ticker,
                    "forward_pe_zscore_5y",
                    (values[-1] - mean) / std,
                    _available(ordered[-1][0].isoformat()),
                    str(multiples),
                )

    if evidence:
        chosen: dict[tuple[str, str], tuple[datetime, float, str]] = {}
        for row in _rows(evidence):
            available = normalize_utc(
                datetime.fromisoformat(row["available_at"].replace("Z", "+00:00"))
            )
            if available > cutoff:
                continue
            feature_key = (row["ticker"].strip().upper(), row["feature"].strip())
            candidate = (available, float(row["value"]), row["source"].strip())
            if not isfinite(candidate[1]):
                raise ValueError(f"Non-finite evidence for {feature_key}")
            if feature_key in chosen and chosen[feature_key][0] == available:
                raise ValueError(f"Ambiguous observation for {feature_key}")
            if feature_key not in chosen or candidate[0] > chosen[feature_key][0]:
                chosen[feature_key] = candidate
        for (ticker, feature_name), (available, value, source) in sorted(chosen.items()):
            put(ticker, feature_name, value, available, source)
    return output
