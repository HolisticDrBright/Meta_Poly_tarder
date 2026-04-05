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

# Polymarket charges a 2% taker fee on winnings (not stakes). For a bet
# priced at `market_price` that pays off with probability `p`, the
# per-dollar fee cost is approximately:
#
#     fee_cost_per_dollar ≈ fee * p * (1 - market_price)
#
# For a small-edge trade symmetric around 0.5, that's ≈ fee / 4 ≈ 0.5%,
# not the full 2%. The previous version of this module applied the
# full 2% as a flat haircut, which was double-counting and made the
# EV gate reject almost every Polymarket opportunity.
POLYMARKET_WINNINGS_FEE = 0.02

# Assumed slippage beyond the visible best-bid/ask on paper fills.
# Kept at 0.003 for the EV gate (entry decisions). The friend's
# "bid-0.01" recommendation was about EXIT sell pricing for live
# orders, not the entry EV gate. Raising this too high starves A-S
# of all opportunities (14/14 markets rejected by EV gate).
DEFAULT_EXPECTED_SLIPPAGE = 0.003


# ── 1. EV gate for directional strategies ──────────────────

def expected_fee_cost(fair_probability: float, market_price: float, fee: float = POLYMARKET_WINNINGS_FEE) -> float:
    """
    Fee cost per dollar staked, accounting for Polymarket charging
    `fee` on winnings (not stakes). Symmetric in side: if you buy YES
    at p and expect to win with probability fair, the fee per $ is
    fee * fair * (1 - p).
    """
    fair = max(0.001, min(0.999, fair_probability))
    mkt = max(0.001, min(0.999, market_price))
    return fee * fair * (1.0 - mkt)


def ev_gate_passes(
    fair_probability: float,
    market_price: float,
    spread: float,
    fee: float = POLYMARKET_WINNINGS_FEE,
    extra_slippage: float = DEFAULT_EXPECTED_SLIPPAGE,
) -> bool:
    """
    Return True only if the directional edge is large enough to cover
    fees (on winnings), half the spread (taker crossing the book), and
    expected slippage.

        required_edge = fee_cost_on_winnings + spread/2 + slippage
        signed_edge   = |fair_p − market_p|

    Hard filter — negative-EV trades are rejected, not scaled.
    """
    edge = abs(fair_probability - market_price)
    fee_cost = expected_fee_cost(fair_probability, market_price, fee)
    required = fee_cost + (spread / 2.0) + extra_slippage
    return edge >= required


def required_edge_for_market(
    fair_probability: float,
    market_price: float,
    spread: float,
    fee: float = POLYMARKET_WINNINGS_FEE,
) -> float:
    """Minimum directional edge a strategy needs before the trade is EV+."""
    return (
        expected_fee_cost(fair_probability, market_price, fee)
        + (spread / 2.0)
        + DEFAULT_EXPECTED_SLIPPAGE
    )


# ── 1b. EV gate for market makers ──────────────────────────
#
# A market maker's edge is NOT a fair-vs-market mispricing — it's the
# spread it captures by providing liquidity on both sides. A-S with
# zero inventory produces fair_p == yes_price, giving signed edge = 0,
# which incorrectly fails ev_gate_passes. Market makers need their
# own EV check:
#
#     expected_capture = spread_captured * fill_rate_estimate
#     required         = adverse_selection + fee_on_winnings + slippage
#
# For a symmetric quoted spread of `s` around the mid, the MM earns
# roughly s/2 per round-trip fill (capture half the spread on each
# side). That must exceed the 2% fee on winnings (≈ fee * 0.25 per $
# for mid-priced markets) plus expected adverse selection and slippage.

def mm_ev_gate_passes(
    quoted_spread: float,
    market_price: float,
    fee: float = POLYMARKET_WINNINGS_FEE,
    extra_slippage: float = DEFAULT_EXPECTED_SLIPPAGE,
    adverse_selection_bps: float = 30.0,
) -> bool:
    """
    EV check for market makers. The MM captures (quoted_spread / 2) per
    round-trip fill on average. That must cover fees + slippage +
    adverse selection (the cost of being picked off when a toxic trader
    hits your quote).

        capture   = quoted_spread / 2
        required  = fee*p*(1-p) + slippage + adverse_selection
        passes    = capture >= required
    """
    capture = quoted_spread / 2.0
    # Fee on winnings is symmetric: at the mid, winning_prob ≈ market_p
    # and payoff ≈ (1 - market_p), so fee_cost ≈ fee * p * (1-p).
    mkt = max(0.001, min(0.999, market_price))
    fee_cost = fee * mkt * (1.0 - mkt)
    adverse = adverse_selection_bps / 10000.0
    required = fee_cost + extra_slippage + adverse
    return capture >= required


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
    # Information-driven: real volume, tight-ish spread, news flowing.
    # Directional strategies dominate (ensemble, entropy). Binance arb
    # runs in every regime because its edge is venue-agnostic — a
    # lagging Polymarket vs realtime Binance spot is always tradeable.
    Regime.INFORMATION_DRIVEN: {
        StrategyName.AVELLANEDA,
        StrategyName.ENTROPY,
        StrategyName.ENSEMBLE_AI,
        StrategyName.ARB,
        StrategyName.BINANCE_ARB,
        StrategyName.JET,
        StrategyName.COPY,
    },

    # Consensus-grind: slow drift + micro-mispricing. A-S allowed but
    # disabled at config level (unprofitable on Polymarket spreads).
    Regime.CONSENSUS_GRIND: {
        StrategyName.AVELLANEDA,
        StrategyName.ARB,
        StrategyName.BINANCE_ARB,
        StrategyName.ENTROPY,
        StrategyName.ENSEMBLE_AI,
        StrategyName.COPY,
    },

    # Resolution cliff: <24h to resolve. Theta harvester dominates;
    # arb if the book is crossed. Binance arb also fires here — a
    # crypto market with an hour left that disagrees with Binance spot
    # is the highest-confidence arb we can take.
    Regime.RESOLUTION_CLIFF: {
        StrategyName.THETA,
        StrategyName.ARB,
        StrategyName.BINANCE_ARB,
    },

    # Illiquid noise: only truly untradeable markets land here now
    # (spread > 12% or liq < $500). Everything is blocked.
    Regime.ILLIQUID_NOISE: set(),
}


def regime_allows_strategy(regime: Regime, strategy: StrategyName) -> bool:
    """Policy lookup: is this strategy allowed to fire in this regime?"""
    allowed = REGIME_STRATEGY_POLICY.get(regime, set())
    return strategy in allowed
