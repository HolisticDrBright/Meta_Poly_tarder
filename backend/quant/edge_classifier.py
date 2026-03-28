"""
Edge Classifier — every trade must have a labeled edge type.

If the edge can't be clearly classified, the trade is blocked.
This prevents vague "the AI thinks so" trades from slipping through.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from enum import Enum
from typing import Optional

logger = logging.getLogger(__name__)


class EdgeType(str, Enum):
    RULES = "rules_edge"            # Headline differs from resolution criteria
    INFORMATION = "information_edge"  # Public info not yet absorbed by market
    BASE_RATE = "base_rate_edge"     # Historical frequency disagrees with price
    SENTIMENT = "sentiment_edge"     # Crowd overreacted emotionally
    STRUCTURAL = "structural_edge"   # Liquidity dislocation or YES+NO arb
    FAKE = "fake_edge"              # Dissolves under scrutiny


EDGE_DESCRIPTIONS = {
    EdgeType.RULES: "Market headline differs from resolution criteria. Careful reading reveals mispricing.",
    EdgeType.INFORMATION: "Public information exists that the market hasn't fully absorbed yet.",
    EdgeType.BASE_RATE: "Historical frequency is significantly different from current market price.",
    EdgeType.SENTIMENT: "Crowd has overreacted emotionally. Price should revert to fundamentals.",
    EdgeType.STRUCTURAL: "Liquidity dislocation or YES+NO arb gap exists. Mechanical, not predictive.",
    EdgeType.FAKE: "Edge appears to exist but dissolves under scrutiny.",
}


@dataclass
class EdgeClassification:
    edge_type: EdgeType
    confidence: float
    reasoning: str
    blocked: bool = False
    block_reason: str = ""


def classify_edge_heuristic(
    model_prob: float,
    market_price: float,
    arb_edge: float = 0.0,
    evidence_quality: float = 0.5,
    resolution_ambiguity: float = 0.0,
    headline_mismatch: bool = False,
) -> EdgeClassification:
    """
    Fast heuristic edge classification (no AI needed).

    Rules:
    - arb_edge > 0.01 → STRUCTURAL
    - headline_mismatch → RULES
    - evidence_quality > 0.7 → INFORMATION
    - large deviation + low evidence → BASE_RATE or SENTIMENT
    - everything else → needs AI or is FAKE
    """
    edge_mag = abs(model_prob - market_price)

    if arb_edge > 0.01:
        return EdgeClassification(
            edge_type=EdgeType.STRUCTURAL,
            confidence=min(arb_edge * 20, 1.0),
            reasoning=f"YES+NO gap of {arb_edge:.3f} detected",
        )

    if headline_mismatch:
        return EdgeClassification(
            edge_type=EdgeType.RULES,
            confidence=0.7,
            reasoning="Resolution criteria differ from market headline",
        )

    if evidence_quality > 0.7 and edge_mag > 0.05:
        return EdgeClassification(
            edge_type=EdgeType.INFORMATION,
            confidence=evidence_quality,
            reasoning=f"Strong evidence (q={evidence_quality:.2f}) with {edge_mag:.2f} edge",
        )

    if edge_mag > 0.10 and evidence_quality < 0.4:
        return EdgeClassification(
            edge_type=EdgeType.BASE_RATE,
            confidence=0.5,
            reasoning=f"Large deviation ({edge_mag:.2f}) without strong evidence — likely base rate",
        )

    if edge_mag < 0.03:
        return EdgeClassification(
            edge_type=EdgeType.FAKE,
            confidence=0.8,
            reasoning=f"Edge too small ({edge_mag:.3f}) to be meaningful after spread",
            blocked=True,
            block_reason="Edge magnitude below threshold",
        )

    # Uncertain — can't classify clearly
    return EdgeClassification(
        edge_type=EdgeType.SENTIMENT,
        confidence=0.4,
        reasoning="Moderate edge with moderate evidence — possibly sentiment-driven",
    )


async def classify_edge_ai(
    question: str,
    model_prob: float,
    market_price: float,
    evidence_quality: float,
    resolution_ambiguity: float,
    anthropic_api_key: str = "",
) -> EdgeClassification:
    """AI-powered edge classification using Claude."""
    if not anthropic_api_key:
        return classify_edge_heuristic(
            model_prob, market_price,
            evidence_quality=evidence_quality,
            resolution_ambiguity=resolution_ambiguity,
        )

    try:
        import anthropic

        client = anthropic.AsyncAnthropic(api_key=anthropic_api_key)
        prompt = f"""Given this market and analysis, classify the trading edge:
Market: {question}
Model probability: {model_prob:.3f}
Market price: {market_price:.3f}
Edge magnitude: {abs(model_prob - market_price):.3f}
Evidence quality: {evidence_quality:.2f}
Resolution ambiguity: {resolution_ambiguity:.2f}

Which edge type applies?
- rules_edge: headline differs from resolution criteria
- information_edge: public info not absorbed
- base_rate_edge: historical frequency disagrees
- sentiment_edge: crowd overreacted
- structural_edge: liquidity/arb gap
- fake_edge: dissolves under scrutiny

If you cannot clearly identify a real edge, return fake_edge.

Output ONLY JSON: {{"edge_type": "...", "reasoning": "...", "confidence": 0.0}}"""

        response = await client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=300,
            messages=[{"role": "user", "content": prompt}],
        )

        text = response.content[0].text
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            match = re.search(r'\{[^{}]*\}', text, re.DOTALL)
            data = json.loads(match.group()) if match else {"edge_type": "fake_edge", "confidence": 0.3}

        edge_type = EdgeType(data.get("edge_type", "fake_edge"))
        confidence = float(data.get("confidence", 0.5))
        blocked = edge_type == EdgeType.FAKE or confidence < 0.5

        return EdgeClassification(
            edge_type=edge_type,
            confidence=confidence,
            reasoning=data.get("reasoning", ""),
            blocked=blocked,
            block_reason="Edge unclassifiable or fake" if blocked else "",
        )
    except Exception as e:
        logger.warning(f"AI edge classification failed: {e}")
        return classify_edge_heuristic(
            model_prob, market_price,
            evidence_quality=evidence_quality,
            resolution_ambiguity=resolution_ambiguity,
        )
