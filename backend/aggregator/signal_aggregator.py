"""
Signal Aggregator — fuses OrderIntents from all 7 strategies into
a single scored queue.

Priority ordering: ARB > COPY (manual) > ENTROPY high > JET compound >
ENSEMBLE confident > A-S MM > THETA

Confluence detection: when 2+ strategies agree on same market + direction,
boost confidence and sizing.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from backend.strategies.base import (
    OrderIntent,
    ScoredIntent,
    Side,
    StrategyName,
)


# Strategy weights for composite scoring
STRATEGY_WEIGHTS: dict[StrategyName, float] = {
    StrategyName.ENTROPY: 0.25,
    StrategyName.AVELLANEDA: 0.20,
    StrategyName.ARB: 0.15,
    StrategyName.ENSEMBLE_AI: 0.20,
    StrategyName.JET: 0.10,
    StrategyName.COPY: 0.05,
    StrategyName.THETA: 0.05,
}

# Priority order (lower = higher priority)
PRIORITY: dict[StrategyName, int] = {
    StrategyName.ARB: 1,
    StrategyName.COPY: 2,
    StrategyName.ENTROPY: 3,
    StrategyName.JET: 4,
    StrategyName.ENSEMBLE_AI: 5,
    StrategyName.AVELLANEDA: 6,
    StrategyName.THETA: 7,
}


@dataclass
class MarketSignalGroup:
    """All signals for a single market, grouped for confluence detection."""

    market_id: str
    question: str
    intents: list[OrderIntent] = field(default_factory=list)

    @property
    def yes_count(self) -> int:
        return sum(1 for i in self.intents if i.side == Side.YES)

    @property
    def no_count(self) -> int:
        return sum(1 for i in self.intents if i.side == Side.NO)

    @property
    def dominant_side(self) -> Side:
        return Side.YES if self.yes_count >= self.no_count else Side.NO

    @property
    def confluence(self) -> int:
        """Number of strategies agreeing on dominant side."""
        return max(self.yes_count, self.no_count)

    @property
    def strategies(self) -> list[StrategyName]:
        return [i.strategy for i in self.intents]


class SignalAggregator:
    """Fuse signals from all strategies into a scored queue."""

    def __init__(
        self,
        weights: dict[StrategyName, float] | None = None,
        min_confluence_for_boost: int = 2,
        confluence_boost: float = 1.5,
    ) -> None:
        self._explicit_weights = weights  # override — skips learned weights
        self.weights = weights or STRATEGY_WEIGHTS
        self.min_confluence = min_confluence_for_boost
        self.confluence_boost = confluence_boost

    def _load_learned_weights(self) -> dict[StrategyName, float]:
        """Pull the latest learned strategy weights from the learning loop.

        Reads data/active_weights.json (mtime-cached) and converts the
        string keys back to StrategyName enum values. Falls back to
        STRATEGY_WEIGHTS for any strategy the loop hasn't seen yet.

        Called at the start of every score() cycle so deploys from
        run_learning_pass take effect on the next aggregation.
        """
        try:
            from backend.learning.weights import get_strategy_weights
            learned = get_strategy_weights()  # string keys like "entropy", "theta"
            mapped: dict[StrategyName, float] = dict(STRATEGY_WEIGHTS)
            for k, v in learned.items():
                # Map the lowercase string back to the enum value
                for sn in StrategyName:
                    if sn.value == k:
                        mapped[sn] = float(v)
                        break
            return mapped
        except Exception:
            return STRATEGY_WEIGHTS

    def score(self, intents: list[OrderIntent]) -> list[ScoredIntent]:
        """
        Score and rank all intents.

        1. Refresh learned weights from the learning loop's output
        2. Group by market_id
        3. Detect confluence (multiple strategies same direction)
        4. Compute composite score per intent
        5. Return sorted by priority then score
        """
        # Refresh learned weights unless caller explicitly overrode them.
        # This is the hook that closes the learning loop on the aggregator
        # side — every aggregation cycle uses the latest deployed weights.
        if self._explicit_weights is None:
            self.weights = self._load_learned_weights()
        # Group by market
        groups: dict[str, MarketSignalGroup] = defaultdict(
            lambda: MarketSignalGroup(market_id="", question="")
        )
        for intent in intents:
            if intent.market_id not in groups:
                groups[intent.market_id] = MarketSignalGroup(
                    market_id=intent.market_id, question=intent.question
                )
            groups[intent.market_id].intents.append(intent)

        scored = []
        for market_id, group in groups.items():
            confluence = group.confluence
            confluence_strategies = [
                i.strategy
                for i in group.intents
                if i.side == group.dominant_side
            ]

            for intent in group.intents:
                weight = self.weights.get(intent.strategy, 0.05)
                base_score = intent.confidence * weight

                # Confluence boost
                if confluence >= self.min_confluence and intent.side == group.dominant_side:
                    base_score *= self.confluence_boost

                # KL divergence bonus
                if intent.kl_divergence > 0:
                    base_score += intent.kl_divergence * 0.5

                # Update intent confluence count
                intent.confluence_count = confluence

                scored.append(
                    ScoredIntent(
                        intent=intent,
                        composite_score=base_score,
                        confluence_strategies=confluence_strategies,
                    )
                )

        # Sort: primary = priority, secondary = composite score descending
        scored.sort(
            key=lambda s: (
                PRIORITY.get(s.intent.strategy, 99),
                -s.composite_score,
            )
        )
        return scored

    def top_signals(
        self, intents: list[OrderIntent], n: int = 10
    ) -> list[ScoredIntent]:
        """Return top N scored signals."""
        return self.score(intents)[:n]
