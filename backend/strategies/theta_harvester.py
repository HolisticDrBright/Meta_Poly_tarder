"""
Strategy 7: Time Decay (Theta) Harvester

Harvests time decay in prediction markets as they approach resolution.
If a market at 15¢ is going to resolve NO, it should drift toward 0¢
over time even without new information.

Urgency classification:
  T > 168h (7 days):  "patient"   — wait for better entry
  24-168h:            "normal"    — standard sizing
  6-24h:              "urgent"    — theta accelerating
  < 6h:               "critical"  — max theta, highest confidence needed
"""

from __future__ import annotations

import math
from typing import Optional

from backend.quant.regime import classify as classify_regime
from backend.quant.sizing import (
    ev_gate_passes,
    kelly_size_usdc,
    regime_allows_strategy,
)
from backend.strategies.base import (
    MarketState,
    OrderIntent,
    OrderType,
    Side,
    Strategy,
    StrategyName,
)


URGENCY_SIZE_MULTIPLIER = {
    "patient": 0.5,
    "normal": 1.0,
    "urgent": 1.5,
    "critical": 0.75,  # reduce size late due to lower liquidity
}


def classify_urgency(hours: float) -> str:
    if hours > 168:
        return "patient"
    elif hours > 24:
        return "normal"
    elif hours > 6:
        return "urgent"
    else:
        return "critical"


def compute_theta(fair_price: float, current_price: float, hours_remaining: float) -> float:
    """
    Time decay rate for a prediction market.

        theta = (P_fair - P_current) / sqrt(T_remaining_hours)

    The closer to expiry, the faster the decay.
    """
    if hours_remaining <= 0:
        return 0.0
    return (fair_price - current_price) / math.sqrt(hours_remaining)


class ThetaHarvester(Strategy):
    """Harvest time decay in prediction markets near resolution."""

    name = StrategyName.THETA

    def __init__(
        self,
        min_theta_edge: float = 0.05,
        max_resolution_hours: float = 72,
        base_size_usdc: float = 25,
        max_size_usdc: float = 100,
        min_confidence: float = 0.7,
        bankroll: float = 300.0,
        kelly_fraction_mult: float = 0.25,
    ) -> None:
        self.min_theta_edge = min_theta_edge
        self.max_resolution_hours = max_resolution_hours
        self.base_size_usdc = base_size_usdc
        self.max_size_usdc = max_size_usdc
        self.min_confidence = min_confidence
        self.bankroll = bankroll
        self.kelly_fraction_mult = kelly_fraction_mult

    async def evaluate(self, market_state: MarketState) -> Optional[OrderIntent]:
        hours = market_state.hours_to_close

        if hours > self.max_resolution_hours:
            return None
        if hours <= 0:
            return None

        # Regime gate — theta harvester is designed for resolution-cliff markets
        regime_call = classify_regime(market_state)
        if not regime_allows_strategy(regime_call.regime, self.name):
            return None

        mp = market_state.yes_price

        # Determine expected resolution direction
        # Markets near 0 or 1 are candidates for theta harvesting
        if mp < 0.20:
            # Likely resolves NO → already priced near 0
            fair_price = 0.0
            side = Side.NO
            price = market_state.no_price
            edge = mp  # distance from 0
            # Our fair value for the NO token itself
            fair_for_side = 1.0 - fair_price  # = 1.0
        elif mp > 0.80:
            # Likely resolves YES → already priced near 1
            fair_price = 1.0
            side = Side.YES
            price = mp
            edge = 1.0 - mp  # distance from 1
            fair_for_side = fair_price  # = 1.0
        else:
            # Mid-range: no clear theta edge
            return None

        if edge < self.min_theta_edge:
            return None

        # EV gate on the theta trade direction
        if not ev_gate_passes(
            fair_probability=fair_for_side,
            market_price=price,
            spread=market_state.spread,
        ):
            return None

        theta = compute_theta(fair_price, mp, hours)
        urgency = classify_urgency(hours)
        multiplier = URGENCY_SIZE_MULTIPLIER[urgency]

        # Kelly size scaled by urgency multiplier
        kelly_size = kelly_size_usdc(
            fair_probability=fair_for_side,
            market_price=price,
            bankroll=self.bankroll,
            kelly_fraction_multiplier=self.kelly_fraction_mult,
            max_trade_usdc=self.max_size_usdc,
        )
        size = min(kelly_size * multiplier, self.max_size_usdc)
        if size < 1.0:
            return None

        confidence = min(edge / 0.20 + (1.0 - hours / self.max_resolution_hours), 1.0)
        if confidence < self.min_confidence:
            return None

        return OrderIntent(
            strategy=self.name,
            market_id=market_state.market_id,
            condition_id=market_state.condition_id,
            question=market_state.question,
            side=side,
            order_type=OrderType.LIMIT,
            price=price,
            size_usdc=size,
            confidence=confidence,
            reason=(
                f"THETA: edge={edge:.3f}, theta={theta:.4f}/√h, "
                f"urgency={urgency}, hours_left={hours:.1f}"
            ),
        )

    async def evaluate_batch(self, markets: list[MarketState]) -> list[OrderIntent]:
        intents = []
        for m in markets:
            intent = await self.evaluate(m)
            if intent:
                intents.append(intent)
        return sorted(intents, key=lambda x: x.confidence, reverse=True)
