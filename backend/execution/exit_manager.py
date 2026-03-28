"""
Position exit manager — handles take-profit, stop-loss, and auto-close.

Runs every position price update cycle (15s) and checks:
  1. Take-profit: close if PnL exceeds target %
  2. Stop-loss: close if loss exceeds max %
  3. Resolution close: close if market closes in <1h and position is profitable
  4. Theta exit: close if time decay has extracted most of the edge
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from backend.strategies.base import MarketState, Position, Side

logger = logging.getLogger(__name__)


@dataclass
class ExitRule:
    """Configurable exit parameters."""

    take_profit_pct: float = 0.30     # 30% profit → close
    stop_loss_pct: float = -0.20      # 20% loss → close
    resolution_hours: float = 1.0     # auto-close when <1h to resolution
    resolution_min_profit: float = 0  # only auto-close if at least break-even
    trailing_stop_pct: float = 0.0    # 0 = disabled, >0 = trailing stop from peak


@dataclass
class ExitSignal:
    """A signal to close a position."""

    position: Position
    reason: str
    pnl: float
    urgency: str  # "immediate" | "normal"


class ExitManager:
    """Evaluates open positions against exit rules."""

    def __init__(self, rules: ExitRule | None = None) -> None:
        self.rules = rules or ExitRule()
        self._peak_prices: dict[str, float] = {}  # for trailing stop

    def check_exits(
        self,
        positions: list[Position],
        markets: list[MarketState],
    ) -> list[ExitSignal]:
        """Check all positions for exit conditions. Returns signals to close."""
        market_map = {m.market_id: m for m in markets}
        signals: list[ExitSignal] = []

        for pos in positions:
            market = market_map.get(pos.market_id)
            signal = self._check_position(pos, market)
            if signal:
                signals.append(signal)

        return signals

    def _check_position(
        self, pos: Position, market: MarketState | None
    ) -> Optional[ExitSignal]:
        """Check a single position against all exit rules."""

        # 1. Take-profit
        if pos.entry_price > 0:
            pnl_pct = pos.pnl / (pos.entry_price * pos.size_usdc)
            if pnl_pct >= self.rules.take_profit_pct:
                return ExitSignal(
                    position=pos,
                    reason=f"TAKE PROFIT: {pnl_pct:.1%} >= {self.rules.take_profit_pct:.0%}",
                    pnl=pos.pnl,
                    urgency="normal",
                )

            # 2. Stop-loss
            if pnl_pct <= self.rules.stop_loss_pct:
                return ExitSignal(
                    position=pos,
                    reason=f"STOP LOSS: {pnl_pct:.1%} <= {self.rules.stop_loss_pct:.0%}",
                    pnl=pos.pnl,
                    urgency="immediate",
                )

        # 3. Trailing stop (if enabled)
        if self.rules.trailing_stop_pct > 0:
            key = pos.market_id
            current = pos.current_price
            peak = self._peak_prices.get(key, current)
            if current >= peak:
                # Always record the peak (including first observation)
                self._peak_prices[key] = current
                peak = current
            if peak > 0:
                drawdown_from_peak = (peak - current) / peak
                if drawdown_from_peak >= self.rules.trailing_stop_pct:
                    return ExitSignal(
                        position=pos,
                        reason=f"TRAILING STOP: {drawdown_from_peak:.1%} drawdown from peak {peak:.3f}",
                        pnl=pos.pnl,
                        urgency="immediate",
                    )

        # 4. Resolution auto-close
        if market and market.hours_to_close <= self.rules.resolution_hours:
            if pos.pnl >= self.rules.resolution_min_profit:
                return ExitSignal(
                    position=pos,
                    reason=f"RESOLUTION EXIT: {market.hours_to_close:.1f}h left, PnL=${pos.pnl:.2f}",
                    pnl=pos.pnl,
                    urgency="normal",
                )

        # 5. Near-certain resolution (price >0.95 or <0.05)
        if market:
            if pos.side == Side.YES and market.yes_price >= 0.95:
                return ExitSignal(
                    position=pos,
                    reason=f"NEAR CERTAIN YES: price={market.yes_price:.3f}",
                    pnl=pos.pnl,
                    urgency="normal",
                )
            if pos.side == Side.NO and market.no_price >= 0.95:
                return ExitSignal(
                    position=pos,
                    reason=f"NEAR CERTAIN NO: price={market.no_price:.3f}",
                    pnl=pos.pnl,
                    urgency="normal",
                )

        return None

    def clear_tracking(self, market_id: str) -> None:
        """Remove tracking data for a closed position."""
        self._peak_prices.pop(market_id, None)
