"""
Strategy base classes and shared data models.

Every strategy emits OrderIntent objects. The SignalAggregator
fuses them into a scored queue before they reach the risk engine.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Optional


# ── enums ───────────────────────────────────────────────────────────


class Side(str, Enum):
    YES = "YES"
    NO = "NO"


class OrderType(str, Enum):
    LIMIT = "LIMIT"
    MARKET = "MARKET"
    FOK = "FOK"  # fill-or-kill (arb scanner)


class StrategyName(str, Enum):
    ENTROPY = "entropy"
    AVELLANEDA = "avellaneda"
    ARB = "arb"
    BINANCE_ARB = "binance_arb"
    ENSEMBLE_AI = "ensemble_ai"
    JET = "jet"
    COPY = "copy"
    THETA = "theta"


# ── data models ─────────────────────────────────────────────────────


@dataclass
class MarketState:
    """Snapshot of a market at a point in time."""

    market_id: str
    condition_id: str
    question: str
    category: str

    # prices
    yes_price: float
    no_price: float
    mid_price: float
    spread: float

    # book
    best_bid: float
    best_ask: float
    bid_depth: float
    ask_depth: float

    # metadata
    liquidity: float
    volume_24h: float
    end_date: Optional[datetime] = None
    active: bool = True

    # derived (filled by quant modules)
    entropy_bits: float = 0.0
    model_probability: float = 0.0
    kl_divergence: float = 0.0

    @property
    def hours_to_close(self) -> float:
        if self.end_date is None:
            return float("inf")
        delta = self.end_date - datetime.now(timezone.utc)
        return max(0.0, delta.total_seconds() / 3600)

    @property
    def arb_edge(self) -> float:
        """YES + NO should sum to 1.0; positive = free money."""
        return 1.0 - self.yes_price - self.no_price


@dataclass
class OrderIntent:
    """A proposed trade from a strategy, before risk checks."""

    strategy: StrategyName
    market_id: str
    condition_id: str
    question: str
    side: Side
    order_type: OrderType
    price: float
    size_usdc: float
    confidence: float  # 0-1
    reason: str
    timestamp: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )

    # for aggregator scoring
    kl_divergence: float = 0.0
    kelly_fraction: float = 0.0
    confluence_count: int = 0  # how many strategies agree


@dataclass
class ScoredIntent:
    """An OrderIntent scored and ranked by the aggregator."""

    intent: OrderIntent
    composite_score: float  # weighted fusion score
    confluence_strategies: list[StrategyName] = field(default_factory=list)
    approved: bool = False  # set by risk engine


@dataclass
class Position:
    """An open position in a market."""

    market_id: str
    condition_id: str
    question: str
    side: Side
    entry_price: float
    size_usdc: float
    current_price: float
    strategy: StrategyName
    opened_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    # Link back to the prediction_intelligence decision record so that
    # when the position closes we can log the outcome and grade the
    # original bet (Brier score, pnl, hit-rate, etc.).
    decision_id: str = ""

    @property
    def pnl(self) -> float:
        """
        Realized+unrealized P&L in USDC.

        Polymarket YES and NO are two distinct ERC-1155 outcome tokens.
        For BOTH sides the rule is the same: you paid `entry_price` per
        share, you now hold shares worth `current_price` each, and
        `current_price` on a Position is always the price of the token
        you actually own (set in scheduler.py from market.yes_price or
        market.no_price). So the P&L is just mark-to-market:

            shares        = size_usdc / entry_price
            current_value = shares * current_price
            pnl           = current_value - size_usdc
                          = size_usdc * (current_price / entry_price - 1)

        The old formula treated NO as a short on YES, which is wrong —
        buying NO means you WANT NO to go up, not down.
        """
        if self.entry_price <= 0:
            return 0.0
        return self.size_usdc * (self.current_price / self.entry_price - 1.0)

    @property
    def pnl_pct(self) -> float:
        if self.entry_price <= 0:
            return 0.0
        return (self.current_price / self.entry_price - 1.0) * 100


# ── strategy ABC ────────────────────────────────────────────────────


class Strategy(ABC):
    """Base class for all trading strategies."""

    name: StrategyName

    @abstractmethod
    async def evaluate(
        self, market_state: MarketState
    ) -> Optional[OrderIntent]:
        """
        Evaluate a single market and optionally emit an OrderIntent.

        Returns None if no trade signal.
        """
        ...

    @abstractmethod
    async def evaluate_batch(
        self, markets: list[MarketState]
    ) -> list[OrderIntent]:
        """Evaluate multiple markets and return all signals."""
        ...
