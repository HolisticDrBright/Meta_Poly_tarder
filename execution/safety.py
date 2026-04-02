"""
Safety Guardrails — ALL trades must pass every check before execution.

If any check fails, the trade is blocked and logged.
The kill switch halts everything and never auto-deactivates.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from execution.config import SafetyConfig
from execution.models import TradeRequest

logger = logging.getLogger(__name__)


class SafetyGuardrails:
    """Every trade must pass ALL checks before execution."""

    def __init__(self, config: SafetyConfig | None = None) -> None:
        self.config = config or SafetyConfig()
        self.daily_trades: list[dict] = []
        self.daily_pnl: float = 0.0
        self.is_killed: bool = False
        self._kill_reason: str = ""
        self._kill_time: Optional[datetime] = None

    def check_all(self, trade: TradeRequest, portfolio_state: dict) -> tuple[bool, str]:
        """Run all safety checks. Returns (allowed, reason)."""
        checks = [
            self._check_kill_switch,
            self._check_max_trade_size,
            self._check_daily_loss_limit,
            self._check_daily_trade_count,
            self._check_max_portfolio_exposure,
            self._check_max_single_market_exposure,
            self._check_min_edge,
            self._check_min_opportunity_score,
            self._check_balance_available,
            self._check_drawdown_limit,
        ]
        for check in checks:
            allowed, reason = check(trade, portfolio_state)
            if not allowed:
                logger.warning(f"TRADE BLOCKED: {reason} — {trade.market_title[:50]}")
                return False, reason
        return True, "All checks passed"

    def _check_kill_switch(self, trade: TradeRequest, state: dict) -> tuple[bool, str]:
        if self.is_killed:
            return False, f"KILL SWITCH ACTIVE: {self._kill_reason}"
        return True, ""

    def _check_max_trade_size(self, trade: TradeRequest, state: dict) -> tuple[bool, str]:
        amount = trade.effective_amount
        if amount > self.config.MAX_TRADE_SIZE_USD:
            return False, f"Trade ${amount:.2f} exceeds max ${self.config.MAX_TRADE_SIZE_USD}"
        return True, ""

    def _check_daily_loss_limit(self, trade: TradeRequest, state: dict) -> tuple[bool, str]:
        if self.daily_pnl < -self.config.MAX_DAILY_LOSS_USD:
            return False, f"Daily loss limit: ${self.daily_pnl:.2f} < -${self.config.MAX_DAILY_LOSS_USD}"
        return True, ""

    def _check_daily_trade_count(self, trade: TradeRequest, state: dict) -> tuple[bool, str]:
        if len(self.daily_trades) >= self.config.MAX_DAILY_TRADES:
            return False, f"Daily trade limit: {len(self.daily_trades)}/{self.config.MAX_DAILY_TRADES}"
        return True, ""

    def _check_max_portfolio_exposure(self, trade: TradeRequest, state: dict) -> tuple[bool, str]:
        current = state.get("total_exposure_usd", 0)
        if current + trade.effective_amount > self.config.MAX_PORTFOLIO_EXPOSURE_USD:
            return False, f"Portfolio exposure would exceed ${self.config.MAX_PORTFOLIO_EXPOSURE_USD}"
        return True, ""

    def _check_max_single_market_exposure(self, trade: TradeRequest, state: dict) -> tuple[bool, str]:
        capital = state.get("total_capital", self.config.STARTING_CAPITAL)
        if capital > 0 and (trade.effective_amount / capital) > self.config.MAX_SINGLE_MARKET_PCT:
            return False, f"Single market > {self.config.MAX_SINGLE_MARKET_PCT*100}% of capital"
        return True, ""

    def _check_min_edge(self, trade: TradeRequest, state: dict) -> tuple[bool, str]:
        if abs(trade.edge_estimate) < self.config.MIN_EDGE_TO_TRADE:
            return False, f"Edge {trade.edge_estimate:.1%} below min {self.config.MIN_EDGE_TO_TRADE:.1%}"
        return True, ""

    def _check_min_opportunity_score(self, trade: TradeRequest, state: dict) -> tuple[bool, str]:
        if trade.opportunity_score < self.config.MIN_OPPORTUNITY_SCORE:
            return False, f"Score {trade.opportunity_score} below min {self.config.MIN_OPPORTUNITY_SCORE}"
        return True, ""

    def _check_balance_available(self, trade: TradeRequest, state: dict) -> tuple[bool, str]:
        balance = state.get("available_balance_usd", 0)
        if trade.effective_amount > balance:
            return False, f"Insufficient: need ${trade.effective_amount:.2f}, have ${balance:.2f}"
        return True, ""

    def _check_drawdown_limit(self, trade: TradeRequest, state: dict) -> tuple[bool, str]:
        peak = state.get("peak_portfolio_value", self.config.STARTING_CAPITAL)
        current = state.get("current_portfolio_value", self.config.STARTING_CAPITAL)
        if peak > 0:
            dd = (peak - current) / peak
            if dd > self.config.MAX_DRAWDOWN_PCT:
                self.activate_kill_switch(f"Drawdown {dd:.1%} exceeds {self.config.MAX_DRAWDOWN_PCT:.0%}")
                return False, f"DRAWDOWN LIMIT — KILL SWITCH ACTIVATED ({dd:.1%})"
        return True, ""

    def activate_kill_switch(self, reason: str = "Manual") -> None:
        self.is_killed = True
        self._kill_reason = reason
        self._kill_time = datetime.now(timezone.utc)
        logger.critical(f"KILL SWITCH ACTIVATED: {reason}")

    def deactivate_kill_switch(self) -> None:
        logger.warning(f"Kill switch deactivated (was: {self._kill_reason})")
        self.is_killed = False
        self._kill_reason = ""

    def record_trade(self, pnl: float = 0.0) -> None:
        self.daily_trades.append({"time": datetime.now(timezone.utc).isoformat(), "pnl": pnl})
        self.daily_pnl += pnl

    def reset_daily(self) -> None:
        self.daily_trades.clear()
        self.daily_pnl = 0.0

    def get_daily_stats(self) -> dict:
        return {
            "trades_today": len(self.daily_trades),
            "max_trades": self.config.MAX_DAILY_TRADES,
            "daily_pnl": self.daily_pnl,
            "max_daily_loss": self.config.MAX_DAILY_LOSS_USD,
            "kill_switch": self.is_killed,
            "kill_reason": self._kill_reason,
        }
