from smct_research.core.models import FeatureSnapshot, SignalDirection
from smct_research.signals.reddit_awareness import RedditAwarenessSignal
from smct_research.signals.valuation_compression import ValuationCompressionSignal


def test_valuation_compression_detects_disconnect() -> None:
    snapshot = FeatureSnapshot(
        ticker="TEST",
        values={
            "ev_sales_current": 2.0,
            "ev_sales_3y_median": 6.0,
            "revenue_growth_current": 0.18,
            "revenue_growth_3y_median": 0.25,
        },
    )
    result = ValuationCompressionSignal().evaluate(snapshot)
    assert result.direction == SignalDirection.POSITIVE
    assert result.score > 50


def test_reddit_signal_rewards_underfollowed_momentum() -> None:
    snapshot = FeatureSnapshot(
        ticker="TEST",
        values={
            "reddit_mentions_30d": 35,
            "reddit_mentions_percentile": 15,
            "operating_momentum_score": 75,
            "promotional_language_share": 4,
        },
    )
    result = RedditAwarenessSignal().evaluate(snapshot)
    assert result.direction == SignalDirection.POSITIVE
    assert result.score > 20


def test_congressional_signal_rewards_broad_recent_buying() -> None:
    from smct_research.signals.congressional_purchases import CongressionalPurchaseSignal

    snapshot = FeatureSnapshot(
        ticker="TEST",
        values={
            "congress_purchase_count_90d": 7,
            "congress_sale_count_90d": 0,
            "congress_unique_buyers_90d": 4,
            "congress_estimated_purchase_usd_90d": 850_000,
            "congress_latest_purchase_age_days": 18,
            "congress_median_disclosure_lag_days": 22,
            "congress_committee_relevance_score": 75,
            "congress_repeat_buyer_score": 65,
        },
    )
    result = CongressionalPurchaseSignal().evaluate(snapshot)
    assert result.direction == SignalDirection.POSITIVE
    assert result.score > 40
    assert result.confidence > 0.5


def test_congressional_signal_penalizes_sale_dominance() -> None:
    from smct_research.signals.congressional_purchases import CongressionalPurchaseSignal

    snapshot = FeatureSnapshot(
        ticker="TEST",
        values={
            "congress_purchase_count_90d": 0,
            "congress_sale_count_90d": 6,
            "congress_unique_buyers_90d": 0,
            "congress_estimated_purchase_usd_90d": 0,
            "congress_latest_purchase_age_days": 0,
            "congress_median_disclosure_lag_days": 15,
            "congress_committee_relevance_score": 0,
            "congress_repeat_buyer_score": 0,
        },
    )
    result = CongressionalPurchaseSignal().evaluate(snapshot)
    assert result.direction == SignalDirection.NEGATIVE
    assert result.score < -20
