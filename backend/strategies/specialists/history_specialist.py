"""
Polymarket History Specialist.

Queries real resolved Polymarket markets (via the Gamma API through
backend.data_layer.history_client) that are textually similar to the
target, computes crowd hit-rate + average final-price error, and asks
Claude to draw lessons for the current market.

Real data only: the reference set is actual closed Polymarket markets.
Nothing is synthesized.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Optional

from backend.config import settings
from backend.data_layer.history_client import get_history_client, HistorySnapshot
from backend.strategies.base import MarketState
from backend.strategies.specialists.base import (
    Specialist,
    SpecialistOpinion,
    format_market_context,
)

logger = logging.getLogger(__name__)


SYSTEM_PROMPT = """You are the Polymarket History Specialist on a trading team.

You are given a target prediction market plus a reference set of past
Polymarket markets that resolved, ranked by keyword similarity to the
target. For each comparable you see: the final traded price, whether the
market resolved YES or NO, and whether the crowd was "right" (price on
the correct side of 0.5 at close).

Your job: use the base-rate pattern from the comparables to produce a
calibrated probability for the target. If the comparables show the
crowd tends to over/underreact in this category, adjust accordingly.
If the comparables are too sparse or too unlike the target, return a
low-confidence opinion close to the current market price.

Respond with a single JSON object, nothing else:
{
  "probability": 0.XX,
  "confidence": 0.XX,
  "base_rate": 0.XX,
  "lessons": ["lesson 1", "lesson 2"],
  "rationale": "1-3 sentences"
}"""


class HistorySpecialist(Specialist):
    name = "history"

    def __init__(self) -> None:
        self._client = None
        self._api_key = settings.ai.anthropic_api_key

    def _get_client(self):
        if self._client is not None:
            return self._client
        if not self._api_key:
            return None
        try:
            import anthropic
            self._client = anthropic.AsyncAnthropic(api_key=self._api_key)
            return self._client
        except ImportError:
            logger.error("anthropic SDK not installed — HistorySpecialist disabled")
            return None

    async def analyze(self, market: MarketState) -> Optional[SpecialistOpinion]:
        client = self._get_client()
        if client is None:
            return None

        history = get_history_client()
        try:
            snap: HistorySnapshot = await history.find_comparables(
                question=market.question,
                category=market.category,
                limit=15,
            )
        except Exception as e:
            logger.warning(f"HistorySpecialist Gamma fetch failed: {e}")
            return None

        if not snap.comparables:
            return SpecialistOpinion(
                specialist=self.name,
                market_id=market.market_id,
                probability=market.yes_price,
                confidence=0.0,
                rationale="No resolved comparables found on Gamma",
            )

        ref_block = self._format_comparables(snap)
        user_prompt = (
            f"{format_market_context(market)}\n\n"
            f"Resolved comparables (real Polymarket closed markets):\n"
            f"{ref_block}\n\n"
            f"Aggregate crowd hit-rate on comparables: "
            f"{snap.crowd_hit_rate if snap.crowd_hit_rate is not None else 'n/a'}\n"
            f"Average |final_price - truth|: "
            f"{snap.avg_final_edge if snap.avg_final_edge is not None else 'n/a'}\n\n"
            f"Return the JSON object as instructed."
        )

        try:
            resp = await client.messages.create(
                model="claude-sonnet-4-5",
                max_tokens=1000,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user_prompt}],
            )
        except Exception as e:
            logger.warning(f"HistorySpecialist Claude call failed: {e}")
            return SpecialistOpinion(
                specialist=self.name,
                market_id=market.market_id,
                probability=market.yes_price,
                confidence=0.0,
                rationale=f"API error: {e}",
                error=str(e),
            )

        text = ""
        for block in (resp.content or []):
            if getattr(block, "type", "") == "text":
                text = getattr(block, "text", "") or text

        data = _extract_json(text)
        if not data:
            return SpecialistOpinion(
                specialist=self.name,
                market_id=market.market_id,
                probability=market.yes_price,
                confidence=0.0,
                rationale="Could not parse JSON from history specialist",
            )

        return SpecialistOpinion(
            specialist=self.name,
            market_id=market.market_id,
            probability=_clip(float(data.get("probability", market.yes_price))),
            confidence=_clip(float(data.get("confidence", 0.3))),
            rationale=str(data.get("rationale", ""))[:500],
            data_points={
                "base_rate": data.get("base_rate"),
                "lessons": data.get("lessons", [])[:5],
                "crowd_hit_rate": snap.crowd_hit_rate,
                "avg_final_edge": snap.avg_final_edge,
                "n_comparables": len(snap.comparables),
                "total_found": snap.total_found,
            },
        )

    @staticmethod
    def _format_comparables(snap: HistorySnapshot) -> str:
        lines = []
        for i, c in enumerate(snap.comparables[:12], 1):
            outcome = c.resolved_outcome or "?"
            right = "✓" if c.crowd_was_right else ("✗" if c.crowd_was_right is False else "?")
            lines.append(
                f"{i:2d}. final={c.final_yes_price:.3f} resolved={outcome} crowd={right} "
                f"vol=${c.volume:,.0f}  {c.question[:100]}"
            )
        return "\n".join(lines)


def _clip(x: float) -> float:
    if x != x:
        return 0.5
    return max(0.001, min(0.999, x))


def _extract_json(text: str) -> Optional[dict]:
    if not text:
        return None
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if fenced:
        try:
            return json.loads(fenced.group(1))
        except json.JSONDecodeError:
            pass
    brace = re.search(r"\{.*\}", text, re.DOTALL)
    if brace:
        try:
            return json.loads(brace.group(0))
        except json.JSONDecodeError:
            return None
    return None
