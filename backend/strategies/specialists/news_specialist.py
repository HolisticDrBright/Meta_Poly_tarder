"""
News Retrieval Specialist.

Calls Anthropic's Claude with the server-side `web_search` tool to pull
real, cited news about the target market from the last 48 hours and
score whether genuinely new information has arrived since the market
last moved.

Output: a SpecialistOpinion with:
  - probability: specialist's fair probability for YES based on news
  - confidence: how decisive the news is
  - rationale: summary + citations
  - data_points: raw source URLs and headlines Claude used

Real web data only. No hardcoded headlines, no canned responses.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Optional

from backend.config import settings
from backend.strategies.base import MarketState
from backend.strategies.specialists.base import (
    Specialist,
    SpecialistOpinion,
    format_market_context,
)

logger = logging.getLogger(__name__)


SYSTEM_PROMPT = """You are the News Retrieval Specialist on a Polymarket trading team.

Your single job: search the web for the most recent relevant information
about a prediction market, then return a calibrated probability and a
freshness score based on what you actually found.

You MUST use the web_search tool to fetch real sources. Do not rely on
training data alone — markets move on the margin of fresh information.

RULES:
- Prefer sources from the last 48 hours. Note the publication date of
  everything you cite.
- Ignore sources older than 7 days unless they're essential context.
- If the web returned nothing meaningfully new, say so — do not pretend
  the existing market price is right or wrong.
- Cite 3-8 real URLs. Never fabricate a source.

Respond with a single JSON object, nothing else:
{
  "probability": 0.XX,
  "confidence": 0.XX,
  "freshness_score": 0.XX,
  "new_info_detected": true|false,
  "key_findings": ["finding 1", "finding 2", ...],
  "sources": [{"title": "...", "url": "...", "date": "YYYY-MM-DD"}],
  "rationale": "1-3 sentences on why you arrived at this probability"
}"""


class NewsSpecialist(Specialist):
    name = "news"

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
            logger.error("anthropic SDK not installed — NewsSpecialist disabled")
            return None

    async def analyze(self, market: MarketState) -> Optional[SpecialistOpinion]:
        client = self._get_client()
        if client is None:
            return None

        user_prompt = (
            f"{format_market_context(market)}\n\n"
            f"Search the web for news from the last 48 hours that could "
            f"move this market, then produce the JSON object as instructed."
        )

        try:
            resp = await client.messages.create(
                model="claude-sonnet-4-5",
                max_tokens=2000,
                system=SYSTEM_PROMPT,
                tools=[{
                    "type": "web_search_20250305",
                    "name": "web_search",
                    "max_uses": 5,
                }],
                messages=[{"role": "user", "content": user_prompt}],
            )
        except Exception as e:
            logger.warning(f"NewsSpecialist web_search call failed: {e}")
            return SpecialistOpinion(
                specialist=self.name,
                market_id=market.market_id,
                probability=market.yes_price,
                confidence=0.0,
                rationale=f"API error: {e}",
                error=str(e),
            )

        # Extract the last text block (the model's final answer)
        text = ""
        sources_used: list[dict] = []
        for block in (resp.content or []):
            btype = getattr(block, "type", "")
            if btype == "text":
                text = getattr(block, "text", "") or text
            elif btype == "web_search_tool_result":
                content = getattr(block, "content", None) or []
                for item in content:
                    if isinstance(item, dict) and item.get("type") == "web_search_result":
                        sources_used.append({
                            "title": item.get("title", ""),
                            "url": item.get("url", ""),
                        })

        data = _extract_json(text)
        if not data:
            return SpecialistOpinion(
                specialist=self.name,
                market_id=market.market_id,
                probability=market.yes_price,
                confidence=0.0,
                rationale="Could not parse JSON from news specialist",
            )

        prob = _clip(float(data.get("probability", market.yes_price)))
        conf = _clip(float(data.get("confidence", 0.3)))
        return SpecialistOpinion(
            specialist=self.name,
            market_id=market.market_id,
            probability=prob,
            confidence=conf,
            rationale=str(data.get("rationale", ""))[:800],
            data_points={
                "freshness_score": data.get("freshness_score"),
                "new_info_detected": data.get("new_info_detected"),
                "key_findings": data.get("key_findings", [])[:6],
                "sources": (data.get("sources") or sources_used)[:8],
            },
        )


def _clip(x: float) -> float:
    if x != x:  # NaN
        return 0.5
    return max(0.001, min(0.999, x))


def _extract_json(text: str) -> Optional[dict]:
    if not text:
        return None
    # Try fenced ```json blocks first, then raw object.
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
