"""
Strategy 3: YES+NO Arbitrage Scanner

Exploits mispricings where YES_price + NO_price < 1.0 in binary markets.
The edge is risk-free if both sides fill simultaneously.

Best markets: 15-min BTC/SOL/ETH Up-Down markets where arb windows
appear regularly due to oracle lag.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from backend.strategies.base import (
    MarketState,
    OrderIntent,
    OrderType,
    Side,
    Strategy,
    StrategyName,
)


@dataclass
class ArbitrageOpportunity:
    market_id: str
    question: str
    yes_price: float
    no_price: float
    edge: float
    size_usdc: float


class ArbScanner(Strategy):
    """Scan for YES+NO price gaps in binary markets."""

    name = StrategyName.ARB

    def __init__(
        self,
        min_arb_edge: float = 0.015,
        max_arb_size: float = 50,
        target_keywords: list[str] | None = None,
    ) -> None:
        self.min_arb_edge = min_arb_edge
        self.max_arb_size = max_arb_size
        self.target_keywords = target_keywords or ["BTC", "SOL", "ETH", "15min", "15-min"]

    def _is_target_market(self, question: str) -> bool:
        """Prefer crypto 15-min markets but accept any with arb."""
        q_lower = question.lower()
        return any(kw.lower() in q_lower for kw in self.target_keywords)

    async def evaluate(self, market_state: MarketState) -> Optional[OrderIntent]:
        edge = market_state.arb_edge

        if edge < self.min_arb_edge:
            return None

        # Size proportional to edge, capped
        size = min(self.max_arb_size, edge * 1000)
        if size < 1.0:
            return None

        # Arb is direction-neutral; buy YES side (we'd buy NO simultaneously)
        return OrderIntent(
            strategy=self.name,
            market_id=market_state.market_id,
            condition_id=market_state.condition_id,
            question=market_state.question,
            side=Side.YES,
            order_type=OrderType.FOK,
            price=market_state.yes_price,
            size_usdc=size,
            confidence=min(edge / 0.03, 1.0),
            reason=(
                f"ARB: YES={market_state.yes_price:.4f} + "
                f"NO={market_state.no_price:.4f} = "
                f"{market_state.yes_price + market_state.no_price:.4f}, "
                f"edge={edge:.4f} ({edge*100:.1f}¢)"
            ),
        )

    async def evaluate_batch(self, markets: list[MarketState]) -> list[OrderIntent]:
        intents = []
        for m in markets:
            intent = await self.evaluate(m)
            if intent:
                intents.append(intent)
        return sorted(intents, key=lambda x: x.confidence, reverse=True)

    async def scan_opportunities(
        self, markets: list[MarketState]
    ) -> list[ArbitrageOpportunity]:
        """Return raw arb opportunities without converting to OrderIntents."""
        opps = []
        for m in markets:
            edge = m.arb_edge
            if edge >= self.min_arb_edge:
                opps.append(
                    ArbitrageOpportunity(
                        market_id=m.market_id,
                        question=m.question,
                        yes_price=m.yes_price,
                        no_price=m.no_price,
                        edge=edge,
                        size_usdc=min(self.max_arb_size, edge * 1000),
                    )
                )
        return sorted(opps, key=lambda x: x.edge, reverse=True)
