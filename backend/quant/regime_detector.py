"""
Regime Detection — adapts strategy weights to current market environment.

The same signal deserves different weight depending on whether the market
is information-driven, rumor-driven, in a liquidity vacuum, etc.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Regime(str, Enum):
    INFORMATION_DRIVEN = "information_driven"
    RUMOR_DRIVEN = "rumor_driven"
    LIQUIDITY_VACUUM = "liquidity_vacuum"
    EVENT_COUNTDOWN = "event_countdown"
    NARRATIVE_MOMENTUM = "narrative_momentum"
    CONSENSUS_GRIND = "consensus_grind"


@dataclass
class RegimeWeights:
    evidence: float
    base_rate: float
    sentiment: float
    microstructure: float


@dataclass
class RegimeAssessment:
    regime: Regime
    confidence: float
    weights: RegimeWeights
    posture: str
    description: str


REGIME_CONFIGS = {
    Regime.INFORMATION_DRIVEN: {
        "weights": RegimeWeights(evidence=0.40, base_rate=0.20, sentiment=0.10, microstructure=0.30),
        "posture": "aggressive",
        "description": "Real new data just hit. Market repricing on facts.",
    },
    Regime.RUMOR_DRIVEN: {
        "weights": RegimeWeights(evidence=0.15, base_rate=0.45, sentiment=0.05, microstructure=0.35),
        "posture": "selective — wait for confirmation",
        "description": "Price moving on unverified narrative.",
    },
    Regime.LIQUIDITY_VACUUM: {
        "weights": RegimeWeights(evidence=0.20, base_rate=0.30, sentiment=0.10, microstructure=0.40),
        "posture": "passive or no-trade",
        "description": "Wide spreads, low depth, stale quotes.",
    },
    Regime.EVENT_COUNTDOWN: {
        "weights": RegimeWeights(evidence=0.30, base_rate=0.20, sentiment=0.15, microstructure=0.35),
        "posture": "rules precision + timing critical",
        "description": "Known event approaching. Price compressing.",
    },
    Regime.NARRATIVE_MOMENTUM: {
        "weights": RegimeWeights(evidence=0.25, base_rate=0.40, sentiment=0.05, microstructure=0.30),
        "posture": "fade the narrative — look for reversion",
        "description": "Story is being chased. Facts lag narrative.",
    },
    Regime.CONSENSUS_GRIND: {
        "weights": RegimeWeights(evidence=0.20, base_rate=0.35, sentiment=0.20, microstructure=0.25),
        "posture": "selective — edge likely small",
        "description": "Market slowly crawling toward a known outcome.",
    },
}


def detect_regime(
    spread_pct: float,
    volume_24h: float,
    liquidity: float,
    hours_to_close: float,
    price_change_1h: float = 0.0,
    volume_spike: bool = False,
    has_news_catalyst: bool = False,
) -> RegimeAssessment:
    """
    Detect the current market regime from observable metrics.

    Returns regime with confidence and adaptive weights.
    """

    # Liquidity vacuum check
    if spread_pct > 0.05 or liquidity < 5_000:
        config = REGIME_CONFIGS[Regime.LIQUIDITY_VACUUM]
        return RegimeAssessment(
            regime=Regime.LIQUIDITY_VACUUM,
            confidence=min(spread_pct * 10, 1.0),
            weights=config["weights"],
            posture=config["posture"],
            description=config["description"],
        )

    # Event countdown
    if hours_to_close < 48:
        config = REGIME_CONFIGS[Regime.EVENT_COUNTDOWN]
        return RegimeAssessment(
            regime=Regime.EVENT_COUNTDOWN,
            confidence=min(1.0, 48 / max(hours_to_close, 1)),
            weights=config["weights"],
            posture=config["posture"],
            description=config["description"],
        )

    # Information-driven (volume spike + news catalyst)
    if volume_spike and has_news_catalyst:
        config = REGIME_CONFIGS[Regime.INFORMATION_DRIVEN]
        return RegimeAssessment(
            regime=Regime.INFORMATION_DRIVEN,
            confidence=0.8,
            weights=config["weights"],
            posture=config["posture"],
            description=config["description"],
        )

    # Rumor-driven (volume spike without catalyst)
    if volume_spike and not has_news_catalyst:
        config = REGIME_CONFIGS[Regime.RUMOR_DRIVEN]
        return RegimeAssessment(
            regime=Regime.RUMOR_DRIVEN,
            confidence=0.7,
            weights=config["weights"],
            posture=config["posture"],
            description=config["description"],
        )

    # Narrative momentum (big price change on thin volume)
    if abs(price_change_1h) > 0.05 and volume_24h < liquidity * 0.3:
        config = REGIME_CONFIGS[Regime.NARRATIVE_MOMENTUM]
        return RegimeAssessment(
            regime=Regime.NARRATIVE_MOMENTUM,
            confidence=0.6,
            weights=config["weights"],
            posture=config["posture"],
            description=config["description"],
        )

    # Default: consensus grind
    config = REGIME_CONFIGS[Regime.CONSENSUS_GRIND]
    return RegimeAssessment(
        regime=Regime.CONSENSUS_GRIND,
        confidence=0.5,
        weights=config["weights"],
        posture=config["posture"],
        description=config["description"],
    )
