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

    # Weights for the AI ensemble. Only real model calls contribute —
    # the old "mirofish" synthetic-swarm fallback has been removed.
    WEIGHTS = {
        "claude": 0.55,
        "gpt4": 0.45,
    }

    def __init__(
        self,
        anthropic_api_key: str = "",
        openai_api_key: str = "",
        min_confidence: float = 0.6,
        min_edge: float = 0.05,
        bankroll: float = 300,
        kelly_fraction: float = 0.25,
        max_trade_usdc: float = 4,
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

    # Class-level circuit breaker for GPT-4o. Once the OpenAI account hits
    # insufficient_quota, retrying on every market of every cycle wastes
    # ~15-20 seconds per call and spams the log. After N consecutive 429s,
    # disable GPT-4o for the rest of the process lifetime. User can top up
    # their OpenAI account and restart to re-enable.
    _gpt4_disabled: bool = False
    _gpt4_429_count: int = 0
    _gpt4_429_threshold: int = 3

    async def _call_claude(self, prompt: str) -> Optional[DebateResult]:
        """Call Claude API for debate."""
        if not self.anthropic_key:
            return None
        try:
            import anthropic

            client = anthropic.AsyncAnthropic(api_key=self.anthropic_key)
            response = await client.messages.create(
                model="claude-sonnet-4-5",
                max_tokens=2000,
                system=DEBATE_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": prompt}],
            )
            text = ""
            for block in (response.content or []):
                if getattr(block, "type", "") == "text":
                    text = getattr(block, "text", "") or text
            try:
                data = json.loads(text)
            except json.JSONDecodeError:
                import re as _re
                match = _re.search(r"\{.*\}", text, _re.DOTALL)
                if not match:
                    logger.error(f"Claude returned non-JSON: {text[:200]}")
                    return None
                data = json.loads(match.group())
            result = DebateResult.from_json(data, "claude")
            logger.info(
                f"Claude OK: p={result.final_probability:.3f} "
                f"conf={result.confidence} {result.recommended_action}"
            )
            return result
        except Exception as e:
            logger.error(f"Claude debate failed: {e}")
            return None

    async def _call_gpt4(self, prompt: str) -> Optional[DebateResult]:
        """Call GPT-4o API for debate with per-process quota circuit breaker."""
        if not self.openai_key:
            return None
        if EnsembleAI._gpt4_disabled:
            # Circuit breaker tripped earlier — don't even try.
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
            # Success — reset the 429 counter
            EnsembleAI._gpt4_429_count = 0
            text = response.choices[0].message.content
            data = json.loads(text)
            return DebateResult.from_json(data, "gpt4")
        except Exception as e:
            err_str = str(e)
            is_quota = "429" in err_str or "insufficient_quota" in err_str or "quota" in err_str.lower()
            if is_quota:
                EnsembleAI._gpt4_429_count += 1
                if EnsembleAI._gpt4_429_count >= EnsembleAI._gpt4_429_threshold:
                    EnsembleAI._gpt4_disabled = True
                    logger.error(
                        f"GPT-4o DISABLED for this process — "
                        f"{EnsembleAI._gpt4_429_count} consecutive 429 quota errors. "
                        f"Top up your OpenAI account at "
                        f"platform.openai.com/account/billing and restart backend."
                    )
                else:
                    logger.error(f"GPT-4o 429 quota error ({EnsembleAI._gpt4_429_count}/{EnsembleAI._gpt4_429_threshold})")
            else:
                logger.error(f"GPT-4o debate failed: {e}")
            return None

    async def run_ensemble(
        self,
        market: MarketState,
        context: str = "",
        jet_signals: str = "",
        whale_positions: str = "",
    ) -> EnsembleResult:
        """Run all models and fuse results."""
        # Specialist layer runs FIRST on gated markets. Its findings are
        # injected into the outer debate context so Claude + GPT-4o can
        # read real news/on-chain/history/swarm findings before forming
        # their own 7-role views. The specialists' fused probability is
        # also folded into the final ensemble weighting.
        specialist_bundle = None
        specialist_context = ""
        try:
            from backend.strategies.specialists.orchestrator import (
                get_specialist_orchestrator,
            )
            orch = get_specialist_orchestrator()
            specialist_bundle = await orch.analyze(market)
            if specialist_bundle is not None:
                specialist_context = specialist_bundle.context_for_outer_debate
        except Exception as e:
            logger.warning(f"Specialist layer failed (continuing without): {e}")

        merged_context = context
        if specialist_context:
            merged_context = (
                f"{context}\n\n=== SPECIALIST LAYER ===\n{specialist_context}"
                if context else f"=== SPECIALIST LAYER ===\n{specialist_context}"
            )

        prompt = self._build_user_prompt(market, merged_context, jet_signals, whale_positions)

        claude_result = await self._call_claude(prompt)
        gpt4_result = await self._call_gpt4(prompt)

        # Only real model responses contribute. If both fail (no keys /
        # API down), debates is empty and run_ensemble returns a HOLD
        # action with zero confidence — the strategy will skip this
        # market rather than fabricating a signal.
        debates = [r for r in [claude_result, gpt4_result] if r]

        # Weighted average across outer models. Pulls the latest
        # LEARNED weights from the learning loop output so when the
        # analyzer decides Claude is outperforming GPT-4o (or vice
        # versa), the next ensemble fusion uses the updated balance.
        try:
            from backend.learning.weights import get_model_weights
            active_model_weights = get_model_weights()
        except Exception:
            active_model_weights = self.WEIGHTS

        total_weight = 0.0
        weighted_sum = 0.0
        probs = []
        for d in debates:
            w = active_model_weights.get(d.model_source, self.WEIGHTS.get(d.model_source, 0.1))
            weighted_sum += d.final_probability * w
            total_weight += w
            probs.append(d.final_probability)

        # Fold specialist opinions into the fusion with their assigned
        # weights. Shadow-mode opinions already have weight=0 and do not
        # influence the outcome (they still get logged for learning).
        if specialist_bundle is not None:
            for op in specialist_bundle.opinions:
                if op.weight <= 0:
                    continue
                weighted_sum += op.probability * op.weight
                total_weight += op.weight
                probs.append(op.probability)

        # With no real model responses, hold — never synthesize a probability.
        if total_weight == 0:
            return EnsembleResult(
                debates=[],
                ensemble_probability=market.yes_price,
                ensemble_confidence=0.0,
                recommended_action="HOLD",
                spread=0.0,
            )

        ensemble_p = weighted_sum / total_weight
        spread = statistics.stdev(probs) if len(probs) > 1 else 0.0
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
        # Regime gate — ensemble is most useful in information-driven markets
        from backend.quant.regime import classify as classify_regime
        from backend.quant.sizing import ev_gate_passes, regime_allows_strategy

        regime_call = classify_regime(market_state)
        if not regime_allows_strategy(regime_call.regime, self.name):
            return None

        result = await self.run_ensemble(market_state)

        if result.recommended_action == "HOLD":
            return None
        if result.ensemble_confidence < self.min_confidence:
            return None

        edge = abs(result.ensemble_probability - market_state.yes_price)
        if edge < self.min_edge:
            return None

        side = Side.YES if result.recommended_action == "BUY_YES" else Side.NO
        price = market_state.yes_price if side == Side.YES else market_state.no_price

        # Build (fair, market) pair in the direction we're actually trading
        # for the EV gate. For NO side we compare 1-ensemble_p to no_price.
        fair_for_side = (
            result.ensemble_probability if side == Side.YES
            else (1.0 - result.ensemble_probability)
        )
        if not ev_gate_passes(
            fair_probability=fair_for_side,
            market_price=price,
            spread=market_state.spread,
        ):
            return None

        # Kelly sizing
        from backend.quant.entropy import quarter_kelly

        if side == Side.YES:
            f = quarter_kelly(result.ensemble_probability, market_state.yes_price)
        else:
            f = quarter_kelly(1.0 - result.ensemble_probability, market_state.no_price)
        size = min(abs(f) * self.bankroll, self.max_trade_usdc)
        if size < 1.0:
            return None

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
