"""
Shannon entropy, KL divergence, and Kelly criterion for prediction markets.

References
----------
- Shannon, C. E. (1948). A Mathematical Theory of Communication.
- Cover & Thomas (2006). Elements of Information Theory.
- Kelly, J. L. (1956). A New Interpretation of Information Rate.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum


# ── tiny guard to avoid log(0) ──────────────────────────────────────
_EPS = 1e-12


# ── core functions ──────────────────────────────────────────────────


def market_entropy(price: float) -> float:
    """
    Shannon entropy H(p) for a binary market in bits.

        H(p) = -p·log₂(p) - (1-p)·log₂(1-p)

    Parameters
    ----------
    price : float   Market-implied probability (0, 1).

    Returns
    -------
    float   Entropy in bits. Max = 1.0 at p = 0.5.
    """
    p = max(_EPS, min(1 - _EPS, price))
    q = 1 - p
    return -(p * math.log2(p) + q * math.log2(q))


def kl_divergence(model_p: float, market_p: float) -> float:
    """
    KL divergence D_KL(model ‖ market) for a binary outcome.

        D_KL = p·log(p/m) + (1-p)·log((1-p)/(1-m))

    Measures how much information your model has over the market.
    Units: bits (using log base 2).

    Parameters
    ----------
    model_p  : float   Your estimated probability (0, 1).
    market_p : float   Current market price (0, 1).

    Returns
    -------
    float   KL divergence in bits. Always ≥ 0.
    """
    p = max(_EPS, min(1 - _EPS, model_p))
    m = max(_EPS, min(1 - _EPS, market_p))
    q = 1 - p
    n = 1 - m
    return p * math.log2(p / m) + q * math.log2(q / n)


def kelly_fraction(model_p: float, market_price: float) -> float:
    """
    Full Kelly fraction for a binary bet.

        b = (1/market_price) - 1       # decimal odds
        f* = (p·b - q) / b

    Parameters
    ----------
    model_p      : float   Your estimated probability.
    market_price : float   Current market price (cost to buy YES).

    Returns
    -------
    float   Kelly fraction (can be negative → bet NO).
    """
    p = max(_EPS, min(1 - _EPS, model_p))
    m = max(_EPS, min(1 - _EPS, market_price))
    b = (1.0 / m) - 1.0  # decimal odds for YES
    q = 1.0 - p
    if b <= 0:
        return 0.0
    return (p * b - q) / b


def quarter_kelly(model_p: float, market_price: float) -> float:
    """Quarter-Kelly for conservative sizing."""
    return kelly_fraction(model_p, market_price) * 0.25


def entropy_efficiency(current_price: float, base_rate_price: float) -> float:
    """
    Entropy efficiency ratio R.

        R = H(current) / H(base_rate)

    R < 0.35 → market is "asleep", edge likely exists.
    R > 0.70 → market has resolved most uncertainty, edge gone.

    Parameters
    ----------
    current_price   : float   Current market price.
    base_rate_price : float   Prior / base rate price (e.g., 0.50).

    Returns
    -------
    float   R in [0, 1] (can exceed 1 if current entropy > base).
    """
    h_base = market_entropy(base_rate_price)
    if h_base < _EPS:
        return 1.0
    return market_entropy(current_price) / h_base


# ── scoring ─────────────────────────────────────────────────────────


class Action(str, Enum):
    BUY_YES = "BUY_YES"
    BUY_NO = "BUY_NO"
    HOLD = "HOLD"


@dataclass
class EntropyScoredMarket:
    """All entropy-based metrics for a single market."""

    market_id: str
    question: str
    market_price: float
    model_probability: float

    # derived
    entropy_bits: float
    kl_div_bits: float
    kelly_f: float
    quarter_kelly_f: float
    entropy_efficiency_r: float

    # sizing
    recommended_action: Action
    position_size_usdc: float
    edge_strength: str  # "strong" | "moderate" | "weak" | "none"

    def __str__(self) -> str:
        return (
            f"{self.question[:60]:<60s} "
            f"mkt={self.market_price:.3f}  "
            f"mdl={self.model_probability:.3f}  "
            f"H={self.entropy_bits:.3f}  "
            f"KL={self.kl_div_bits:.4f}  "
            f"f*={self.kelly_f:.3f}  "
            f"f/4={self.quarter_kelly_f:.3f}  "
            f"R={self.entropy_efficiency_r:.3f}  "
            f"${self.position_size_usdc:>7.2f}  "
            f"[{self.recommended_action.value:>7s}] "
            f"({self.edge_strength})"
        )


def score_market(
    market_id: str,
    question: str,
    market_price: float,
    model_probability: float,
    bankroll: float = 10_000.0,
    kelly_multiplier: float = 0.25,
    entropy_threshold: float = 0.08,
    efficiency_max: float = 0.35,
    base_rate: float = 0.50,
    max_trade_usdc: float = 150.0,
) -> EntropyScoredMarket:
    """
    Full scoring pipeline for one market.

    1. Compute H(market_price).
    2. Compute D_KL(model ‖ market).
    3. Compute Kelly f* and quarter-Kelly.
    4. Compute entropy efficiency R.
    5. Decide action + position size.
    """

    h = market_entropy(market_price)
    kl = kl_divergence(model_probability, market_price)
    f_full = kelly_fraction(model_probability, market_price)
    f_quarter = f_full * kelly_multiplier
    r = entropy_efficiency(market_price, base_rate)

    # ── action logic ──
    if kl < entropy_threshold or r > efficiency_max:
        action = Action.HOLD
        size = 0.0
        strength = "none" if kl < 0.02 else "weak"
    else:
        if model_probability > market_price:
            action = Action.BUY_YES
        else:
            action = Action.BUY_NO

        raw_size = abs(f_quarter) * bankroll
        size = min(raw_size, max_trade_usdc)

        if kl > 0.15:
            strength = "strong"
        elif kl > 0.08:
            strength = "moderate"
        else:
            strength = "weak"

    return EntropyScoredMarket(
        market_id=market_id,
        question=question,
        market_price=market_price,
        model_probability=model_probability,
        entropy_bits=h,
        kl_div_bits=kl,
        kelly_f=f_full,
        quarter_kelly_f=f_quarter,
        entropy_efficiency_r=r,
        recommended_action=action,
        position_size_usdc=size,
        edge_strength=strength,
    )
