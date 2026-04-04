"""
On-Chain Flow Specialist.

Reads real Polygon PoS data via Alchemy (USDC flows into/out of the
Polymarket exchange contract) and asks Claude to interpret whether
whales are arming up, taking profit, or ambivalent — then returns a
calibrated probability adjustment.

Real chain data only. If Alchemy isn't configured or the RPC fails,
the specialist returns None and the orchestrator skips it.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Optional

from backend.config import settings
from backend.data_layer.alchemy_client import get_alchemy_client, OnChainSnapshot
from backend.strategies.base import MarketState
from backend.strategies.specialists.base import (
    Specialist,
    SpecialistOpinion,
    format_market_context,
)

logger = logging.getLogger(__name__)


SYSTEM_PROMPT = """You are the On-Chain Flow Specialist on a Polymarket trading team.

You are given a market snapshot plus real Polygon PoS data showing USDC
flows into and out of the Polymarket exchange contract over the last hour.

Your job: interpret the flows. Large inflow = whales depositing capital
to take positions. Large outflow = whales withdrawing / taking profit.
Neutral flows = no strong signal.

You DO NOT see which specific market the capital is flowing to — the
data is exchange-wide. Your probability adjustment should therefore be
modest (0.02-0.08 typical, not 0.20). Use confidence to express how
clearly the flows suggest direction.

Respond with a single JSON object, nothing else:
{
  "probability": 0.XX,
  "confidence": 0.XX,
  "flow_interpretation": "arming|taking_profit|neutral|mixed",
  "rationale": "1-3 sentences explaining the inference"
}"""


class OnChainSpecialist(Specialist):
    name = "onchain"

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
            logger.error("anthropic SDK not installed — OnChainSpecialist disabled")
            return None

    async def analyze(self, market: MarketState) -> Optional[SpecialistOpinion]:
        alchemy = get_alchemy_client()
        if not alchemy.is_configured():
            logger.debug("On-chain specialist skipped: ALCHEMY_POLYGON_URL not set")
            return None
        client = self._get_client()
        if client is None:
            return None

        # Pull last ~60 minutes of exchange USDC flows
        try:
            snap: OnChainSnapshot = await alchemy.get_exchange_flows(
                window_blocks=1800, min_usd=5000
            )
        except Exception as e:
            logger.warning(f"OnChainSpecialist Alchemy fetch failed: {e}")
            return None

        if snap.total_inflow_usd == 0 and snap.total_outflow_usd == 0:
            # No meaningful on-chain activity observed in the window.
            return SpecialistOpinion(
                specialist=self.name,
                market_id=market.market_id,
                probability=market.yes_price,
                confidence=0.0,
                rationale="No on-chain flows observed in the last hour",
                data_points={"window_blocks": snap.window_blocks},
            )

        flow_summary = self._summarize_flows(snap)
        user_prompt = (
            f"{format_market_context(market)}\n\n"
            f"On-chain data (last ~60 min on Polygon PoS):\n{flow_summary}\n\n"
            f"Return the JSON object as instructed."
        )

        try:
            resp = await client.messages.create(
                model="claude-sonnet-4-5",
                max_tokens=800,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user_prompt}],
            )
        except Exception as e:
            logger.warning(f"OnChainSpecialist Claude call failed: {e}")
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
                rationale="Could not parse JSON from on-chain specialist",
            )

        return SpecialistOpinion(
            specialist=self.name,
            market_id=market.market_id,
            probability=_clip(float(data.get("probability", market.yes_price))),
            confidence=_clip(float(data.get("confidence", 0.3))),
            rationale=str(data.get("rationale", ""))[:500],
            data_points={
                "flow_interpretation": data.get("flow_interpretation"),
                "total_inflow_usd": round(snap.total_inflow_usd, 2),
                "total_outflow_usd": round(snap.total_outflow_usd, 2),
                "net_flow_usd": round(snap.net_flow_usd, 2),
                "unique_addresses": snap.unique_addresses,
                "large_transfers": [
                    {"usd": round(t.amount_usd, 2), "direction": t.direction}
                    for t in snap.large_transfers[:10]
                ],
            },
        )

    @staticmethod
    def _summarize_flows(snap: OnChainSnapshot) -> str:
        lines = [
            f"Total USDC inflow to exchange:  ${snap.total_inflow_usd:,.0f}",
            f"Total USDC outflow from exchange: ${snap.total_outflow_usd:,.0f}",
            f"Net flow: ${snap.net_flow_usd:+,.0f}",
            f"Unique addresses touching exchange: {snap.unique_addresses}",
            f"Large transfers (≥$5k): {len(snap.large_transfers)}",
        ]
        if snap.large_transfers:
            lines.append("Top transfers:")
            for t in snap.large_transfers[:8]:
                lines.append(f"  - ${t.amount_usd:,.0f}  {t.direction}  from {t.from_addr[:10]}… to {t.to_addr[:10]}…")
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
