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
        # Polymarket-realistic defaults. The legacy $50k / 48h values
        # were inherited from CEX market-making and rejected virtually
        # every Polymarket market — the universe of markets with $50k+
        # liquidity and 48h+ to close is tiny. Tuned for Polymarket.
        min_liquidity: float = 2_000,
        min_hours_to_close: float = 12,
        max_inventory: float = 500,
        quote_size_usdc: float = 15,
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
        """Rough volatility estimate from spread and price level.

        Uses yes_price directly rather than mid_price, because mid_price
        on Polymarket is (yes + no) / 2 which is always ~0.5 — so the
        old `4 * mid * (1 - mid)` factor was always ~1.0, making the
        whole scaling term dead code. yes_price is the real current
        probability, and binary entropy `p * (1 - p)` peaks at 0.5 as
        intended.
        """
        base_vol = m.spread * 2
        p = max(0.01, min(0.99, m.yes_price))
        price_uncertainty = 4 * p * (1 - p)
        return max(base_vol * price_uncertainty, 0.001)

    async def evaluate(self, market_state: MarketState) -> Optional[OrderIntent]:
        """Single-market evaluate path. Kept for external callers; the
        scheduler uses evaluate_batch() which shares the same gates +
        _build_intent helper without double-filtering."""
        if not self._passes_filters(market_state):
            return None
        regime_call = classify_regime(market_state)
        if not regime_allows_strategy(regime_call.regime, self.name):
            return None
        state = self._get_state(market_state.market_id)
        if state.trade_buckets and vpin(state.trade_buckets, n_buckets=20) > self.vpin_threshold:
            state.paused = True
            return None
        state.paused = False
        if abs(state.inventory) >= self.max_inventory:
            return None
        if state.inventory >= 0:
            side = Side.NO
            market_price = max(0.02, min(0.98, market_state.no_price))
        else:
            side = Side.YES
            market_price = max(0.02, min(0.98, market_state.yes_price))
        if not mm_ev_gate_passes(quoted_spread=market_state.spread, market_price=market_price):
            return None
        return self._build_intent(market_state, state, regime_call, side, market_price)

    def _build_intent(
        self,
        m: MarketState,
        state: MMState,
        regime_call,
        side: Side,
        market_price: float,
    ) -> OrderIntent:
        """Construct the OrderIntent after all gates have already passed.
        Split out of evaluate() so evaluate_batch() can share it without
        redundantly re-running the filter + regime + VPIN + EV checks.
        """
        vol = self._estimate_volatility(m)
        t_remaining = min(m.hours_to_close * 3600, self.session_seconds)
        quotes = compute_quotes(
            mid=m.yes_price,
            inventory=state.inventory,
            gamma=self.gamma,
            volatility=vol,
            t_remaining=t_remaining,
            kappa=self.kappa,
        )
        # Use the A-S computed quote prices instead of raw market prices.
        # Clamp to [0.01, 0.99] — large t_remaining can push bid/ask
        # outside the valid probability range.
        if side == Side.YES:
            limit_price = max(0.01, min(0.99, quotes.bid))
        else:
            limit_price = max(0.01, min(0.99, quotes.ask))
        # Kelly-based sizing from edge between reservation price and market price
        edge = abs(quotes.reservation_price - market_price)
        from backend.quant.entropy import quarter_kelly
        if edge > 0 and market_price > 0:
            fair_p = max(0.01, min(0.99, quotes.reservation_price))
            kelly_f = quarter_kelly(fair_p, market_price)
            size_usdc = round(max(1.0, min(self.max_trade_usdc, abs(kelly_f) * self.bankroll)), 2)
        else:
            size_usdc = round(max(1.0, min(self.max_trade_usdc * 0.5, self.quote_size_usdc)), 2)
        captured_bps = (m.spread / 2.0) * 10000
        return OrderIntent(
            strategy=self.name,
            market_id=m.market_id,
            condition_id=m.condition_id,
            question=m.question,
            side=side,
            order_type=OrderType.LIMIT,
            price=limit_price,
            size_usdc=size_usdc,
            confidence=min(0.95, m.spread / 0.05),
            reason=(
                f"A-S MM [{regime_call.regime.value}]: "
                f"mkt={market_price:.4f} spread={m.spread:.4f} "
                f"capture={captured_bps:.0f}bps size=${size_usdc:.2f} "
                f"r={quotes.reservation_price:.4f} inv={state.inventory:.1f}"
            ),
        )

    async def evaluate_batch(self, markets: list[MarketState]) -> list[OrderIntent]:
        # Per-cycle rejection counters so we can see at INFO level why
        # no trades are firing without having to enable DEBUG.
        rej_filter = 0
        rej_regime = 0
        rej_vpin = 0
        rej_inv = 0
        rej_ev = 0
        intents: list[OrderIntent] = []

        for m in markets:
            if not self._passes_filters(m):
                rej_filter += 1
                continue
            regime_call = classify_regime(m)
            if not regime_allows_strategy(regime_call.regime, self.name):
                rej_regime += 1
                continue
            state = self._get_state(m.market_id)
            if state.trade_buckets and vpin(state.trade_buckets, n_buckets=20) > self.vpin_threshold:
                state.paused = True
                rej_vpin += 1
                continue
            state.paused = False
            if abs(state.inventory) >= self.max_inventory:
                rej_inv += 1
                continue
            # Side + market price selection
            if state.inventory >= 0:
                side = Side.NO
                market_price = max(0.02, min(0.98, m.no_price))
            else:
                side = Side.YES
                market_price = max(0.02, min(0.98, m.yes_price))
            # Market-maker EV gate
            if not mm_ev_gate_passes(quoted_spread=m.spread, market_price=market_price):
                rej_ev += 1
                continue
            # All gates passed — build the intent directly
            intents.append(self._build_intent(m, state, regime_call, side, market_price))

        logger.info(
            f"A-S cycle: {len(markets)} markets → {len(intents)} intents "
            f"(rej: filter={rej_filter} regime={rej_regime} vpin={rej_vpin} "
            f"inv={rej_inv} ev={rej_ev})"
        )
        return intents

    def record_fill(self, market_id: str, side: Side, price: float, size: float) -> None:
        """Update state after a fill.

        Called by the scheduler after a successful paper or live fill.
        Updates inventory used for reservation-price skew. Does NOT
        populate trade_buckets from our own fills — VPIN measures
        two-sided market flow (informed vs uninformed traders hitting
        the book), and our own one-sided fills would produce a constant
        VPIN = 1.0, which would trip the 0.70 adverse-selection guard
        and permanently pause A-S after any fill. The VPIN guard
        remains wired for future integration with a real CLOB trade
        tape, but is inert until that data source is connected.
        """
        state = self._get_state(market_id)
        state.fills += 1
        if side == Side.YES:
            state.inventory += size
        else:
            state.inventory -= size

    def record_close(self, market_id: str, side: Side, size: float) -> None:
        """Reverse the inventory delta when a position is closed."""
        state = self._get_state(market_id)
        if side == Side.YES:
            state.inventory -= size
        else:
            state.inventory += size
