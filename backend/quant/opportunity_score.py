"""
Opportunity Score (0-100) — auditable composite score for every trade.

Replaces ad-hoc signal weighting with a transparent formula.
Every trade gets a number you can sort by and review.

Thresholds:
  0:       HARD BLOCK
  1-39:    NO TRADE (watchlist only)
  40-59:   WATCHLIST (monitor, gather evidence)
  60-74:   PAPER TRADE (small size)
  75-89:   HIGH PRIORITY (full size)
  90+:     EXCEPTIONAL (flag for review + max size)
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from backend.quant.entropy import kl_divergence


class OpportunityAction(str, Enum):
    HARD_BLOCK = "HARD_BLOCK"
    NO_TRADE = "NO_TRADE"
    WATCHLIST = "WATCHLIST"
    PAPER_TRADE = "PAPER_TRADE"
    HIGH_PRIORITY = "HIGH_PRIORITY"
    EXCEPTIONAL = "EXCEPTIONAL"


@dataclass
class OpportunityResult:
    score: float
    action: OpportunityAction
    hard_block_reason: str = ""
    components: dict = None

    def __post_init__(self):
        if self.components is None:
            self.components = {}


# Weights sum to 1.10 (penalty can reduce below sum of positives)
WEIGHTS = {
    "mispricing":       0.18,
    "evidence_quality": 0.15,
    "resolution_clarity": 0.14,
    "liquidity_quality": 0.12,
    "regime_fit":       0.10,
    "confidence_cal":   0.10,
    "timing_quality":   0.08,
    "execution_realism": 0.08,
    "red_team_survival": 0.05,
    "correlation_penalty": -0.10,
}


def compute_opportunity_score(
    model_prob: float,
    market_price: float,
    evidence_quality: float = 0.5,
    resolution_clarity: float = 0.8,
    liquidity: float = 50_000,
    regime_fit: float = 0.5,
    calibration_score: float = 0.5,
    hours_to_close: float = 168,
    spread: float = 0.02,
    red_team_haircut: float = 0.1,
    portfolio_correlation: float = 0.0,
    model_disagreement: float = 0.0,
) -> OpportunityResult:
    """
    Compute opportunity score for a market.

    All component inputs should be 0-1 (except liquidity in USD and hours).
    """

    # Compute components
    mispricing = min(abs(model_prob - market_price) * 2, 1.0)  # scale to 0-1
    liquidity_q = min(liquidity / 100_000, 1.0)
    timing_q = _timing_quality(hours_to_close)
    edge_magnitude = abs(model_prob - market_price)
    exec_realism = 1.0 - min(spread / (edge_magnitude + 0.001), 1.0)
    red_team_surv = 1.0 - red_team_haircut

    components = {
        "mispricing": mispricing,
        "evidence_quality": evidence_quality,
        "resolution_clarity": resolution_clarity,
        "liquidity_quality": liquidity_q,
        "regime_fit": regime_fit,
        "confidence_cal": calibration_score,
        "timing_quality": timing_q,
        "execution_realism": exec_realism,
        "red_team_survival": red_team_surv,
        "correlation_penalty": portfolio_correlation,
    }

    # Hard blocks
    blocks = []
    if resolution_clarity < 0.4:
        blocks.append("Resolution rules too ambiguous")
    if liquidity_q < 0.1:
        blocks.append("Insufficient liquidity")
    if spread > edge_magnitude * 0.5 and edge_magnitude > 0:
        blocks.append("Spread eats >50% of edge")
    if evidence_quality < 0.2:
        blocks.append("Evidence too weak")
    if model_disagreement > 0.20:
        blocks.append("Models disagree by >20%")
    if portfolio_correlation > 0.7:
        blocks.append("Too correlated with open positions")

    if blocks:
        return OpportunityResult(
            score=0.0,
            action=OpportunityAction.HARD_BLOCK,
            hard_block_reason="; ".join(blocks),
            components=components,
        )

    # Weighted score
    raw = (
        WEIGHTS["mispricing"] * mispricing
        + WEIGHTS["evidence_quality"] * evidence_quality
        + WEIGHTS["resolution_clarity"] * resolution_clarity
        + WEIGHTS["liquidity_quality"] * liquidity_q
        + WEIGHTS["regime_fit"] * regime_fit
        + WEIGHTS["confidence_cal"] * calibration_score
        + WEIGHTS["timing_quality"] * timing_q
        + WEIGHTS["execution_realism"] * exec_realism
        + WEIGHTS["red_team_survival"] * red_team_surv
        + WEIGHTS["correlation_penalty"] * portfolio_correlation
    ) * 100

    score = max(0.0, min(100.0, raw))

    if score >= 90:
        action = OpportunityAction.EXCEPTIONAL
    elif score >= 75:
        action = OpportunityAction.HIGH_PRIORITY
    elif score >= 60:
        action = OpportunityAction.PAPER_TRADE
    elif score >= 40:
        action = OpportunityAction.WATCHLIST
    else:
        action = OpportunityAction.NO_TRADE

    return OpportunityResult(score=score, action=action, components=components)


def _timing_quality(hours: float) -> float:
    """Maps hours-to-close to a 0-1 timing quality score."""
    if hours < 1:
        return 0.3   # too close, low liquidity risk
    if hours < 6:
        return 0.7   # urgent but tradeable
    if hours < 48:
        return 1.0   # sweet spot
    if hours < 168:
        return 0.8   # normal
    return 0.5        # very far out, uncertain
