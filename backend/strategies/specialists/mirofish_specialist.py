"""
MiroFish Swarm Specialist.

Spawns N distinct LLM agents (default 500) with real, diverse personas
drawn from structured demographic / viewpoint distributions. Each agent
receives the market snapshot plus shared specialist context (news,
on-chain, history findings if available) and returns its own
probability estimate. The swarm's distribution is the signal.

This is a REAL multi-agent LLM swarm using real model calls — not a
seeded RNG. Each agent produces an independent LLM inference.

NOTE ON SHADOW MODE:
  When EXECUTION_MODE=paper (the default), this specialist runs in
  SHADOW mode — its predictions are fully computed and logged to the
  prediction_intelligence learning loop, but its fusion weight is
  forced to 0 so it does NOT influence the actual trade decision.
  This lets us accumulate real Brier-score history before giving the
  swarm any vote. The moment you flip to EXECUTION_MODE=live the
  configured MIROFISH_WEIGHT takes effect automatically.

NOTE ON IMPLEMENTATION:
  This is a lightweight multi-agent swarm inspired by MiroFish's OASIS
  framework. It does not implement the full 23-action social graph —
  each agent is independent (no follow/repost dynamics). If the
  shadow-mode backtest shows MiroFish adds real edge, upgrading to the
  full OASIS social simulation is a drop-in replacement at this layer.
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import re
import statistics
from typing import Optional

from backend.config import settings
from backend.strategies.base import MarketState
from backend.strategies.specialists.base import (
    Specialist,
    SpecialistOpinion,
    format_market_context,
)

logger = logging.getLogger(__name__)


# Diverse persona axes. Each agent is a combinatorial draw from these
# dimensions — this produces ~thousands of distinct perspectives before
# we even hit the LLM's own stochasticity.
AGE_BUCKETS = [
    "early-20s college student", "late-20s tech worker", "30s mid-career professional",
    "40s parent/home-owner", "50s experienced manager", "60s near-retiree",
    "70s retiree",
]
OCCUPATIONS = [
    "software engineer", "financial analyst", "retail trader", "academic researcher",
    "small-business owner", "journalist", "policy wonk", "healthcare worker",
    "lawyer", "teacher", "logistics manager", "marketing professional",
    "scientist", "nonprofit staffer", "sales rep", "day-trader",
    "civil servant", "consultant", "crypto native", "sports bettor",
]
WORLDVIEWS = [
    "rigorously Bayesian", "instinct-driven contrarian", "momentum follower",
    "fundamentals-only fundamentalist", "narrative-sensitive", "data-skeptical",
    "news-junkie", "historically-minded", "macro-first thinker",
    "micro-detail obsessive", "process-oriented", "outcome-oriented",
    "risk-averse conservative", "calculated risk-taker",
]
RISK_PROFILES = ["very risk-averse", "moderately cautious", "balanced", "moderately aggressive", "highly speculative"]
INFO_DIETS = [
    "mainstream news only", "heavy on Twitter/X", "academic papers",
    "niche subreddits", "industry publications", "podcasts and long-form",
    "direct primary sources", "aggregators and newsletters",
]


SYSTEM_PROMPT = """You are a single simulated human participant in a large
forecasting swarm evaluating a Polymarket prediction market. Your persona
is described below — answer AS that persona, not as a neutral AI.

Read the market and the shared specialist findings (if any). Give your
best honest probability for YES based on how YOUR persona would reason.
Do not hedge toward the market price unless your persona has no view.

Respond with ONE compact JSON object, nothing else:
{"p": 0.XX, "c": 0.XX, "why": "one short sentence"}

