"""
Trade Orchestrator — sits between analysis pipeline and execution engine.

Receives trade signals, validates through safety, executes, and logs.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from execution.engine import ExecutionEngine
from execution.safety import SafetyGuardrails
from execution.config import SafetyConfig
from execution.models import TradeRequest, TradeResult

logger = logging.getLogger(__name__)


class TradeOrchestrator:
    """Processes trade signals through safety → execution → logging."""

    def __init__(self, mode: str | None = None) -> None:
        self.engine = ExecutionEngine(mode)
        self.safety = SafetyGuardrails(SafetyConfig())
        self._peak_value: float = SafetyConfig().STARTING_CAPITAL
        self._current_value: float = SafetyConfig().STARTING_CAPITAL

    async def process_signal(self, signal: dict) -> Optional[TradeResult]:
        """
        Process a trade signal from the analysis pipeline.

        Flow: build request → safety checks → execute → log
        """
        try:
            trade = TradeRequest(
                market_id=signal.get("market_id", ""),
                market_title=signal.get("market_title", signal.get("question", "")),
                token_id=signal.get("token_id", signal.get("condition_id", "")),
                direction=signal.get("direction", signal.get("side", "YES")),
                order_type=signal.get("order_type", "limit"),
                price=signal.get("price"),
                size=signal.get("size"),
                amount_usd=signal.get("amount_usd"),
                opportunity_score=signal.get("opportunity_score", 0),
                edge_estimate=signal.get("edge_estimate", 0),
                fair_probability=signal.get("fair_probability", 0.5),
                classification=signal.get("classification", "PAPER-TRADE"),
                decision_id=signal.get("decision_id", ""),
                tick_size=signal.get("tick_size", "0.01"),
                neg_risk=signal.get("neg_risk", False),
            )

            # Get portfolio state
            state = self._get_portfolio_state()

            # Safety checks
            allowed, reason = self.safety.check_all(trade, state)
            if not allowed:
                logger.info(f"Trade blocked: {reason}")
                return None

            # Execute
            result = await self.engine.execute_trade(trade)

            # Record
            if result.status == "filled":
                self.safety.record_trade(pnl=0)  # PnL computed on close
                self._current_value = state.get("current_portfolio_value", self._current_value) + result.amount_usd
                self._peak_value = max(self._peak_value, self._current_value)

            logger.info(
                f"Trade executed [{result.mode}]: {result.direction} "
                f"${result.amount_usd:.2f} @ {result.fill_price:.4f} — {result.market_title[:40]}"
            )
            return result

        except Exception as e:
            logger.error(f"Signal processing failed: {e}")
            return None

    def _get_portfolio_state(self) -> dict:
        """Build portfolio state for safety checks."""
        return {
            "total_exposure_usd": self._current_value - SafetyConfig().STARTING_CAPITAL,
            "total_capital": SafetyConfig().STARTING_CAPITAL,
            "available_balance_usd": SafetyConfig().STARTING_CAPITAL,
            "peak_portfolio_value": self._peak_value,
            "current_portfolio_value": self._current_value,
        }

    async def emergency_shutdown(self) -> dict:
        """Kill switch: cancel all orders, halt trading."""
        self.safety.activate_kill_switch("Emergency shutdown via API")
        cancelled = False
        if self.engine.mode == "live":
            cancelled = await self.engine.cancel_all_orders()
        self.engine.mode = "paper"
        logger.critical("EMERGENCY SHUTDOWN — switched to paper mode")
        return {"killed": True, "orders_cancelled": cancelled, "mode": "paper"}

    def get_status(self) -> dict:
        return {
            "mode": self.engine.mode,
            "kill_switch": self.safety.is_killed,
            "daily_stats": self.safety.get_daily_stats(),
            "peak_value": self._peak_value,
            "current_value": self._current_value,
        }
