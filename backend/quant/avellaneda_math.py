"""
Avellaneda-Stoikov market-making math.

References
----------
- Avellaneda, M. & Stoikov, S. (2008). High-frequency trading in a limit
  order book. Quantitative Finance, 8(3), 217-224.
- Easley, D. et al. (2012). Flow Toxicity and Liquidity in a High-Frequency
  World. Review of Financial Studies, 25(5). (VPIN)
"""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass
class ASQuotes:
    """Bid/ask quotes from the Avellaneda-Stoikov model."""

    reservation_price: float
    optimal_spread: float
    bid: float
    ask: float
    spread_bps: float


@dataclass
class TradeBucket:
    """Volume bucket for VPIN calculation."""

    buy_volume: float
    sell_volume: float

    @property
    def total(self) -> float:
        return self.buy_volume + self.sell_volume


def reservation_price(
    mid: float,
    inventory: float,
    gamma: float,
    volatility: float,
    t_remaining: float,
) -> float:
    """
    Inventory-adjusted fair value.

        r = S - q·γ·σ²·(T-t)

    Parameters
    ----------
    mid         : float   Current mid-price.
    inventory   : float   Net position (positive=long).
    gamma       : float   Risk aversion parameter.
    volatility  : float   Recent price volatility (std dev).
    t_remaining : float   Time remaining in session (seconds).
    """
    return mid - inventory * gamma * (volatility ** 2) * t_remaining


def optimal_spread(
    gamma: float,
    volatility: float,
    t_remaining: float,
    kappa: float,
) -> float:
    """
    Optimal total spread (bid to ask).

        δ = γ·σ²·(T-t) + (2/γ)·ln(1 + γ/κ)

    Parameters
    ----------
    gamma       : float   Risk aversion.
    volatility  : float   Price volatility.
    t_remaining : float   Time remaining (seconds).
    kappa       : float   Order book depth factor.
    """
    term1 = gamma * (volatility ** 2) * t_remaining
    term2 = (2.0 / gamma) * math.log(1.0 + gamma / kappa)
    return term1 + term2


def compute_quotes(
    mid: float,
    inventory: float,
    gamma: float,
    volatility: float,
    t_remaining: float,
    kappa: float,
) -> ASQuotes:
    """Compute full bid/ask quote set."""
    r = reservation_price(mid, inventory, gamma, volatility, t_remaining)
    delta = optimal_spread(gamma, volatility, t_remaining, kappa)
    bid = r - delta / 2
    ask = r + delta / 2
    spread_bps = (delta / mid * 10_000) if mid > 0 else 0.0
    return ASQuotes(
        reservation_price=r,
        optimal_spread=delta,
        bid=bid,
        ask=ask,
        spread_bps=spread_bps,
    )


def vpin(buckets: list[TradeBucket], n_buckets: int | None = None) -> float:
    """
    Volume-Synchronized Probability of Informed Trading.

        VPIN = Σ|buy_vol - sell_vol| / Σ total_vol

    Returns
    -------
    float   VPIN in [0, 1]. High (>0.7) = toxic flow.
    """
    if not buckets:
        return 0.0
    used = buckets[-n_buckets:] if n_buckets else buckets
    total_imbalance = sum(abs(b.buy_volume - b.sell_volume) for b in used)
    total_volume = sum(b.total for b in used)
    if total_volume == 0:
        return 0.0
    return total_imbalance / total_volume


def order_flow_imbalance(
    bid_qty_change: float, ask_qty_change: float
) -> float:
    """
    Order Flow Imbalance (OFI).

        OFI = (ΔBid - ΔAsk) / (ΔBid + ΔAsk)

    Returns
    -------
    float   OFI in [-1, 1]. Positive = buying pressure.
    """
    total = abs(bid_qty_change) + abs(ask_qty_change)
    if total == 0:
        return 0.0
    return (bid_qty_change - ask_qty_change) / total
