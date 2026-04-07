"""
Strategy: Cross-Market Correlation Arbitrage Scanner

Detects logically correlated Polymarket markets and flags arbitrage
opportunities where probability relationships are violated:

  1. Parent/child: longer-timeframe market must be >= shorter-timeframe.
  2. Mutually exclusive: candidate markets for the same event should sum to ~1.0.
  3. Complementary: YES + NO within a single market should sum to ~1.0.
"""

from __future__ import annotations

import logging
import re
from collections import defaultdict
from dataclasses import dataclass
from itertools import combinations
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

# ── helpers ────────────────────────────────────────────────────────

# Common date patterns: "by April 30", "by June 2026", "before May 1"
_DATE_PATTERN = re.compile(
    r"\b(?:by|before|until)\s+"
    r"(January|February|March|April|May|June|July|August|September|"
    r"October|November|December)\s+(\d{1,2})(?:[,\s]+(\d{4}))?\b",
    re.IGNORECASE,
)

_MONTH_ORDER = {
    "january": 1, "february": 2, "march": 3, "april": 4,
    "may": 5, "june": 6, "july": 7, "august": 8,
    "september": 9, "october": 10, "november": 11, "december": 12,
}

# Words to ignore when computing keyword overlap
_STOPWORDS = frozenset({
    "will", "the", "a", "an", "in", "on", "by", "of", "to", "for",
    "be", "is", "it", "at", "or", "and", "if", "as", "do", "does",
    "this", "that", "from", "with", "before", "after", "yes", "no",
    "what", "who", "which", "when", "where", "how", "?", "market",
})


def _keywords(question: str) -> set[str]:
    """Extract meaningful lowercase keywords from a market question."""
    tokens = re.findall(r"[A-Za-z0-9]+", question.lower())
    return {t for t in tokens if t not in _STOPWORDS and len(t) > 1}


def _extract_date_ordinal(question: str) -> Optional[int]:
    """Return a rough ordinal (YYYYMMDD int) from a deadline in the question."""
    m = _DATE_PATTERN.search(question)
    if not m:
        return None
    month = _MONTH_ORDER.get(m.group(1).lower(), 0)
    day = int(m.group(2))
    year = int(m.group(3)) if m.group(3) else 2026  # sensible default
    return year * 10000 + month * 100 + day


def _keyword_similarity(a: set[str], b: set[str]) -> float:
    """Jaccard similarity between two keyword sets."""
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


# ── correlation types ──────────────────────────────────────────────


@dataclass
class _Violation:
    kind: str          # "parent_child" | "mutually_exclusive" | "complementary"
    market: MarketState
    side: Side
    price: float
    edge: float
    reason: str


# ── scanner ────────────────────────────────────────────────────────


