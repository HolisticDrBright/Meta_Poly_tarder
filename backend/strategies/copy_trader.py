"""
Strategy 6: Copy Trading Engine

Follows top wallets (@RN1 and leaderboard) with a confluence filter.

Auto-execute requires AT LEAST ONE confluence signal:
  - Entropy screener also flags this market (KL > 0.05)
  - AI ensemble agrees with direction (>60%)
  - Another whale from leaderboard also entered same side
  - Jet signal active for this market

Without confluence → queue for MANUAL_CONFIRM.
With 2+ confluence → auto-execute at full COPY_RATIO.
With 3+ confluence → auto-execute at 1.5x COPY_RATIO.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from backend.strategies.base import (
    MarketState,
    OrderIntent,
    OrderType,
    Side,
    Strategy,
    StrategyName,
)

logger = logging.getLogger(__name__)


@dataclass
class CopyTarget:
    """A wallet being tracked for copy trading."""

    address: str
    display_name: str
    auto_copy: bool = False
    copy_ratio: float = 0.10
    win_rate: float = 0.0
    total_pnl: float = 0.0
    trades_followed: int = 0


@dataclass
class CopyTradeEvent:
    """A trade detected from a copy target."""

    target: CopyTarget
    market_id: str
    question: str
    side: Side
    size_usdc: float
    price: float
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    # confluence signals (filled by the system)
    entropy_signal: bool = False
    ensemble_agrees: bool = False
    other_whale_agrees: bool = False
    jet_signal_active: bool = False

    @property
    def confluence_count(self) -> int:
        return sum(
            [
                self.entropy_signal,
                self.ensemble_agrees,
                self.other_whale_agrees,
                self.jet_signal_active,
            ]
        )


class CopyTrader(Strategy):
    """Copy trading with confluence filter."""

    name = StrategyName.COPY

    def __init__(
        self,
        targets: list[CopyTarget] | None = None,
        default_ratio: float = 0.10,
        max_size_usdc: float = 75,
        confluence_required: bool = False,
    ) -> None:
        self.targets = targets or []
        self.default_ratio = default_ratio
        self.max_size_usdc = max_size_usdc
        self.confluence_required = confluence_required
        self._pending_events: list[CopyTradeEvent] = []
        self._manual_queue: list[CopyTradeEvent] = []

    def add_target(self, target: CopyTarget) -> None:
        self.targets.append(target)

    def queue_event(self, event: CopyTradeEvent) -> None:
        """Queue a detected trade for processing."""
        self._pending_events.append(event)

    def _size_for_event(self, event: CopyTradeEvent) -> float:
        """Calculate position size based on confluence."""
        base = event.target.copy_ratio * event.size_usdc
        if event.confluence_count >= 3:
            base *= 1.5
        return min(base, self.max_size_usdc)

    def _should_auto_execute(self, event: CopyTradeEvent) -> bool:
        """Determine if this trade can auto-execute."""
        if not event.target.auto_copy:
            return False
        if self.confluence_required and event.confluence_count == 0:
            return False
        return event.confluence_count >= 1

    async def evaluate(self, market_state: MarketState) -> Optional[OrderIntent]:
        """Check pending copy events for this market."""
        for event in list(self._pending_events):
            if event.market_id != market_state.market_id:
                continue

            size = self._size_for_event(event)
            if size < 1.0:
                continue

            if not self._should_auto_execute(event):
                self._manual_queue.append(event)
                logger.info(
                    f"COPY queued for manual confirm: "
                    f"{event.target.display_name} → {event.question}"
                )
                continue

            self._pending_events.remove(event)
            return OrderIntent(
                strategy=self.name,
                market_id=market_state.market_id,
                condition_id=market_state.condition_id,
                question=market_state.question,
                side=event.side,
                order_type=OrderType.LIMIT,
                price=event.price,
                size_usdc=size,
                confidence=min(event.confluence_count / 4, 1.0),
                reason=(
                    f"COPY {event.target.display_name}: "
                    f"{event.side.value} ${event.size_usdc:.0f} "
                    f"(confluence={event.confluence_count})"
                ),
                confluence_count=event.confluence_count,
            )
        return None

    async def evaluate_batch(self, markets: list[MarketState]) -> list[OrderIntent]:
        intents = []
        for m in markets:
            intent = await self.evaluate(m)
            if intent:
                intents.append(intent)
        return intents

    @property
    def manual_queue(self) -> list[CopyTradeEvent]:
        return self._manual_queue

    def confirm_manual(self, event: CopyTradeEvent) -> OrderIntent:
        """Manually confirm a queued copy trade."""
        self._manual_queue.remove(event)
        size = self._size_for_event(event)
        return OrderIntent(
            strategy=self.name,
            market_id=event.market_id,
            condition_id="",
            question=event.question,
            side=event.side,
            order_type=OrderType.LIMIT,
            price=event.price,
            size_usdc=size,
            confidence=0.5,
            reason=f"COPY (manual confirm): {event.target.display_name}",
        )
