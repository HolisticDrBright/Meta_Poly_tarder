"""
Strategy 4: Multi-Model AI Ensemble Forecaster

3-model ensemble (Claude + GPT-4o + MiroFish-lite) using the
7-agent AI Debate Floor for structured deliberation.

Agents:
  1. Statistics Expert — base rates and historical outcomes
  2. Time Decay Analyst — theta, resolution urgency
  3. Generalist Expert — balanced view with caveats
  4. Crypto/Macro Analyst — broader market context
  5. Devil's Advocate — steelman the contrarian position
  6. Jet Signal Analyst — interpret active flight signals
  7. Moderator — synthesize into final probability
"""

from __future__ import annotations

import json
import logging
import statistics
from dataclasses import dataclass, field
from typing import Any, Optional

from backend.strategies.base import (
    MarketState,
    OrderIntent,
    OrderType,
    Side,
    Strategy,
    StrategyName,
)

logger = logging.getLogger(__name__)


DEBATE_SYSTEM_PROMPT = """You are analyzing a Polymarket prediction market.
Respond ONLY as JSON. Include a probability estimate (0-1) and brief reasoning
for each agent below.

AGENTS:
1. Statistics Expert: base rates and historical outcomes
2. Time Decay Analyst: theta, resolution urgency, timing
3. Generalist Expert: balanced view with caveats
4. Crypto/Macro Analyst: broader market context
5. Devil's Advocate: steelman the contrarian position
6. Jet Signal Analyst: interpret any active flight signals
7. Moderator: synthesize all views into final probability

OUTPUT FORMAT:
{
  "agents": [
    {"role": "Statistics Expert", "probability": 0.XX, "reasoning": "..."},
    {"role": "Time Decay Analyst", "probability": 0.XX, "reasoning": "..."},
    {"role": "Generalist Expert", "probability": 0.XX, "reasoning": "..."},
    {"role": "Crypto/Macro Analyst", "probability": 0.XX, "reasoning": "..."},
    {"role": "Devil's Advocate", "probability": 0.XX, "reasoning": "..."},
    {"role": "Jet Signal Analyst", "probability": 0.XX, "reasoning": "..."},
    {"role": "Moderator", "probability": 0.XX, "reasoning": "..."}
  ],
  "final_probability": 0.XX,
  "confidence": "low|medium|high",
  "recommended_action": "BUY_YES|BUY_NO|HOLD",
  "time_sensitivity": "urgent|normal|patient"
}"""


@dataclass
class DebateResult:
    """Parsed result from the AI Debate Floor."""

    agents: list[dict[str, Any]]
    final_probability: float
    confidence: str
    recommended_action: str
    time_sensitivity: str
    model_source: str  # which AI model produced this

    @classmethod
    def from_json(cls, data: dict, source: str) -> DebateResult:
        return cls(
            agents=data.get("agents", []),
            final_probability=float(data.get("final_probability", 0.5)),
            confidence=data.get("confidence", "low"),
            recommended_action=data.get("recommended_action", "HOLD"),
            time_sensitivity=data.get("time_sensitivity", "normal"),
            model_source=source,
        )


@dataclass
class EnsembleResult:
    """Fused result from multiple AI models."""

    debates: list[DebateResult]
    ensemble_probability: float
    ensemble_confidence: float  # 0-1, high = models agree
    recommended_action: str
    spread: float  # std of model estimates


