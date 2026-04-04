"""
Specialist base class + gating helpers.

Each specialist is a single focused LLM call with:
  - its own dedicated system prompt
  - its own real data source (web search, Alchemy, Gamma, LLM swarm)
  - a structured SpecialistOpinion output (probability + confidence + rationale)

Specialists are only invoked on markets that clear the entropy gate
(|edge| > SPECIALIST_MIN_EDGE). All results are logged to
prediction_intelligence for the learning feedback loop.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from backend.config import settings
from backend.strategies.base import MarketState

logger = logging.getLogger(__name__)


@dataclass
class SpecialistOpinion:
    """Output of a single specialist run."""
    specialist: str
    market_id: str
    probability: float       # model's fair probability for YES
    confidence: float        # 0..1, how strongly the specialist believes its own answer
    rationale: str           # human-readable explanation
    weight: float = 0.0      # fusion weight assigned by orchestrator
    shadow: bool = False     # if True, weight becomes 0 at fusion (logged only)
    data_points: dict = field(default_factory=dict)  # structured real-data snippets
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    error: str = ""

    def as_log(self) -> dict:
        return {
            "specialist": self.specialist,
            "market_id": self.market_id,
            "probability": round(self.probability, 4),
            "confidence": round(self.confidence, 4),
            "weight": round(self.weight, 4),
            "shadow": self.shadow,
            "rationale": self.rationale[:500],
            "timestamp": self.timestamp.isoformat(),
        }


class Specialist(ABC):
    """Base class — every specialist implements .analyze()."""

    name: str = "base"

    @abstractmethod
    async def analyze(self, market: MarketState) -> Optional[SpecialistOpinion]:
        """Return an opinion or None on hard failure."""
        raise NotImplementedError


# ── Gating ──────────────────────────────────────────────────────

def entropy_edge_passes(market: MarketState) -> bool:
    """
    Gate: only run expensive specialists when the cheap entropy screener
    has flagged at least SPECIALIST_MIN_EDGE edge between the model's
    probability and the market-implied price.

    This keeps daily specialist cost in the ~$5-8 range instead of $50+.
    """
    min_edge = settings.specialists.min_edge
    model_p = float(getattr(market, "model_probability", 0) or 0)
    mid = float(getattr(market, "mid_price", 0) or getattr(market, "yes_price", 0) or 0)
    if model_p <= 0 or mid <= 0:
        return False
    edge = abs(model_p - mid)
    return edge >= min_edge


def format_market_context(market: MarketState) -> str:
    """Shared compact market description injected into every specialist prompt."""
    hours = getattr(market, "hours_to_close", float("inf"))
    hours_str = f"{hours:.1f}h" if hours < 1e6 else "unknown"
    return (
        f"Market: {market.question}\n"
        f"Category: {market.category}\n"
        f"Current YES price: {market.yes_price:.4f}  NO price: {market.no_price:.4f}\n"
        f"Spread: {market.spread:.4f}  Liquidity: ${market.liquidity:,.0f}  "
        f"Vol24h: ${market.volume_24h:,.0f}\n"
        f"Hours to resolution: {hours_str}\n"
        f"Model probability (screener): {market.model_probability:.4f}  "
        f"Entropy bits: {market.entropy_bits:.3f}  KL div: {market.kl_divergence:.4f}"
    )
