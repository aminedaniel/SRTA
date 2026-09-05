from smct_research.core.signal import SignalRegistry
from smct_research.signals.congressional_purchases import CongressionalPurchaseSignal
from smct_research.signals.developer_ecosystem_momentum import DeveloperEcosystemMomentumSignal
from smct_research.signals.estimate_revision_velocity import ConsensusEstimateRevisionSignal
from smct_research.signals.fed_regime import FederalReserveRegimeSignal
from smct_research.signals.financial_quality import FinancialQualitySignal
from smct_research.signals.form4_cluster_buying import Form4ClusterBuyingSignal
from smct_research.signals.reddit_awareness import RedditAwarenessSignal
from smct_research.signals.renaissance_public_equity import RenaissancePublicEquityActivitySignal
from smct_research.signals.reverse_dcf_expectations import ReverseDCFExpectationsSignal
from smct_research.signals.valuation_compression import ValuationCompressionSignal


def default_registry() -> SignalRegistry:
    registry = SignalRegistry()
    registry.register(ValuationCompressionSignal())
    registry.register(ReverseDCFExpectationsSignal())
    registry.register(ConsensusEstimateRevisionSignal())
    registry.register(DeveloperEcosystemMomentumSignal())
    registry.register(RedditAwarenessSignal())
    registry.register(CongressionalPurchaseSignal())
    registry.register(Form4ClusterBuyingSignal())
    registry.register(FederalReserveRegimeSignal())
    registry.register(RenaissancePublicEquityActivitySignal())
    registry.register(FinancialQualitySignal())
    return registry
