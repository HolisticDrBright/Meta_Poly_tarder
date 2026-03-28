"""
Strategy 1: Shannon Entropy Screener

Identifies markets where your model probability diverges significantly
from the market price, as measured by KL divergence. Uses Kelly criterion
for position sizing.

Sweet spot: markets priced between 0.20 and 0.45 (high uncertainty,
crowd not doing homework).
"""

from __future__ import annotations

from typing import Optional

from backend.quant.entropy import (
    Action,
    entropy_efficiency,
    kl_divergence,
    market_entropy,
    quarter_kelly,
    score_market,
)
from backend.strategies.base import (
    MarketState,
    OrderIntent,
    OrderType,
    Side,
    Strategy,
    StrategyName,
)


class EntropyScreener(Strategy):
    """Scan markets for information-theoretic edges."""

    name = StrategyName.ENTROPY

    def __init__(
        self,
        entropy_threshold: float = 0.08,
        efficiency_max: float = 0.35,
        kelly_fraction: float = 0.25,
        min_liquidity: float = 25_000,
        max_days_to_close: float = 30,
        min_days_to_close: float = 1,
        bankroll: float = 10_000,
        max_trade_usdc: float = 150,
    ) -> None:
        self.entropy_threshold = entropy_threshold
        self.efficiency_max = efficiency_max
        self.kelly_fraction = kelly_fraction
        self.min_liquidity = min_liquidity
        self.max_days_to_close = max_days_to_close
        self.min_days_to_close = min_days_to_close
        self.bankroll = bankroll
        self.max_trade_usdc = max_trade_usdc

    def _passes_filters(self, m: MarketState) -> bool:
        if m.liquidity < self.min_liquidity:
            return False
        hours = m.hours_to_close
        if hours < self.min_days_to_close * 24:
            return False
        if hours > self.max_days_to_close * 24:
            return False
        # Sweet spot filter: market between 0.05 and 0.95
        if m.yes_price < 0.05 or m.yes_price > 0.95:
            return False
        return True

    async def evaluate(self, market_state: MarketState) -> Optional[OrderIntent]:
        if not self._passes_filters(market_state):
            return None

        mp = market_state.yes_price
        model_p = market_state.model_probability

        if model_p <= 0 or model_p >= 1:
            return None

        kl = kl_divergence(model_p, mp)
        r = entropy_efficiency(mp, 0.50)

        if kl < self.entropy_threshold:
            return None
        if r > self.efficiency_max:
            return None

        f_quarter = quarter_kelly(model_p, mp)
        size = min(abs(f_quarter) * self.bankroll, self.max_trade_usdc)

        if size < 1.0:
            return None

        if model_p > mp:
            side = Side.YES
            price = mp
        else:
            side = Side.NO
            price = market_state.no_price

        h = market_entropy(mp)

        return OrderIntent(
            strategy=self.name,
            market_id=market_state.market_id,
            condition_id=market_state.condition_id,
            question=market_state.question,
            side=side,
            order_type=OrderType.LIMIT,
            price=price,
            size_usdc=size,
            confidence=min(kl / 0.20, 1.0),
            reason=(
                f"Entropy edge: KL={kl:.4f} bits, H={h:.3f}, R={r:.3f}, "
                f"model={model_p:.3f} vs market={mp:.3f}"
            ),
            kl_divergence=kl,
            kelly_fraction=f_quarter,
        )

    async def evaluate_batch(self, markets: list[MarketState]) -> list[OrderIntent]:
        intents = []
        for m in markets:
            intent = await self.evaluate(m)
            if intent:
                intents.append(intent)
        return sorted(intents, key=lambda x: x.kl_divergence, reverse=True)
