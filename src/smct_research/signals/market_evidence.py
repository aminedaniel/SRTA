"""Market observations complement fundamental research; no order is generated."""

from smct_research.core.models import FeatureSnapshot, SignalDirection, SignalResult
from smct_research.core.signal import ResearchSignal


def _result(
    signal: ResearchSignal,
    snapshot: FeatureSnapshot,
    score: float,
    thesis: str,
    evidence: list[str],
) -> SignalResult:
    return SignalResult(
        signal_id=signal.id,
        ticker=snapshot.ticker,
        score=max(-100, min(100, score)),
        confidence=0.6,
        direction=(
            SignalDirection.POSITIVE
            if score > 15
            else SignalDirection.NEGATIVE
            if score < -15
            else SignalDirection.NEUTRAL
        ),
        thesis=thesis,
        evidence=evidence,
        risks=["Historical prices and multiples do not establish future returns."],
    )


class ForwardPEReversionSignal(ResearchSignal):
    id = "V1"
    name = "Forward P/E five-year z-score"
    required_features = ("forward_pe_zscore_5y",)

    def evaluate(self, snapshot: FeatureSnapshot) -> SignalResult:
        z = snapshot.require_float("forward_pe_zscore_5y")
        return _result(
            self,
            snapshot,
            -40 * z,
            "Forward valuation relative to its own history.",
            [f"Forward P/E z-score: {z:.2f}."],
        )


class TrendPullbackSignal(ResearchSignal):
    id = "T1"
    name = "Trend-filtered RSI pullback"
    required_features = ("close", "ema_200", "rsi_14")

    def evaluate(self, snapshot: FeatureSnapshot) -> SignalResult:
        price, ema, rsi = (snapshot.require_float(key) for key in self.required_features)
        if price <= 0 or ema <= 0 or not 0 <= rsi <= 100:
            raise ValueError("Invalid price, EMA or RSI")
        score = 50 if price > ema and rsi < 30 else -40 if price < ema else 0
        return _result(
            self,
            snapshot,
            score,
            "Price trend and pullback context.",
            [f"Close {price:.2f}; EMA200 {ema:.2f}; RSI14 {rsi:.1f}."],
        )


class MomentumSignal(ResearchSignal):
    id = "T2"
    name = "Twelve-minus-one-month momentum"
    required_features = ("momentum_12_1",)

    def evaluate(self, snapshot: FeatureSnapshot) -> SignalResult:
        momentum = snapshot.require_float("momentum_12_1")
        return _result(
            self,
            snapshot,
            momentum * 150,
            "Medium-term price momentum.",
            [f"12-1 month adjusted-price return: {momentum:.1%}."],
        )


class CompressionSignal(ResearchSignal):
    id = "T3"
    name = "Bollinger width compression context"
    required_features = ("bollinger_width_percentile_100d",)

    def evaluate(self, snapshot: FeatureSnapshot) -> SignalResult:
        percentile = snapshot.require_float("bollinger_width_percentile_100d")
        if not 0 <= percentile <= 100:
            raise ValueError("Bollinger width percentile must be 0..100")
        # Compression has no directional edge by itself. This signal must not reward it.
        return _result(
            self,
            snapshot,
            0,
            "Volatility compression has no directional bias.",
            [f"Bollinger width percentile: {percentile:.1f}."],
        )