class MarketCorrelationScanner(Strategy):
    """Scan for cross-market correlation arbitrage."""

    name = StrategyName.CORRELATION_ARB

    def __init__(
        self,
        min_edge: float = 0.02,
        max_size: float = 40.0,
        keyword_sim_threshold: float = 0.50,
        complement_tolerance: float = 0.03,
        exclusive_sum_upper: float = 1.05,
        exclusive_sum_lower: float = 0.85,
    ) -> None:
        self.min_edge = min_edge
        self.max_size = max_size
        self.keyword_sim_threshold = keyword_sim_threshold
        self.complement_tolerance = complement_tolerance
        self.exclusive_sum_upper = exclusive_sum_upper
        self.exclusive_sum_lower = exclusive_sum_lower

    # ── public API ─────────────────────────────────────────────────

    def scan(self, markets: list[MarketState]) -> list[OrderIntent]:
        """Synchronous entry-point: detect violations and return OrderIntents."""
        violations: list[_Violation] = []
        try:
            violations.extend(self._check_complementary(markets))
            violations.extend(self._check_parent_child(markets))
            violations.extend(self._check_mutually_exclusive(markets))
        except Exception:
            logger.exception("correlation_scanner: unexpected error during scan")
            return []

        intents = []
        for v in violations:
            if v.edge < self.min_edge:
                continue
            size = min(self.max_size, v.edge * 800)
            if size < 1.0:
                continue
            intents.append(
                OrderIntent(
                    strategy=self.name,
                    market_id=v.market.market_id,
                    condition_id=v.market.condition_id,
                    question=v.market.question,
                    side=v.side,
                    order_type=OrderType.LIMIT,
                    price=v.price,
                    size_usdc=size,
                    confidence=min(v.edge / 0.05, 1.0),
                    reason=v.reason,
                )
            )

        intents.sort(key=lambda x: x.confidence, reverse=True)
        if intents:
            logger.info(
                "correlation_scanner: %d signals (top edge %.4f)",
                len(intents),
                intents[0].confidence,
            )
        return intents

    # Strategy ABC compliance (async wrappers around sync scan)

    async def evaluate(self, market_state: MarketState) -> Optional[OrderIntent]:
        results = self.scan([market_state])
        return results[0] if results else None

    async def evaluate_batch(self, markets: list[MarketState]) -> list[OrderIntent]:
        return self.scan(markets)

    # ── complementary check (YES + NO within one market) ───────────

    def _check_complementary(self, markets: list[MarketState]) -> list[_Violation]:
        violations: list[_Violation] = []
        for m in markets:
            try:
                deviation = abs(m.yes_price + m.no_price - 1.0)
                if deviation <= self.complement_tolerance:
                    continue

                # Determine which side is underpriced
                total = m.yes_price + m.no_price
                if total < 1.0:
                    # Both sides are cheap — buy the cheaper one (bigger discount)
                    if m.yes_price <= m.no_price:
                        side, price = Side.YES, m.yes_price
                    else:
                        side, price = Side.NO, m.no_price
                    edge = 1.0 - total
                else:
                    # Over-priced — skip (selling requires existing position)
                    continue

                violations.append(_Violation(
                    kind="complementary",
                    market=m,
                    side=side,
                    price=price,
                    edge=edge,
                    reason=(
                        f"COMP: YES={m.yes_price:.4f} + NO={m.no_price:.4f} = "
                        f"{total:.4f}, deviation={deviation:.4f}"
                    ),
                ))
            except Exception:
                logger.exception("complementary check failed for %s", m.market_id)
        return violations

    # ── parent/child check (nested timeframes) ─────────────────────

    def _check_parent_child(self, markets: list[MarketState]) -> list[_Violation]:
        """Find markets with similar questions but different deadlines."""
        violations: list[_Violation] = []

        # Pre-compute keywords and date ordinals
        enriched: list[tuple[MarketState, set[str], Optional[int]]] = []
        for m in markets:
            kw = _keywords(m.question)
            dt = _extract_date_ordinal(m.question)
            if kw and dt is not None:
                enriched.append((m, kw, dt))

        for (m1, kw1, d1), (m2, kw2, d2) in combinations(enriched, 2):
            try:
                if d1 == d2:
                    continue
                sim = _keyword_similarity(kw1, kw2)
                if sim < self.keyword_sim_threshold:
                    continue

                # Ensure short is the earlier deadline, long is the later
                if d1 < d2:
                    short_m, long_m = m1, m2
                else:
                    short_m, long_m = m2, m1

                # Longer timeframe YES must be >= shorter timeframe YES
                if long_m.yes_price < short_m.yes_price:
                    edge = short_m.yes_price - long_m.yes_price
                    violations.append(_Violation(
                        kind="parent_child",
                        market=long_m,
                        side=Side.YES,
                        price=long_m.yes_price,
                        edge=edge,
                        reason=(
                            f"P/C: '{short_m.question[:60]}' YES={short_m.yes_price:.4f} > "
                            f"'{long_m.question[:60]}' YES={long_m.yes_price:.4f}, "
                            f"edge={edge:.4f}"
                        ),
                    ))
            except Exception:
                logger.exception(
                    "parent_child check failed for %s vs %s",
                    m1.market_id, m2.market_id,
                )
        return violations

    # ── mutually exclusive check (same event, different outcomes) ──

    def _check_mutually_exclusive(self, markets: list[MarketState]) -> list[_Violation]:
        """Group markets by shared topic and check probability sums."""
        violations: list[_Violation] = []

        # Build keyword index
        kw_map: dict[str, set[str]] = {}
        for m in markets:
            kw_map[m.market_id] = _keywords(m.question)

        # Group markets that look like the same question with different subjects
        # e.g. "Will Biden win 2028?" vs "Will Trump win 2028?"
        groups: dict[str, list[MarketState]] = defaultdict(list)

        for m1, m2 in combinations(markets, 2):
            try:
                kw1 = kw_map.get(m1.market_id, set())
                kw2 = kw_map.get(m2.market_id, set())
                if not kw1 or not kw2:
                    continue

                sim = _keyword_similarity(kw1, kw2)
                if sim < self.keyword_sim_threshold:
                    continue

                # Use the intersection as a group key
                shared = tuple(sorted(kw1 & kw2))
                if len(shared) < 2:
                    continue
                key = "|".join(shared)
                # Add both markets to the group
                existing_ids = {gm.market_id for gm in groups[key]}
                if m1.market_id not in existing_ids:
                    groups[key].append(m1)
                if m2.market_id not in existing_ids:
                    groups[key].append(m2)
            except Exception:
                logger.exception(
                    "exclusive grouping failed for %s vs %s",
                    m1.market_id, m2.market_id,
                )

        for key, group in groups.items():
            try:
                if len(group) < 2:
                    continue

                prob_sum = sum(m.yes_price for m in group)

                if prob_sum > self.exclusive_sum_upper:
                    # Over-allocated — the most expensive market is likely
                    # overpriced; buy NO on the most expensive
                    most_expensive = max(group, key=lambda m: m.yes_price)
                    edge = (prob_sum - 1.0) / len(group)
                    violations.append(_Violation(
                        kind="mutually_exclusive",
                        market=most_expensive,
                        side=Side.NO,
                        price=most_expensive.no_price,
                        edge=edge,
                        reason=(
                            f"EXCL: {len(group)} markets sum={prob_sum:.4f} > "
                            f"{self.exclusive_sum_upper}; sell YES on "
                            f"'{most_expensive.question[:60]}' "
                            f"(YES={most_expensive.yes_price:.4f})"
                        ),
                    ))
                elif prob_sum < self.exclusive_sum_lower:
                    # Under-allocated — the cheapest market is likely underpriced
                    cheapest = min(group, key=lambda m: m.yes_price)
                    edge = (1.0 - prob_sum) / len(group)
                    violations.append(_Violation(
                        kind="mutually_exclusive",
                        market=cheapest,
                        side=Side.YES,
                        price=cheapest.yes_price,
                        edge=edge,
                        reason=(
                            f"EXCL: {len(group)} markets sum={prob_sum:.4f} < "
                            f"{self.exclusive_sum_lower}; buy YES on "
                            f"'{cheapest.question[:60]}' "
                            f"(YES={cheapest.yes_price:.4f})"
                        ),
                    ))
            except Exception:
                logger.exception("exclusive sum check failed for group %s", key)

        return violations
