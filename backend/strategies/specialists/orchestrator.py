"""
Specialist Orchestrator.

Responsibilities:
  1. Gate — only run specialists on markets where the entropy screener
     has flagged |edge| > SPECIALIST_MIN_EDGE (default 0.05).
  2. Dedupe — at most one specialist run per (market_id, specialist)
     per SPECIALIST_DEDUPE_MINUTES window.
  3. Classify regime and inject the regime prompt hint into context.
  4. Run news + on-chain + history specialists in parallel (fast).
  5. Build shared specialist context from their results.
  6. Run MiroFish swarm with the shared context in its prompt.
  7. Log every SpecialistOpinion to prediction_intelligence for the
     learning loop.
  8. Return a SpecialistBundle that EnsembleAI can fuse with the outer
     Claude + GPT-4o debates.

Shadow mode: MiroFish opinions are always computed and logged, but
their weight is forced to 0 whenever paper trading is active. The
other three specialists are live in both paper and real modes.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Optional

from backend.config import settings
from backend.quant.regime import classify as classify_regime, regime_prompt_hint, RegimeCall
from backend.strategies.base import MarketState
from backend.strategies.specialists.base import (
    SpecialistOpinion,
    entropy_edge_passes,
)
from backend.strategies.specialists.news_specialist import NewsSpecialist
from backend.strategies.specialists.onchain_specialist import OnChainSpecialist
from backend.strategies.specialists.history_specialist import HistorySpecialist
from backend.strategies.specialists.mirofish_specialist import MiroFishSpecialist

logger = logging.getLogger(__name__)


# Live-mode weights for specialists in the outer ensemble fusion.
# MiroFish's weight is read from config and forced to 0 in shadow mode.
SPECIALIST_WEIGHTS_LIVE = {
    "news": 0.20,
    "onchain": 0.10,
    "history": 0.15,
}


@dataclass
class SpecialistBundle:
    """Everything the outer ensemble needs from the specialist layer."""
    market_id: str
    regime: RegimeCall
    opinions: list[SpecialistOpinion] = field(default_factory=list)
    context_for_outer_debate: str = ""
    fused_probability: Optional[float] = None
    fused_confidence: float = 0.0

    def any_active_vote(self) -> bool:
        return any(o.weight > 0 for o in self.opinions)


class SpecialistOrchestrator:
    """Fires gated, deduped specialist runs and fuses their opinions."""

    def __init__(self) -> None:
        self.news = NewsSpecialist()
        self.onchain = OnChainSpecialist()
        self.history = HistorySpecialist()
        self.mirofish = MiroFishSpecialist()
        # (market_id, specialist_name) -> last run timestamp
        self._last_run: dict[tuple[str, str], datetime] = {}
        # Optional decision logger injection (set by scheduler)
        self._decision_logger = None

    def attach_decision_logger(self, decision_logger) -> None:
        """Called by scheduler so specialists feed the learning loop."""
        self._decision_logger = decision_logger

    async def analyze(self, market: MarketState) -> Optional[SpecialistBundle]:
        """Main entry point. Returns None if the market fails the gate."""
        if not entropy_edge_passes(market):
            logger.debug(
                f"Specialist gate: {market.market_id[:10]} skipped "
                f"(edge below {settings.specialists.min_edge})"
            )
            return None

        regime = classify_regime(market)
        # Skip illiquid-noise regime entirely — not worth specialist budget
        from backend.quant.regime import Regime
        if regime.regime == Regime.ILLIQUID_NOISE:
            logger.debug(f"Specialist gate: {market.market_id[:10]} illiquid noise, skipped")
            return None

        bundle = SpecialistBundle(market_id=market.market_id, regime=regime)

        # Run the three cheap specialists in parallel
        phase1 = await asyncio.gather(
            self._run_if_due(self.news, market),
            self._run_if_due(self.onchain, market),
            self._run_if_due(self.history, market),
            return_exceptions=True,
        )
        for item in phase1:
            if isinstance(item, SpecialistOpinion):
                bundle.opinions.append(item)
            elif isinstance(item, Exception):
                logger.warning(f"Specialist phase 1 exception: {item}")

        # Build shared context for MiroFish from phase 1 findings
        shared_context = self._build_shared_context(bundle)
        bundle.context_for_outer_debate = self._build_outer_context(bundle)

        # Phase 2: MiroFish swarm (expensive, uses shared context)
        mf_opinion = await self._run_mirofish_if_due(market, shared_context)
        if mf_opinion is not None:
            bundle.opinions.append(mf_opinion)

        # Assign fusion weights
        self._assign_weights(bundle)

        # Fuse into a single specialist probability
        self._fuse(bundle, market)

        # Log every opinion to the learning loop
        self._log_to_intelligence(bundle)

        return bundle

    async def _run_if_due(self, specialist, market: MarketState) -> Optional[SpecialistOpinion]:
        """Run a specialist respecting the dedupe window."""
        key = (market.market_id, specialist.name)
        now = datetime.now(timezone.utc)
        last = self._last_run.get(key)
        if last is not None:
            delta = now - last
            if delta < timedelta(minutes=settings.specialists.dedupe_minutes):
                return None
        try:
            opinion = await specialist.analyze(market)
        except Exception as e:
            logger.warning(f"{specialist.name} specialist raised: {e}")
            return None
        if opinion is not None:
            self._last_run[key] = now
        return opinion

    async def _run_mirofish_if_due(
        self,
        market: MarketState,
        shared_context: str,
    ) -> Optional[SpecialistOpinion]:
        if not settings.specialists.mirofish_enabled:
            return None
        key = (market.market_id, "mirofish")
        now = datetime.now(timezone.utc)
        last = self._last_run.get(key)
        if last is not None:
            delta = now - last
            if delta < timedelta(minutes=settings.specialists.dedupe_minutes):
                return None
        try:
            opinion = await self.mirofish.analyze(market, shared_context=shared_context)
        except Exception as e:
            logger.warning(f"MiroFish specialist raised: {e}")
            return None
        if opinion is not None:
            self._last_run[key] = now
        return opinion

    def _build_shared_context(self, bundle: SpecialistBundle) -> str:
        """Compact summary of phase-1 findings for MiroFish to read."""
        if not bundle.opinions:
            return ""
        parts = []
        for o in bundle.opinions:
            parts.append(
                f"- {o.specialist.upper()} (p={o.probability:.3f} c={o.confidence:.2f}): "
                f"{o.rationale[:200]}"
            )
        return "\n".join(parts)

    def _build_outer_context(self, bundle: SpecialistBundle) -> str:
        """Context block injected into the outer Claude+GPT-4o debate prompt."""
        lines = [
            f"REGIME: {bundle.regime.regime.value} (confidence {bundle.regime.confidence:.2f})",
            f"  — {bundle.regime.reasoning}",
            regime_prompt_hint(bundle.regime.regime),
        ]
        if bundle.opinions:
            lines.append("\nSPECIALIST FINDINGS (real-data agents):")
            for o in bundle.opinions:
                shadow_tag = " [SHADOW]" if o.shadow else ""
                lines.append(
                    f"  • {o.specialist}{shadow_tag}: p={o.probability:.3f} "
                    f"c={o.confidence:.2f} — {o.rationale[:220]}"
                )
                if o.data_points:
                    for k in ("freshness_score", "new_info_detected", "flow_interpretation",
                              "net_flow_usd", "crowd_hit_rate", "avg_final_edge",
                              "n_agents", "agreement"):
                        if k in o.data_points and o.data_points[k] is not None:
                            lines.append(f"      - {k}: {o.data_points[k]}")
        return "\n".join(lines)

    def _assign_weights(self, bundle: SpecialistBundle) -> None:
        """Assign fusion weights. Zero weight in shadow mode for MiroFish."""
        mf_weight_cfg = settings.specialists.mirofish_weight
        for o in bundle.opinions:
            if o.specialist == "mirofish":
                # Shadow mode → 0 weight regardless of config
                o.weight = 0.0 if o.shadow else mf_weight_cfg
            else:
                o.weight = SPECIALIST_WEIGHTS_LIVE.get(o.specialist, 0.0)
            # Scale weight by confidence so low-confidence opinions contribute less
            o.weight *= max(0.1, o.confidence)

    def _fuse(self, bundle: SpecialistBundle, market: MarketState) -> None:
        total_w = sum(o.weight for o in bundle.opinions)
        if total_w <= 0:
            bundle.fused_probability = None
            bundle.fused_confidence = 0.0
            return
        weighted = sum(o.probability * o.weight for o in bundle.opinions) / total_w
        bundle.fused_probability = weighted
        # Confidence is the average per-opinion confidence, weighted
        bundle.fused_confidence = (
            sum(o.confidence * o.weight for o in bundle.opinions) / total_w
        )

    def _log_to_intelligence(self, bundle: SpecialistBundle) -> None:
        if self._decision_logger is None:
            return
        try:
            # Stash specialist opinions on the DecisionLogger for the
            # retrospective analyzer via a side-channel table. We use the
            # existing `evidence_items` field on DecisionRecord at
            # decision-log time (in scheduler._log_decision) to carry the
            # fused summary forward. Here we just emit a log line that
            # the analyzer can parse.
            for o in bundle.opinions:
                logger.info(f"SPECIALIST_OPINION {o.as_log()}")
        except Exception as e:
            logger.debug(f"Specialist log-to-intelligence failed: {e}")


# Module singleton
_orch: Optional[SpecialistOrchestrator] = None


def get_specialist_orchestrator() -> SpecialistOrchestrator:
    global _orch
    if _orch is None:
        _orch = SpecialistOrchestrator()
    return _orch
