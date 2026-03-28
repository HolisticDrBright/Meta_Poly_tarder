"""
Order executor — handles the CLOB order lifecycle.

Paper trading: simulates fills at the intent price.
Live trading: places real orders via the Polymarket CLOB API.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from backend.strategies.base import OrderIntent, Position, ScoredIntent, Side

logger = logging.getLogger(__name__)


@dataclass
class ExecutionResult:
    success: bool
    order_id: str = ""
    fill_price: float = 0.0
    fill_size: float = 0.0
    paper: bool = True
    error: str = ""
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class OrderExecutor:
    """Executes orders against the Polymarket CLOB."""

    def __init__(self, paper_trading: bool = True) -> None:
        self.paper_trading = paper_trading
        self._paper_fills: list[ExecutionResult] = []

    async def execute(self, scored: ScoredIntent) -> ExecutionResult:
        """Execute a single scored intent."""
        intent = scored.intent

        if not scored.approved:
            return ExecutionResult(
                success=False, error="Not approved by risk engine"
            )

        if self.paper_trading:
            return self._paper_fill(intent)
        else:
            return await self._live_fill(intent)

    def _paper_fill(self, intent: OrderIntent) -> ExecutionResult:
        """Simulate a fill for paper trading."""
        result = ExecutionResult(
            success=True,
            order_id=f"PAPER-{intent.market_id[:8]}-{datetime.now(timezone.utc).timestamp():.0f}",
            fill_price=intent.price,
            fill_size=intent.size_usdc,
            paper=True,
        )
        self._paper_fills.append(result)
        logger.info(
            f"PAPER FILL: {intent.strategy.value} {intent.side.value} "
            f"${intent.size_usdc:.2f} @ {intent.price:.4f} — {intent.question[:50]}"
        )
        return result

    async def _live_fill(self, intent: OrderIntent) -> ExecutionResult:
        """Place a real order on the Polymarket CLOB."""
        # This would integrate with py-clob-client or direct API calls.
        # For now, return a placeholder — real integration requires
        # private key signing and the full CLOB order flow.
        logger.warning("LIVE TRADING NOT YET IMPLEMENTED — falling back to paper")
        return self._paper_fill(intent)

    async def execute_batch(
        self, scored_intents: list[ScoredIntent]
    ) -> list[ExecutionResult]:
        results = []
        for si in scored_intents:
            result = await self.execute(si)
            results.append(result)
        return results

    def to_position(self, intent: OrderIntent, result: ExecutionResult) -> Optional[Position]:
        """Convert a fill to a tracked position."""
        if not result.success:
            return None
        return Position(
            market_id=intent.market_id,
            condition_id=intent.condition_id,
            question=intent.question,
            side=intent.side,
            entry_price=result.fill_price,
            size_usdc=result.fill_size,
            current_price=result.fill_price,
            strategy=intent.strategy,
        )

    @property
    def paper_fill_count(self) -> int:
        return len(self._paper_fills)
