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
        max_portfolio_exposure: float = 0.80,
        max_single_market_pct: float = 0.15,
        max_daily_loss_pct: float = 0.10,
        max_trade_size_usdc: float = 150,
        min_balance_usdc: float = 10,
        paper_trading: bool = True,
    ) -> None:
        self.max_portfolio_exposure = max_portfolio_exposure
        self.max_single_market_pct = max_single_market_pct
        self.max_daily_loss_pct = max_daily_loss_pct
        self.max_trade_size_usdc = max_trade_size_usdc
        self.min_balance_usdc = min_balance_usdc
        self.paper_trading = paper_trading
        self.state = RiskState()
        self._kill_switch = False

    def kill(self) -> None:
        """Emergency kill switch — block all trading."""
        self._kill_switch = True
        logger.critical("KILL SWITCH ACTIVATED — all trading halted")

    def unkill(self) -> None:
        self._kill_switch = False
        logger.warning("Kill switch deactivated")

    def check(self, scored: ScoredIntent) -> RiskCheckResult:
        """Run all risk checks on a scored intent."""
        intent = scored.intent

        # Kill switch
        if self._kill_switch:
            return RiskCheckResult(False, "Kill switch active")

        # Paper trading passthrough (still check sizing)
        if not self.paper_trading:
            # Balance check
            if self.state.balance < self.min_balance_usdc:
                return RiskCheckResult(False, f"Balance too low: ${self.state.balance:.2f}")

            # Daily loss check
            if self.state.daily_pnl < 0:
                loss_pct = abs(self.state.daily_pnl) / self.state.balance
                if loss_pct >= self.max_daily_loss_pct:
                    return RiskCheckResult(
                        False,
                        f"Daily loss limit hit: {loss_pct:.1%} >= {self.max_daily_loss_pct:.1%}",
                    )

            # Portfolio exposure check
            if self.state.exposure_pct >= self.max_portfolio_exposure:
                return RiskCheckResult(
                    False,
                    f"Portfolio exposure limit: {self.state.exposure_pct:.1%}",
                )

            # Single market concentration check
            market_exp = self.state.market_exposure_pct(intent.market_id)
            if market_exp >= self.max_single_market_pct:
                return RiskCheckResult(
                    False,
                    f"Market concentration limit: {market_exp:.1%}",
                )

        # Size adjustment (always enforced)
        adjusted_size = min(intent.size_usdc, self.max_trade_size_usdc)

        # Reduce size if approaching limits
        remaining_exposure = (
            self.max_portfolio_exposure * self.state.balance - self.state.total_exposure
        )
        if remaining_exposure > 0:
            adjusted_size = min(adjusted_size, remaining_exposure)

        if adjusted_size < 1.0:
            return RiskCheckResult(False, "Adjusted size too small")

        mode = "PAPER" if self.paper_trading else "LIVE"
        return RiskCheckResult(
            approved=True,
            reason=f"Approved [{mode}]: ${adjusted_size:.2f}",
            adjusted_size=adjusted_size,
        )

    def check_batch(self, scored_intents: list[ScoredIntent]) -> list[ScoredIntent]:
        """Check and approve/reject a batch of scored intents."""
        approved = []
        for si in scored_intents:
            result = self.check(si)
            si.approved = result.approved
            if result.approved:
                si.intent.size_usdc = result.adjusted_size
                approved.append(si)
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
        """Reset daily counters."""
        self.state.daily_pnl = 0.0
        self.state.trades_today = 0
        self.state.last_reset = datetime.now(timezone.utc)
