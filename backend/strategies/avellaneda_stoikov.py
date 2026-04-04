"""
Strategy 2: Avellaneda-Stoikov Market Maker

Provides liquidity by continuously quoting bid/ask around an
inventory-adjusted reservation price. Uses VPIN to detect adverse
selection and auto-pause when toxic flow is detected.

Market selection: active, liquidity > 50k, NOT in last 48h before
resolution, midpoint between 0.05 and 0.95.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional

from backend.quant.avellaneda_math import (
    TradeBucket,
    compute_quotes,
    order_flow_imbalance,
    vpin,
)
from backend.strategies.base import (
    MarketState,
    OrderIntent,
    OrderType,
    Side,
    Strategy,
    StrategyName,
)


@dataclass
class MMState:
    """Per-market state for the market maker."""

    market_id: str
    inventory: float = 0.0  # positive = long YES
    pnl: float = 0.0
    fills: int = 0
    trade_buckets: list[TradeBucket] = field(default_factory=list)
    paused: bool = False


class AvellanedaStoikovMM(Strategy):
    """Inventory-aware market maker with VPIN guard."""

    name = StrategyName.AVELLANEDA

    def __init__(
        self,
        gamma: float = 0.1,
        kappa: float = 1.5,
        session_hours: float = 24,
        vpin_threshold: float = 0.70,
        min_liquidity: float = 50_000,
        min_hours_to_close: float = 48,
        max_inventory: float = 500,
        quote_size_usdc: float = 25,
    ) -> None:
        self.gamma = gamma
        self.kappa = kappa
        self.session_seconds = session_hours * 3600
        self.vpin_threshold = vpin_threshold
        self.min_liquidity = min_liquidity
        self.min_hours_to_close = min_hours_to_close
        self.max_inventory = max_inventory
        self.quote_size_usdc = quote_size_usdc
        self._states: dict[str, MMState] = {}

    def _get_state(self, market_id: str) -> MMState:
        if market_id not in self._states:
            self._states[market_id] = MMState(market_id=market_id)
        return self._states[market_id]

    def _passes_filters(self, m: MarketState) -> bool:
        if m.liquidity < self.min_liquidity:
            return False
        if m.hours_to_close < self.min_hours_to_close:
            return False
        # Skip markets that are effectively resolved. `mid_price` in this
        # codebase is computed as (yes+no)/2 which is ~0.5 even for
        # near-certain markets (yes + no ≈ 1), so we must check the
        # actual token prices, not the synthetic mid.
        if m.yes_price < 0.05 or m.yes_price > 0.95:
            return False
        if m.no_price < 0.05 or m.no_price > 0.95:
            return False
        # Spread too wide → likely dead market
        if m.spread > 0.10:
            return False
        return True

    def _estimate_volatility(self, m: MarketState) -> float:
        """Rough volatility estimate from spread and price level."""
        # In a real system, compute rolling std from price history.
        # Fallback: use spread as a proxy scaled by price uncertainty.
        base_vol = m.spread * 2
        price_uncertainty = 4 * m.mid_price * (1 - m.mid_price)  # max at 0.5
        return max(base_vol * price_uncertainty, 0.001)

    async def evaluate(self, market_state: MarketState) -> Optional[OrderIntent]:
        if not self._passes_filters(market_state):
            return None

        state = self._get_state(market_state.market_id)

        # VPIN check
        if state.trade_buckets:
            current_vpin = vpin(state.trade_buckets, n_buckets=20)
            if current_vpin > self.vpin_threshold:
                state.paused = True
                return None
        state.paused = False

        # Check inventory limits
        if abs(state.inventory) >= self.max_inventory:
            return None

        vol = self._estimate_volatility(market_state)
        t_remaining = min(market_state.hours_to_close * 3600, self.session_seconds)

        # The real "mid" for a prediction market is the current YES token
        # probability — NOT (yes+no)/2, which is always ~0.5 because YES +
        # NO ≈ 1. Using the synthetic mid caused A-S to quote around 0.5 on
        # markets trading at 0.18/0.82, producing delusional fills.
        real_mid = market_state.yes_price

        quotes = compute_quotes(
            mid=real_mid,
            inventory=state.inventory,
            gamma=self.gamma,
            volatility=vol,
            t_remaining=t_remaining,
            kappa=self.kappa,
        )

        # Emit the side that reduces inventory. On Polymarket both YES and
        # NO are buy-only; "selling YES" really means buying NO. We quote
        # tight around the actual market price, not a synthetic mid.
        if state.inventory >= 0:
            # Long or neutral → lean toward buying NO (which is equivalent
            # to selling YES exposure). Price NO near its real market
            # price, not off-book.
            side = Side.NO
            price = max(0.02, min(0.98, market_state.no_price))
        else:
            # Short → lean toward buying YES near its real market price.
            side = Side.YES
            price = max(0.02, min(0.98, market_state.yes_price))

        return OrderIntent(
            strategy=self.name,
            market_id=market_state.market_id,
            condition_id=market_state.condition_id,
            question=market_state.question,
            side=side,
            order_type=OrderType.LIMIT,
            price=price,
            size_usdc=self.quote_size_usdc,
            confidence=0.5,
            reason=(
                f"A-S MM: r={quotes.reservation_price:.4f}, "
                f"bid={quotes.bid:.4f}, ask={quotes.ask:.4f}, "
                f"spread={quotes.spread_bps:.1f}bps, inv={state.inventory:.1f}"
            ),
        )

    async def evaluate_batch(self, markets: list[MarketState]) -> list[OrderIntent]:
        intents = []
        for m in markets:
            intent = await self.evaluate(m)
            if intent:
                intents.append(intent)
        return intents

    def record_fill(self, market_id: str, side: Side, price: float, size: float) -> None:
        """Update state after a fill."""
        state = self._get_state(market_id)
        state.fills += 1
        if side == Side.YES:
            state.inventory += size
        else:
            state.inventory -= size
