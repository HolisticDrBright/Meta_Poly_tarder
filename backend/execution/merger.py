"""
CTF (Conditional Token Framework) position merger.

When you hold both YES and NO tokens in the same market, they can
be merged to redeem the underlying USDC. This is important for:
  1. Closing arb positions (bought both sides)
  2. Exiting partial hedges
  3. Recovering capital from offsetting positions
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from backend.strategies.base import Position, Side

logger = logging.getLogger(__name__)


@dataclass
class MergeOpportunity:
    """A pair of YES+NO positions that can be merged."""

    market_id: str
    question: str
    yes_position: Position
    no_position: Position
    mergeable_usdc: float  # min of yes/no size

    @property
    def full_merge(self) -> bool:
        """True if both sides have equal size."""
        return abs(self.yes_position.size_usdc - self.no_position.size_usdc) < 0.01


class CTFMerger:
    """Detect and execute CTF merges."""

    def scan_positions(self, positions: list[Position]) -> list[MergeOpportunity]:
        """Find positions where YES+NO overlap in the same market."""
        by_market: dict[str, dict[str, list[Position]]] = {}
        for p in positions:
            if p.market_id not in by_market:
                by_market[p.market_id] = {"YES": [], "NO": []}
            by_market[p.market_id][p.side.value].append(p)

        opportunities = []
        for market_id, sides in by_market.items():
            if sides["YES"] and sides["NO"]:
                yes_total = sum(p.size_usdc for p in sides["YES"])
                no_total = sum(p.size_usdc for p in sides["NO"])
                mergeable = min(yes_total, no_total)
                if mergeable > 0.01:
                    opportunities.append(
                        MergeOpportunity(
                            market_id=market_id,
                            question=sides["YES"][0].question,
                            yes_position=sides["YES"][0],
                            no_position=sides["NO"][0],
                            mergeable_usdc=mergeable,
                        )
                    )
        return opportunities

    async def execute_merge(self, opp: MergeOpportunity) -> bool:
        """Execute a CTF merge (paper mode just logs)."""
        logger.info(
            f"CTF MERGE: {opp.question[:50]} — "
            f"merging ${opp.mergeable_usdc:.2f} YES+NO → USDC"
        )
        # In live mode, this would call the CTF contract's merge function
        return True
