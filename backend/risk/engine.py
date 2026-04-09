"""
Risk Engine — all pre-trade risk checks.

Kill switches and limits:
  - Max portfolio exposure (% of bankroll)
  - Max single market concentration
  - Max daily loss
  - Max single trade size
  - Minimum balance reserve
  - Paper trading override
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

from backend.strategies.base import OrderIntent, Position, ScoredIntent

logger = logging.getLogger(__name__)


@dataclass
class RiskState:
    """Current risk state of the portfolio."""

    balance: float = 10_000.0
    total_exposure: float = 0.0
    daily_pnl: float = 0.0
    # Cumulative realized P&L snapshot at the start of the current
    # trading day. `daily_pnl = system_state.realized_pnl - baseline`,
    # so a fresh day always starts at 0 regardless of prior drawdown.
    # Without this, the risk engine reads cumulative P&L as "daily"
    # and the 10% daily-loss limit locks up the bot permanently on
    # first drawdown.
    daily_pnl_baseline: float = 0.0
    positions: list[Position] = field(default_factory=list)
    trades_today: int = 0
    last_reset: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def exposure_pct(self) -> float:
        if self.balance <= 0:
            return 1.0
        return self.total_exposure / self.balance

    def market_exposure(self, market_id: str) -> float:
        return sum(p.size_usdc for p in self.positions if p.market_id == market_id)

    def market_exposure_pct(self, market_id: str) -> float:
        if self.balance <= 0:
            return 1.0
        return self.market_exposure(market_id) / self.balance


@dataclass
class RiskCheckResult:
    approved: bool
    reason: str = ""
    adjusted_size: float = 0.0


class RiskEngine:
    """Pre-trade risk management."""

    def __init__(
        self,
        max_portfolio_exposure: float = 0.75,
        max_single_market_pct: float = 0.10,
        max_daily_loss_pct: float = 0.15,
        max_trade_size_usdc: float = 15,
        min_balance_usdc: float = 10,
        paper_trading: bool = True,
    ) -> None:
        self.max_portfolio_exposure = max_portfolio_exposure
        self.max_single_market_pct = max_single_market_pct
        self.max_daily_loss_pct = max_daily_loss_pct
        self.max_trade_size_usdc = max_trade_size_usdc
        self.min_balance_usdc = min_balance_usdc
        self.paper_trading = paper_trading
        # Internal state used only for daily-loss counters. Concentration
        # and exposure checks read from system_state directly so they
        # always see the real open positions (see _sync_from_system_state).
        self.state = RiskState()
        self._kill_switch = False

    def _sync_from_system_state(self) -> None:
        """Pull fresh balance + positions from the shared system_state
        so concentration and exposure checks are computed against the
        real open book, not a stale in-engine snapshot.

        `daily_pnl` is computed as the delta between the current
        cumulative realized P&L and the snapshot taken at the last
        daily reset, so the 10% daily-loss limit is actually per-day
        and not all-time.
        """
        try:
            from backend.state import system_state
            # Use the ACTUAL current balance for exposure checks, not
            # starting_capital. This ensures the risk engine respects
            # the real cash available (shrinks limits on drawdown,
            # expands them as the bot profits).
            real_balance = getattr(system_state, "balance", 0) or 0
            if real_balance > 0:
                self.state.balance = float(real_balance)
            else:
                sc = getattr(system_state, "starting_capital", 300)
                self.state.balance = float(sc) if sc and sc > 0 else 300.0
            self.state.positions = list(system_state.positions)
            self.state.total_exposure = sum(p.size_usdc for p in self.state.positions)
            cumulative = float(getattr(system_state, "realized_pnl", 0) or 0)
            unrealized = float(getattr(system_state, "unrealized_pnl", 0) or 0)
            # Include unrealized losses in the daily loss check so a
            # position that's down 50% triggers the limit even before
            # it's closed. Only count unrealized if it's negative (losses).
            total_daily = (cumulative - self.state.daily_pnl_baseline) + min(0, unrealized)
            self.state.daily_pnl = total_daily
        except Exception:
            pass

    def kill(self) -> None:
        """Emergency kill switch — block all trading."""
        self._kill_switch = True
        logger.critical("KILL SWITCH ACTIVATED — all trading halted")

    def unkill(self) -> None:
        self._kill_switch = False
        logger.warning("Kill switch deactivated")

    def check(
        self,
        scored: ScoredIntent,
        running_exposure: float | None = None,
        running_market_exposures: dict[str, float] | None = None,
    ) -> RiskCheckResult:
        """Run all risk checks on a scored intent.

        Runs in BOTH paper and live mode — the previous version
        bypassed concentration, daily-loss, and exposure checks in paper
        mode, which meant paper trading couldn't validate the risk
        engine and you'd only find bugs after going live. Paper must
        mirror live behavior except where genuinely impossible (e.g.
        checking a real wallet balance that doesn't exist).

        When called from check_batch(), `running_exposure` and
        `running_market_exposures` carry forward the in-batch approvals
        so the N-th intent sees the effect of the first N-1 approvals
        even though they haven't been written back to system_state yet.
        """
        intent = scored.intent

        # Kill switch
        if self._kill_switch:
            return RiskCheckResult(False, "Kill switch active")

        # Use the caller-provided running totals when present. Otherwise
        # single-intent path: sync fresh from system_state.
        if running_exposure is None or running_market_exposures is None:
            self._sync_from_system_state()
            total_exp = self.state.total_exposure
            market_exp_usdc = self.state.market_exposure(intent.market_id)
        else:
            total_exp = running_exposure
            market_exp_usdc = running_market_exposures.get(intent.market_id, 0.0)

        # Balance check (paper uses starting_capital as the reference)
        if self.state.balance < self.min_balance_usdc:
            return RiskCheckResult(False, f"Balance too low: ${self.state.balance:.2f}")

        # Daily loss check — uses the baseline-adjusted delta, not
        # cumulative P&L, so a prior drawdown doesn't permanently
        # lock up trading.
        if self.state.daily_pnl < 0 and self.state.balance > 0:
            loss_pct = abs(self.state.daily_pnl) / self.state.balance
            if loss_pct >= self.max_daily_loss_pct:
                return RiskCheckResult(
                    False,
                    f"Daily loss limit hit: {loss_pct:.1%} >= {self.max_daily_loss_pct:.1%}",
                )

        # Portfolio exposure check (against the running total)
        if self.state.balance > 0:
            exposure_pct = total_exp / self.state.balance
            if exposure_pct >= self.max_portfolio_exposure:
                return RiskCheckResult(
                    False,
                    f"Portfolio exposure limit: {exposure_pct:.1%}",
                )

        # Single market concentration check (against the running per-market total)
        if self.state.balance > 0:
            market_exp_pct = market_exp_usdc / self.state.balance
            if market_exp_pct >= self.max_single_market_pct:
                return RiskCheckResult(
                    False,
                    f"Market concentration limit: {market_exp_pct:.1%}",
                )

        # Size adjustment (always enforced)
        adjusted_size = min(intent.size_usdc, self.max_trade_size_usdc)

        # Reduce size if approaching the portfolio cap
        remaining_exposure = self.max_portfolio_exposure * self.state.balance - total_exp
        if remaining_exposure > 0:
            adjusted_size = min(adjusted_size, remaining_exposure)

        # Also reduce by remaining headroom under the per-market cap
        remaining_market = self.max_single_market_pct * self.state.balance - market_exp_usdc
        if remaining_market > 0:
            adjusted_size = min(adjusted_size, remaining_market)

        if adjusted_size < 1.0:
            return RiskCheckResult(False, "Adjusted size too small")

        mode = "PAPER" if self.paper_trading else "LIVE"
        return RiskCheckResult(
            approved=True,
            reason=f"Approved [{mode}]: ${adjusted_size:.2f}",
            adjusted_size=adjusted_size,
        )

    def check_batch(self, scored_intents: list[ScoredIntent]) -> list[ScoredIntent]:
        """Check and approve/reject a batch of scored intents.

        Syncs from system_state ONCE at the start of the batch, then
        threads a running total_exposure + per-market_exposures dict
        through each check. This way the N-th approved intent sees the
        effect of the first N-1 approvals even though they haven't
        been written back to system_state yet — preventing the batch
        from blowing through portfolio or concentration caps.
        """
        # Single sync at the start of the batch
        self._sync_from_system_state()

        running_exposure = self.state.total_exposure
        running_market_exposures: dict[str, float] = {}
        for p in self.state.positions:
            running_market_exposures[p.market_id] = (
                running_market_exposures.get(p.market_id, 0.0) + p.size_usdc
            )

        approved = []
        for si in scored_intents:
            result = self.check(
                si,
                running_exposure=running_exposure,
                running_market_exposures=running_market_exposures,
            )
            si.approved = result.approved
            if result.approved:
                si.intent.size_usdc = result.adjusted_size
                approved.append(si)
                # Carry the approval forward so subsequent intents in the
                # same batch see the updated exposure.
                running_exposure += result.adjusted_size
                running_market_exposures[si.intent.market_id] = (
                    running_market_exposures.get(si.intent.market_id, 0.0)
                    + result.adjusted_size
                )
            else:
                logger.info(f"RISK REJECTED: {si.intent.question[:50]} — {result.reason}")
        return approved

    def record_trade(self, intent: OrderIntent) -> None:
        """Update risk state after a trade executes."""
        self.state.total_exposure += intent.size_usdc
        self.state.trades_today += 1

    def record_pnl(self, pnl: float) -> None:
        """Update daily PnL."""
        self.state.daily_pnl += pnl

    def reset_daily(self) -> None:
        """Reset daily counters.

        Snapshots the current cumulative realized P&L as the baseline
        for tomorrow, so `_sync_from_system_state` will compute
        daily_pnl as (cumulative - baseline) = 0 at start-of-day.
        """
        try:
            from backend.state import system_state
            self.state.daily_pnl_baseline = float(
                getattr(system_state, "realized_pnl", 0) or 0
            )
        except Exception:
            self.state.daily_pnl_baseline = 0.0
        self.state.daily_pnl = 0.0
        self.state.trades_today = 0
        self.state.last_reset = datetime.now(timezone.utc)
