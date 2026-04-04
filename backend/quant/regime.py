"""
Market regime classifier.

Takes a MarketState snapshot and returns the regime label plus a
prompt-template hint for regime-conditional LLM debate.

Regimes:
  - INFORMATION_DRIVEN: high volume + tight spread + new info arriving
  - CONSENSUS_GRIND:    moderate volume, narrow range, thin edge
  - ILLIQUID_NOISE:     low volume, wide spread — skip or micro-size
  - RESOLUTION_CLIFF:   <24h to resolve, time decay dominates

Uses only real MarketState fields. No mock lookups.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from backend.strategies.base import MarketState


class Regime(str, Enum):
    INFORMATION_DRIVEN = "information_driven"
    CONSENSUS_GRIND = "consensus_grind"
    ILLIQUID_NOISE = "illiquid_noise"
    RESOLUTION_CLIFF = "resolution_cliff"


@dataclass
class RegimeCall:
    regime: Regime
    confidence: float  # 0..1
    reasoning: str
    # Prompt orientation for the LLM debate — used by the specialist
    # orchestrator to pick which specialists to run and how to weight roles.
    emphasis_roles: tuple[str, ...]


def classify(market: MarketState) -> RegimeCall:
    """Classify a MarketState into one of four regimes.

    Thresholds calibrated for Polymarket specifically:
      - Typical Polymarket spreads are 2-8% (much wider than CEX).
      - Typical daily volumes span $100 to $100k+.
      - Liquidity (top-of-book depth) is often $1k-$20k.
    The old CEX-style thresholds (spread > 5% = illiquid) classified
    nearly every Polymarket market as ILLIQUID_NOISE and blocked trading.
    """
    vol = float(getattr(market, "volume_24h", 0) or 0)
    liq = float(getattr(market, "liquidity", 0) or 0)
    spread = float(getattr(market, "spread", 0) or 0)
    hours = float(getattr(market, "hours_to_close", float("inf")) or float("inf"))

    # Resolution cliff beats everything else
    if hours < 24:
        conf = min(1.0, (24 - hours) / 24)
        return RegimeCall(
            regime=Regime.RESOLUTION_CLIFF,
            confidence=conf,
            reasoning=f"Only {hours:.1f}h until resolution",
            emphasis_roles=("Time Decay Analyst", "Statistics Expert", "Moderator"),
        )

    # Illiquid noise — only truly untradeable markets. Anything with
    # meaningful liquidity and a spread below 12% is tradeable on
    # Polymarket even if daily volume is modest.
    if liq < 500 or spread > 0.12:
        return RegimeCall(
            regime=Regime.ILLIQUID_NOISE,
            confidence=0.8,
            reasoning=f"liq=${liq:.0f} spread={spread:.3f}",
            emphasis_roles=("Devil's Advocate",),
        )

    # Information-driven — real volume moving into the market, tight
    # enough spread that direction matters more than spread capture.
    # Polymarket threshold: $20k+ daily volume AND spread under 4%.
    if vol > 20_000 and spread < 0.04:
        return RegimeCall(
            regime=Regime.INFORMATION_DRIVEN,
            confidence=min(1.0, vol / 100_000),
            reasoning=f"High vol ${vol:,.0f}, tight spread {spread:.3f}",
            emphasis_roles=("Statistics Expert", "Crypto/Macro Analyst", "Moderator"),
        )

    # Default: consensus grind — moderate vol, tradeable spread. This
    # is where A-S earns its keep.
    return RegimeCall(
        regime=Regime.CONSENSUS_GRIND,
        confidence=0.6,
        reasoning=f"Moderate vol ${vol:,.0f}, spread {spread:.3f}",
        emphasis_roles=("Generalist Expert", "Devil's Advocate", "Moderator"),
    )


# ── Prompt fragments injected into the outer debate per regime ──────

REGIME_PROMPT_HINTS: dict[Regime, str] = {
    Regime.INFORMATION_DRIVEN: (
        "This market is INFORMATION-DRIVEN: recent volume and price "
        "movement suggest real new information is being priced in. "
        "Weight the Statistics Expert and Crypto/Macro Analyst heavily. "
        "Ask: has anything genuinely new happened, or is this momentum?"
    ),
    Regime.CONSENSUS_GRIND: (
        "This market is in CONSENSUS GRIND: moderate liquidity, narrow "
        "recent range, no obvious new information. Edges here are small "
        "and come from micro-mispricing or spread capture. Be skeptical "
        "of any large claimed edge — most will be noise."
    ),
    Regime.ILLIQUID_NOISE: (
        "This market is ILLIQUID NOISE: thin liquidity, wide spread. "
        "Any apparent edge is very likely a fill-realism problem, not a "
        "real mispricing. Default action should be HOLD."
    ),
    Regime.RESOLUTION_CLIFF: (
        "This market is at the RESOLUTION CLIFF: less than 24h until "
        "settlement. Time decay and resolution clarity dominate. The "
        "Time Decay Analyst should lead. Ignore long-horizon narratives."
    ),
}


def regime_prompt_hint(regime: Regime) -> str:
    return REGIME_PROMPT_HINTS.get(regime, "")
