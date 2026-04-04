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
from backend.quant.regime import classify as classify_regime, Regime
from backend.quant.sizing import (
    mm_ev_gate_passes,
    regime_allows_strategy,
)
import logging

logger = logging.getLogger(__name__)
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
        bankroll: float = 300.0,
        kelly_fraction_mult: float = 0.25,
        max_trade_usdc: float = 30.0,
    ) -> None:
        self.gamma = gamma
        self.kappa = kappa
        self.session_seconds = session_hours * 3600
        self.vpin_threshold = vpin_threshold
        self.min_liquidity = min_liquidity
        self.min_hours_to_close = min_hours_to_close
        self.max_inventory = max_inventory
        # Legacy flat size — retained only as a fallback when Kelly can't
        # size (no edge). New trades are Kelly-sized.
        self.quote_size_usdc = quote_size_usdc
        self.bankroll = bankroll
        self.kelly_fraction_mult = kelly_fraction_mult
        self.max_trade_usdc = max_trade_usdc
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

        # Regime gate: only run A-S in regimes where spread capture has edge.
        # Consensus-grind = A-S's natural habitat (range-bound, tight books).
        # Information-driven = skip (direction matters, not spread).
        # Resolution-cliff / illiquid-noise = skip.
        regime_call = classify_regime(market_state)
        if not regime_allows_strategy(regime_call.regime, self.name):
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

        # Side selection. On Polymarket YES and NO are separate tokens;
        # "selling YES" isn't possible, so we flip the inventory-skewed
        # quoting into "buy NO" when we want to reduce long exposure.
        if state.inventory >= 0:
            side = Side.NO
            market_price = max(0.02, min(0.98, market_state.no_price))
        else:
            side = Side.YES
            market_price = max(0.02, min(0.98, market_state.yes_price))

        # Market-maker EV gate: a MM's edge isn't fair-vs-market; it's
        # the spread it captures. The gate refuses trades only when the
        # captured spread can't cover fees + slippage + adverse selection.
        if not mm_ev_gate_passes(
            quoted_spread=market_state.spread,
            market_price=market_price,
        ):
            logger.debug(
                f"A-S EV gate rejected {market_state.market_id[:10]}: "
                f"spread={market_state.spread:.4f} too thin to profit after fees"
            )
            return None

        # Market making doesn't scale size with edge the way directional
        # strategies do — you quote a fixed clip per tick and let volume
        # fill you. We use a fraction of the per-trade cap, scaled down
        # by how much of your bankroll would be exposed if this market
        # filled all the way up to max_inventory.
        size_usdc = min(self.max_trade_usdc * 0.5, self.bankroll * 0.05)
        size_usdc = round(max(1.0, size_usdc), 2)

        captured_bps = (market_state.spread / 2.0) * 10000
        return OrderIntent(
            strategy=self.name,
            market_id=market_state.market_id,
            condition_id=market_state.condition_id,
            question=market_state.question,
            side=side,
            order_type=OrderType.LIMIT,
            price=market_price,
            size_usdc=size_usdc,
            confidence=min(0.95, market_state.spread / 0.05),
            reason=(
                f"A-S MM [{regime_call.regime.value}]: "
                f"mkt={market_price:.4f} spread={market_state.spread:.4f} "
                f"capture={captured_bps:.0f}bps size=${size_usdc:.2f} "
                f"r={quotes.reservation_price:.4f} inv={state.inventory:.1f}"
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
