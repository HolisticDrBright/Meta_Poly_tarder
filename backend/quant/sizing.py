"""
Bet sizing + edge gating helpers shared by every strategy.

Three concerns live here:

  1. ev_gate_passes()  — refuses trades whose expected value is smaller
     than fees + half-spread. On Polymarket's 2% profit fee this alone
     eliminates a large chunk of structurally losing thin-edge trades.

  2. kelly_size_usdc() — turns a (fair_p, market_p, bankroll) triple
     into an edge-proportional USDC position size using fractional
     Kelly, bounded by the risk engine's per-trade cap.

  3. regime_allows_strategy() — given a Regime and a StrategyName,
     returns whether that strategy should even attempt to fire in that
     regime. Used by the scheduler to skip whole strategies in regimes
     where they're known to be negative-EV (e.g. A-S in consensus-grind,
     theta in information-driven, everything in illiquid-noise).

All three are pure functions, no side effects — safe to call from any
strategy and trivially unit-testable.
"""

from __future__ import annotations

from typing import Optional

from backend.quant.entropy import kelly_fraction
from backend.quant.regime import Regime
from backend.strategies.base import StrategyName


# ── Fee constants ───────────────────────────────────────────

# Polymarket charges 2% on realized profit. We model this as a 1%
# round-trip haircut on the expected edge (half on entry, half on exit)
# since the fee is only on the profit slice, not the stake.
POLYMARKET_FEE_ROUND_TRIP = 0.02

# Assumed slippage beyond the visible best-bid/ask on paper fills.
# Real CLOB slippage is observed empirically from the trade log once
# the learning loop has enough outcomes.
DEFAULT_EXPECTED_SLIPPAGE = 0.005


# ── 1. EV gate ──────────────────────────────────────────────

def ev_gate_passes(
    fair_probability: float,
    market_price: float,
    spread: float,
    fee: float = POLYMARKET_FEE_ROUND_TRIP,
    extra_slippage: float = DEFAULT_EXPECTED_SLIPPAGE,
) -> bool:
    """
    Return True only if the signed edge is large enough to cover fees,
    half the spread, and expected slippage.

        required_edge = fee + spread/2 + slippage
        signed_edge   = |fair_p − market_p|

    This is a hard filter. Any trade proposal where the edge is smaller
    than the breakeven threshold is rejected outright — not scaled down.
    Scaling down a negative-EV trade still produces a negative-EV trade,
    just smaller. Better to skip.
    """
    edge = abs(fair_probability - market_price)
    required = fee + (spread / 2.0) + extra_slippage
    return edge >= required


def required_edge_for_market(spread: float, fee: float = POLYMARKET_FEE_ROUND_TRIP) -> float:
    """Minimum edge a strategy needs before any trade on this market is EV+."""
    return fee + (spread / 2.0) + DEFAULT_EXPECTED_SLIPPAGE


# ── 2. Kelly sizing ─────────────────────────────────────────

def kelly_size_usdc(
    fair_probability: float,
    market_price: float,
    bankroll: float,
    kelly_fraction_multiplier: float = 0.25,
    max_trade_usdc: float = 30.0,
    min_trade_usdc: float = 1.0,
) -> float:
    """
    Edge-proportional position size via fractional Kelly.

    Flow:
      1. Compute raw Kelly fraction f* from (fair_p, market_p).
      2. Scale down by kelly_fraction_multiplier (default 0.25 = quarter-Kelly).
      3. Apply to bankroll.
      4. Clamp to [min_trade_usdc, max_trade_usdc].
      5. If Kelly is <= 0 (no edge or negative edge), return 0 — the
         caller should not open a position.

    Kelly can produce tiny sizes when the edge is real but small. That's
    the whole point: bet proportional to edge, not flat. A big edge gets
    a big position, a tiny edge gets a tiny position. The max cap
    protects against model over-confidence.
    """
    f = kelly_fraction(fair_probability, market_price)
    if f <= 0:
        return 0.0
    sized = bankroll * f * kelly_fraction_multiplier
    sized = min(sized, max_trade_usdc)
    if sized < min_trade_usdc:
        return 0.0
    return round(sized, 2)


# ── 3. Regime-conditional strategy activation ──────────────

# Which strategies should run in each regime. Missing = disabled.
# This is the policy file — tune based on the learning loop's per-regime
# performance reports once enough outcomes accumulate.
REGIME_STRATEGY_POLICY: dict[Regime, set[StrategyName]] = {
    # Information-driven: news + real mispricing. Run the ensemble +
    # entropy screener. Skip market making (A-S) because spreads are
    # tight and the edge is in direction, not in spread capture.
    Regime.INFORMATION_DRIVEN: {
        StrategyName.ENTROPY,
        StrategyName.ENSEMBLE_AI,
        StrategyName.ARB,
        StrategyName.JET,
        StrategyName.COPY,
    },

    # Consensus-grind: range-bound, moderate volume, no news. A-S and
    # arb only — spread capture is the only edge here. Skip the
    # ensemble because there's nothing for it to reason about.
    Regime.CONSENSUS_GRIND: {
        StrategyName.AVELLANEDA,
        StrategyName.ARB,
        StrategyName.ENTROPY,
    },

    # Resolution cliff: <24h to resolve. Theta harvester dominates;
    # skip long-horizon strategies.
    Regime.RESOLUTION_CLIFF: {
        StrategyName.THETA,
        StrategyName.ARB,
    },

    # Illiquid noise: wide spreads, low volume. Don't trade at all —
    # any "edge" is fill-realism noise. Empty set = everything skipped.
    Regime.ILLIQUID_NOISE: set(),
}


def regime_allows_strategy(regime: Regime, strategy: StrategyName) -> bool:
    """Policy lookup: is this strategy allowed to fire in this regime?"""
    allowed = REGIME_STRATEGY_POLICY.get(regime, set())
    return strategy in allowed
