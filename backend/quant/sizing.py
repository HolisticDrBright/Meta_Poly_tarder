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

from backend.quant.entropy import kelly_fraction, empirical_kelly
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
# Polymarket fee structure (updated Apr 2026):
#   - Dynamic taker fees: peaks at ~3.15% near 50¢ prices on most categories
#   - Maker rebates: 25-50% depending on category
#   - Geopolitics: FEE-FREE (the only zero-fee category)
#   - The old flat 2% on winnings is now just the base — actual cost depends
#     on price and whether you're taking or making liquidity.
POLYMARKET_BASE_FEE = 0.02
POLYMARKET_MAX_TAKER_FEE = 0.0315  # 3.15% peak at 50¢

# Fee-free categories where taker fees don't apply
FEE_FREE_CATEGORIES = frozenset({"geopolitics", "politics"})

# Categories with higher maker rebates (50% instead of 25%)
HIGH_REBATE_CATEGORIES = frozenset({"finance", "crypto"})

# Assumed slippage beyond the visible best-bid/ask on paper fills.
DEFAULT_EXPECTED_SLIPPAGE = 0.003


def dynamic_taker_fee(market_price: float, category: str = "") -> float:
    """Compute the actual taker fee based on price and category.

    Polymarket's dynamic fee curve peaks near 50¢ (max uncertainty).
    Geopolitics is fee-free. Other categories follow a parabolic curve.
    """
    cat = category.lower().strip()
    if any(fc in cat for fc in FEE_FREE_CATEGORIES):
        return 0.0

    # Parabolic fee curve: peaks at 50¢, zero at 0¢ and 100¢
    # fee(p) = max_fee * 4 * p * (1 - p)
    p = max(0.001, min(0.999, market_price))
    return POLYMARKET_MAX_TAKER_FEE * 4.0 * p * (1.0 - p)


# ── 1. EV gate for directional strategies ──────────────────

def expected_fee_cost(
    fair_probability: float,
    market_price: float,
    fee: float = POLYMARKET_BASE_FEE,
    category: str = "",
) -> float:
    """
    Fee cost per dollar staked, using the dynamic fee curve.

    For fee-free categories (geopolitics), returns 0.
    For others, uses the price-dependent taker fee.
    """
    fair = max(0.001, min(0.999, fair_probability))
    mkt = max(0.001, min(0.999, market_price))
    actual_fee = dynamic_taker_fee(mkt, category) if category else fee
    return actual_fee * fair * (1.0 - mkt)


def ev_gate_passes(
    fair_probability: float,
    market_price: float,
    spread: float,
    fee: float = POLYMARKET_BASE_FEE,
    extra_slippage: float = DEFAULT_EXPECTED_SLIPPAGE,
    category: str = "",
) -> bool:
    """
    Return True only if the directional edge is large enough to cover
    fees (dynamic, based on price + category), half the spread, and
    expected slippage.

    Geopolitics markets pass more easily since they're fee-free.
    """
    edge = abs(fair_probability - market_price)
    fee_cost = expected_fee_cost(fair_probability, market_price, fee, category)
    required = fee_cost + (spread / 2.0) + extra_slippage
    return edge >= required


def required_edge_for_market(
    fair_probability: float,
    market_price: float,
    spread: float,
    fee: float = POLYMARKET_BASE_FEE,
    category: str = "",
) -> float:
    """Minimum directional edge a strategy needs before the trade is EV+."""
    return (
        expected_fee_cost(fair_probability, market_price, fee, category)
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
    fee: float = POLYMARKET_BASE_FEE,
    extra_slippage: float = DEFAULT_EXPECTED_SLIPPAGE,
    adverse_selection_bps: float = 30.0,
    category: str = "",
) -> bool:
    """
    EV check for market makers. Makers pay ZERO taker fees and get
    25-50% rebates on most categories (Apr 2026 fee structure).

    The MM captures (quoted_spread / 2) per round-trip fill on average.
    That must cover adverse selection + slippage. Fee cost is zero for
    makers (they earn rebates instead), so the gate is easier to pass
    than the taker EV gate.
    """
    capture = quoted_spread / 2.0
    # Makers pay no taker fee — they get rebates instead.
    # Only adverse selection + slippage need to be covered.
    adverse = adverse_selection_bps / 10000.0
    required = extra_slippage + adverse
    return capture >= required


# ── 2. Kelly sizing ─────────────────────────────────────────

def kelly_size_usdc(
    fair_probability: float,
    market_price: float,
    bankroll: float,
    kelly_fraction_multiplier: float = 0.25,
    max_trade_usdc: float = 30.0,
    min_trade_usdc: float = 1.0,
    edge_variance: float = 0.0,
) -> float:
    """
    Edge-proportional position size via Empirical Kelly.

    Upgrade from standard fractional Kelly: when edge_variance > 0,
    applies the Empirical Kelly penalty (1 - CV_edge) to reduce sizing
    on uncertain edges. When edge_variance=0 (default), behaves exactly
    like standard quarter-Kelly.

    Flow:
      1. Compute Empirical Kelly fraction from (fair_p, market_p, variance).
      2. Apply to bankroll.
      3. Clamp to [min_trade_usdc, max_trade_usdc].
      4. If Kelly is <= 0 (no edge), return 0.
    """
    f = empirical_kelly(fair_probability, market_price, edge_variance, kelly_fraction_multiplier)
    if f <= 0:
        return 0.0
    sized = bankroll * f
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