class EnsembleAI(Strategy):
    """Multi-model AI ensemble with debate floor."""

    name = StrategyName.ENSEMBLE_AI

    # Weights for the 3-model ensemble
    WEIGHTS = {
        "claude": 0.40,
        "gpt4": 0.35,
        "mirofish": 0.25,
    }

    def __init__(
        self,
        anthropic_api_key: str = "",
        openai_api_key: str = "",
        min_confidence: float = 0.6,
        min_edge: float = 0.05,
        bankroll: float = 10_000,
        kelly_fraction: float = 0.25,
        max_trade_usdc: float = 150,
    ) -> None:
        self.anthropic_key = anthropic_api_key
        self.openai_key = openai_api_key
        self.min_confidence = min_confidence
        self.min_edge = min_edge
        self.bankroll = bankroll
        self.kelly_fraction = kelly_fraction
        self.max_trade_usdc = max_trade_usdc

    def _build_user_prompt(
        self,
        market: MarketState,
        context: str = "",
        jet_signals: str = "",
        whale_positions: str = "",
    ) -> str:
        return (
            f"MARKET: {market.question}\n"
            f"CURRENT PRICE: {market.yes_price:.3f}\n"
            f"LIQUIDITY: ${market.liquidity:,.0f}\n"
            f"HOURS TO CLOSE: {market.hours_to_close:.1f}\n"
            f"CONTEXT: {context}\n"
            f"JET SIGNALS: {jet_signals}\n"
            f"WHALE POSITIONS: {whale_positions}\n"
        )

    async def _call_claude(self, prompt: str) -> Optional[DebateResult]:
        """Call Claude API for debate."""
        if not self.anthropic_key:
            return None
        try:
            import anthropic

            client = anthropic.AsyncAnthropic(api_key=self.anthropic_key)
            response = await client.messages.create(
                model="claude-sonnet-4-20250514",
                max_tokens=2000,
                system=DEBATE_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": prompt}],
            )
            text = response.content[0].text
            # Handle both JSON and mixed-content responses
            try:
                data = json.loads(text)
            except json.JSONDecodeError:
                # Try extracting JSON from markdown code blocks
                import re
                match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
                if match:
                    data = json.loads(match.group(1))
                else:
                    # Last resort: try to find raw JSON object
                    brace_start = text.find("{")
                    if brace_start >= 0:
                        data = json.loads(text[brace_start:])
                    else:
                        logger.warning("Claude response was not JSON")
                        return None
            return DebateResult.from_json(data, "claude")
        except Exception as e:
            logger.error(f"Claude debate failed: {e}")
            return None

    async def _call_gpt4(self, prompt: str) -> Optional[DebateResult]:
        """Call GPT-4o API for debate."""
        if not self.openai_key:
            return None
        try:
            import openai

            client = openai.AsyncOpenAI(api_key=self.openai_key)
            response = await client.chat.completions.create(
                model="gpt-4o",
                messages=[
                    {"role": "system", "content": DEBATE_SYSTEM_PROMPT},
                    {"role": "user", "content": prompt},
                ],
                max_tokens=2000,
                response_format={"type": "json_object"},
            )
            text = response.choices[0].message.content
            data = json.loads(text)
            return DebateResult.from_json(data, "gpt4")
        except Exception as e:
            logger.error(f"GPT-4o debate failed: {e}")
            return None

    def _mirofish_lite(self, market: MarketState) -> DebateResult:
        """Lightweight local swarm simulation (no API needed)."""
        # Simple heuristic ensemble: vary the price by noise to simulate
        # 20 agents with slightly different priors
        import random

        probs = []
        rng = random.Random(hash(market.market_id))
        for _ in range(20):
            noise = rng.gauss(0, 0.05)
            p = max(0.01, min(0.99, market.yes_price + noise))
            probs.append(p)
        avg = statistics.mean(probs)
        return DebateResult(
            agents=[],
            final_probability=avg,
            confidence="low",
            recommended_action="HOLD",
            time_sensitivity="normal",
            model_source="mirofish",
        )

    async def run_ensemble(
        self,
        market: MarketState,
        context: str = "",
        jet_signals: str = "",
        whale_positions: str = "",
    ) -> EnsembleResult:
        """Run all models and fuse results."""
        prompt = self._build_user_prompt(market, context, jet_signals, whale_positions)

        claude_result = await self._call_claude(prompt)
        gpt4_result = await self._call_gpt4(prompt)
        mirofish_result = self._mirofish_lite(market)

        debates = [r for r in [claude_result, gpt4_result, mirofish_result] if r]

        # Weighted average
        total_weight = 0.0
        weighted_sum = 0.0
        probs = []
        for d in debates:
            w = self.WEIGHTS.get(d.model_source, 0.1)
            weighted_sum += d.final_probability * w
            total_weight += w
            probs.append(d.final_probability)

        ensemble_p = weighted_sum / total_weight if total_weight > 0 else 0.5
        spread = statistics.stdev(probs) if len(probs) > 1 else 0.5
        confidence = max(0, 1 - spread / 0.5)

        if ensemble_p > market.yes_price + self.min_edge:
            action = "BUY_YES"
        elif ensemble_p < market.yes_price - self.min_edge:
            action = "BUY_NO"
        else:
            action = "HOLD"

        return EnsembleResult(
            debates=debates,
            ensemble_probability=ensemble_p,
            ensemble_confidence=confidence,
            recommended_action=action,
            spread=spread,
        )

    async def evaluate(self, market_state: MarketState) -> Optional[OrderIntent]:
        result = await self.run_ensemble(market_state)

        if result.recommended_action == "HOLD":
            return None
        if result.ensemble_confidence < self.min_confidence:
            return None

        edge = abs(result.ensemble_probability - market_state.yes_price)
        if edge < self.min_edge:
            return None

        # Kelly sizing
        from backend.quant.entropy import quarter_kelly

        f = quarter_kelly(result.ensemble_probability, market_state.yes_price)
        size = min(abs(f) * self.bankroll, self.max_trade_usdc)
        if size < 1.0:
            return None

        side = Side.YES if result.recommended_action == "BUY_YES" else Side.NO
        price = market_state.yes_price if side == Side.YES else market_state.no_price

        return OrderIntent(
            strategy=self.name,
            market_id=market_state.market_id,
            condition_id=market_state.condition_id,
            question=market_state.question,
            side=side,
            order_type=OrderType.LIMIT,
            price=price,
            size_usdc=size,
            confidence=result.ensemble_confidence,
            reason=(
                f"Ensemble: p={result.ensemble_probability:.3f} "
                f"(spread={result.spread:.3f}), "
                f"confidence={result.ensemble_confidence:.2f}, "
                f"edge={edge:.3f}"
            ),
            kelly_fraction=f,
        )

    async def evaluate_batch(self, markets: list[MarketState]) -> list[OrderIntent]:
        intents = []
        for m in markets:
            intent = await self.evaluate(m)
            if intent:
                intents.append(intent)
        return intents