Where p = your probability YES, c = your confidence 0..1."""


class MiroFishSpecialist(Specialist):
    name = "mirofish"

    def __init__(self) -> None:
        self._client = None
        self._api_key = settings.ai.openai_api_key
        self._cfg = settings.specialists
        self._rng = random.Random()

    def _get_client(self):
        if self._client is not None:
            return self._client
        if not self._api_key:
            return None
        try:
            import openai
            self._client = openai.AsyncOpenAI(api_key=self._api_key)
            return self._client
        except ImportError:
            logger.error("openai SDK not installed — MiroFishSpecialist disabled")
            return None

    async def analyze(
        self,
        market: MarketState,
        shared_context: Optional[str] = None,
    ) -> Optional[SpecialistOpinion]:
        if not self._cfg.mirofish_enabled:
            return None
        client = self._get_client()
        if client is None:
            return None

        n_agents = self._cfg.mirofish_agents
        max_concurrency = self._cfg.mirofish_max_concurrency
        model = self._cfg.mirofish_model

        # Decide shadow vs active based on current execution mode
        shadow = self._is_shadow_mode()

        personas = [self._build_persona(i) for i in range(n_agents)]
        market_block = format_market_context(market)
        context_block = f"\n\nShared specialist findings:\n{shared_context}" if shared_context else ""

        sem = asyncio.Semaphore(max_concurrency)

        async def run_agent(persona: str) -> Optional[dict]:
            async with sem:
                try:
                    resp = await client.chat.completions.create(
                        model=model,
                        max_tokens=120,
                        temperature=0.9,
                        messages=[
                            {"role": "system", "content": SYSTEM_PROMPT},
                            {"role": "user", "content": (
                                f"PERSONA: {persona}\n\n"
                                f"MARKET:\n{market_block}"
                                f"{context_block}\n\n"
                                "Respond with ONE JSON object as instructed."
                            )},
                        ],
                    )
                    text = resp.choices[0].message.content or ""
                    data = _extract_json(text)
                    if not data or "p" not in data:
                        return None
                    p = _clip(float(data["p"]))
                    c = _clip(float(data.get("c", 0.5)))
                    return {"p": p, "c": c, "persona": persona[:80]}
                except Exception as e:
                    logger.debug(f"MiroFish agent failed: {e}")
                    return None

        results = await asyncio.gather(*[run_agent(p) for p in personas])
        votes = [r for r in results if r is not None]

        if len(votes) < max(20, n_agents // 20):
            # Too few agents responded for the distribution to mean anything
            return SpecialistOpinion(
                specialist=self.name,
                market_id=market.market_id,
                probability=market.yes_price,
                confidence=0.0,
                rationale=f"Only {len(votes)}/{n_agents} agents returned valid votes",
                shadow=shadow,
                data_points={"valid_votes": len(votes), "attempted": n_agents},
            )

        probs = [v["p"] for v in votes]
        confidences = [v["c"] for v in votes]
        mean_p = statistics.fmean(probs)
        median_p = statistics.median(probs)
        stdev_p = statistics.pstdev(probs)
        mean_conf = statistics.fmean(confidences)

        # Swarm confidence: agents agreeing (low stdev) AND individually
        # confident AND producing a probability meaningfully different
        # from the market price.
        agreement = max(0.0, 1.0 - stdev_p / 0.35)  # 0.35 = very disagreeing
        divergence = min(1.0, abs(mean_p - market.yes_price) * 3)
        swarm_conf = _clip((agreement * 0.5 + mean_conf * 0.3 + divergence * 0.2))

        return SpecialistOpinion(
            specialist=self.name,
            market_id=market.market_id,
            probability=mean_p,
            confidence=swarm_conf,
            rationale=(
                f"Swarm of {len(votes)} agents: mean={mean_p:.3f} "
                f"median={median_p:.3f} stdev={stdev_p:.3f} "
                f"agreement={agreement:.2f} divergence_from_market={divergence:.2f}"
            ),
            shadow=shadow,
            data_points={
                "n_agents": len(votes),
                "n_attempted": n_agents,
                "mean": round(mean_p, 4),
                "median": round(median_p, 4),
                "stdev": round(stdev_p, 4),
                "agreement": round(agreement, 4),
                "market_divergence": round(divergence, 4),
                "model": model,
                "shadow_mode": shadow,
            },
        )

    def _build_persona(self, seed: int) -> str:
        """Deterministic per-seed persona so runs are reproducible per market."""
        r = random.Random(seed * 2654435761 & 0xFFFFFFFF)
        age = r.choice(AGE_BUCKETS)
        occ = r.choice(OCCUPATIONS)
        wv = r.choice(WORLDVIEWS)
        risk = r.choice(RISK_PROFILES)
        diet = r.choice(INFO_DIETS)
        return f"{age}, {occ}, {wv}, {risk}, information diet: {diet}"

    @staticmethod
    def _is_shadow_mode() -> bool:
        """MiroFish is in shadow mode whenever paper trading is active.
        It flips to active automatically when EXECUTION_MODE=live."""
        try:
            from backend.state import system_state
            return bool(getattr(system_state, "paper_trading", True))
        except Exception:
            return True  # default to shadow on any error


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
    brace = re.search(r"\{.*?\}", text, re.DOTALL)
    if brace:
        try:
            return json.loads(brace.group(0))
        except json.JSONDecodeError:
            return None
    return None
