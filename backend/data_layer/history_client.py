"""
Polymarket history client — queries the Gamma API for resolved markets
that are semantically similar to a given target market.

Used by the History Specialist to answer:
  "On past markets like this, how often was the crowd right?
   How far did the final price drift from the realized outcome?
   What resolution surprises showed up?"

All data is real (Gamma API). No synthetic history, no mock reference set.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Optional

from backend.data_layer.gamma_client import GammaClient, GAMMA_BASE

logger = logging.getLogger(__name__)


_STOPWORDS = {
    "will", "the", "a", "an", "be", "is", "are", "in", "on", "at", "to",
    "of", "for", "by", "with", "and", "or", "than", "more", "less", "by",
    "before", "after", "reach", "hit", "over", "under", "pass", "this",
    "next", "that", "which", "who", "when", "what", "how", "any",
}


def _keywords(question: str, top_n: int = 5) -> list[str]:
    words = re.findall(r"[A-Za-z][A-Za-z0-9\-]+", (question or "").lower())
    # Keep content words; prefer longer ones (usually entities / topics)
    ranked = sorted(
        (w for w in words if len(w) > 3 and w not in _STOPWORDS),
        key=lambda w: -len(w),
    )
    seen: set[str] = set()
    out: list[str] = []
    for w in ranked:
        if w in seen:
            continue
        seen.add(w)
        out.append(w)
        if len(out) >= top_n:
            break
    return out


@dataclass
class ResolvedComparable:
    """A closed market that's textually similar to the target."""
    market_id: str
    question: str
    category: str
    final_yes_price: float
    resolved_outcome: Optional[str]  # "YES" | "NO" | None if unknown
    liquidity: float
    volume: float
    crowd_was_right: Optional[bool]

    @property
    def edge_from_final(self) -> Optional[float]:
        """How far the final traded price was from the resolved outcome.
        0 = crowd nailed it. 1 = crowd maximally wrong.
        """
        if self.resolved_outcome is None:
            return None
        truth = 1.0 if self.resolved_outcome == "YES" else 0.0
        return abs(self.final_yes_price - truth)


@dataclass
class HistorySnapshot:
    """Aggregated statistics over a set of comparable resolved markets."""
    target_question: str
    comparables: list[ResolvedComparable]
    total_found: int = 0

    @property
    def crowd_hit_rate(self) -> Optional[float]:
        graded = [c for c in self.comparables if c.crowd_was_right is not None]
        if not graded:
            return None
        return sum(1 for c in graded if c.crowd_was_right) / len(graded)

    @property
    def avg_final_edge(self) -> Optional[float]:
        errs = [c.edge_from_final for c in self.comparables if c.edge_from_final is not None]
        if not errs:
            return None
        return sum(errs) / len(errs)


class HistoryClient:
    """Wraps GammaClient to find resolved markets similar to a target."""

    def __init__(self, gamma: Optional[GammaClient] = None) -> None:
        self._gamma = gamma or GammaClient()

    async def find_comparables(
        self,
        question: str,
        category: str = "",
        limit: int = 20,
    ) -> HistorySnapshot:
        keywords = _keywords(question)
        if not keywords:
            return HistorySnapshot(target_question=question, comparables=[], total_found=0)

        # Pull a generous slice of closed markets and filter client-side.
        # Gamma's server-side search is keyword-sparse, so we scan locally.
        try:
            raw = await self._gamma.get_markets(limit=500, closed=True, active=False)
        except Exception as e:
            logger.warning(f"HistoryClient: Gamma closed-market fetch failed: {e}")
            return HistorySnapshot(target_question=question, comparables=[], total_found=0)

        scored: list[tuple[int, ResolvedComparable]] = []
        for m in raw:
            q = (getattr(m, "question", "") or "").lower()
            if not q:
                continue
            overlap = sum(1 for kw in keywords if kw in q)
            if overlap == 0:
                continue
            # Category bonus
            if category and (getattr(m, "category", "") or "").lower() == category.lower():
                overlap += 1

            final_yes = float(getattr(m, "yes_price", 0) or 0)
            resolved_outcome = self._infer_outcome(m)
            crowd_was_right = None
            if resolved_outcome == "YES":
                crowd_was_right = final_yes >= 0.5
            elif resolved_outcome == "NO":
                crowd_was_right = final_yes < 0.5

            scored.append((
                overlap,
                ResolvedComparable(
                    market_id=getattr(m, "id", ""),
                    question=getattr(m, "question", ""),
                    category=getattr(m, "category", ""),
                    final_yes_price=final_yes,
                    resolved_outcome=resolved_outcome,
                    liquidity=float(getattr(m, "liquidity", 0) or 0),
                    volume=float(getattr(m, "volume", 0) or 0),
                    crowd_was_right=crowd_was_right,
                ),
            ))

        scored.sort(key=lambda x: (-x[0], -x[1].volume))
        top = [c for _, c in scored[:limit]]
        return HistorySnapshot(
            target_question=question,
            comparables=top,
            total_found=len(scored),
        )

    def _infer_outcome(self, market) -> Optional[str]:
        """Best-effort parse of the resolved outcome from Gamma's raw payload.

        Polymarket closed markets expose `umaResolutionStatus`, `resolved`,
        and sometimes the final outcome in `outcomePrices` (e.g. ['1','0']).
        We return "YES", "NO", or None.
        """
        raw = getattr(market, "raw", {}) or {}
        prices = raw.get("outcomePrices")
        if isinstance(prices, list) and len(prices) >= 2:
            try:
                ys, ns = float(prices[0]), float(prices[1])
                if ys >= 0.99 and ns <= 0.01:
                    return "YES"
                if ns >= 0.99 and ys <= 0.01:
                    return "NO"
            except (TypeError, ValueError):
                pass
        if isinstance(prices, str):
            # Sometimes comes as a JSON-encoded string
            import json as _json
            try:
                arr = _json.loads(prices)
                if isinstance(arr, list) and len(arr) >= 2:
                    ys, ns = float(arr[0]), float(arr[1])
                    if ys >= 0.99:
                        return "YES"
                    if ns >= 0.99:
                        return "NO"
            except Exception:
                pass
        # Fallback: final yes_price at exactly 0 or 1
        yp = float(getattr(market, "yes_price", 0.5) or 0.5)
        if yp >= 0.99:
            return "YES"
        if yp <= 0.01:
            return "NO"
        return None

    async def close(self) -> None:
        await self._gamma.close()


_client: Optional[HistoryClient] = None


def get_history_client() -> HistoryClient:
    global _client
    if _client is None:
        _client = HistoryClient()
    return _client
