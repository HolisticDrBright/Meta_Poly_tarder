"""
Position exit manager — handles swing exits, stop-loss, and auto-close.

Runs every position price update cycle (15s) and checks:
  1. Swing exit: close when price reaches edge-capture target
  2. Stop-loss: close if loss exceeds max %
  3. Trailing stop: track peak and close on drawdown
  4. Time-decay exit: lower profit target as position ages
  5. Resolution close: close if market closes in <1h and profitable
  6. Near-certain: close if price >0.95 / <0.05 and profitable
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from backend.strategies.base import MarketState, Position, Side

logger = logging.getLogger(__name__)


@dataclass
class ExitRule:
    """Configurable exit parameters."""

    # --- Swing exit (model-probability based) ---
    # Capture this fraction of the model edge before selling.
    # E.g., bought YES at 0.12, model says 0.22, edge_capture=0.60
    # → target = 0.12 + 0.60*(0.22-0.12) = 0.18 → sell at 18¢
    edge_capture_pct: float = 0.60

    # Fall back to flat % take-profit when no model_probability
    take_profit_pct: float = 0.30     # 30% profit → close

    # --- Stop-loss ---
    stop_loss_pct: float = -0.20      # 20% loss → close

    # --- Trailing stop ---
    trailing_stop_pct: float = 0.15   # 15% drawdown from peak → close

    # --- Time decay: lower the profit target as position ages ---
    # After this many hours, start reducing the edge_capture target
    age_hours_full_target: float = 2.0    # first 2h: hold for full target
    age_hours_min_target: float = 24.0    # by 24h: accept minimum profit
    min_profit_to_exit: float = 0.02      # always require at least 2% return

    # --- Resolution ---
    resolution_hours: float = 1.0
    resolution_min_profit: float = 0.10

    # --- Max age: unconditional close to prevent zombie positions ---
    max_age_hours: float = 72.0  # 3 days max, regardless of P&L


@dataclass
class ExitSignal:
    """A signal to close a position."""

    position: Position
    reason: str
    pnl: float
    urgency: str  # "immediate" | "normal"


class ExitManager:
    """Evaluates open positions against exit rules with smart swing exits."""

    def __init__(self, rules: ExitRule | None = None) -> None:
        self.rules = rules or ExitRule()
        self._peak_prices: dict[str, float] = {}

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

    def _age_hours(self, pos: Position) -> float:
        """How many hours this position has been open."""
        now = datetime.now(timezone.utc)
        opened = pos.opened_at if pos.opened_at.tzinfo else pos.opened_at.replace(tzinfo=timezone.utc)
        return max(0, (now - opened).total_seconds() / 3600)

    def _time_adjusted_capture(self, age_h: float) -> float:
        """Lower the edge capture target as the position ages.

        Fresh positions (< age_hours_full_target): hold for full target.
        Old positions (> age_hours_min_target): accept minimum profit.
        In between: linear interpolation.
        """
        r = self.rules
        if age_h <= r.age_hours_full_target:
            return r.edge_capture_pct
        if age_h >= r.age_hours_min_target:
            return r.min_profit_to_exit
        # Linear decay
        progress = (age_h - r.age_hours_full_target) / (r.age_hours_min_target - r.age_hours_full_target)
        return r.edge_capture_pct - progress * (r.edge_capture_pct - r.min_profit_to_exit)

    def _check_position(
        self, pos: Position, market: MarketState | None
    ) -> Optional[ExitSignal]:
        """Check a single position against all exit rules."""

        if pos.size_usdc <= 0:
            return None

        pnl_pct = pos.pnl / pos.size_usdc
        age_h = self._age_hours(pos)

        # ── 1. Swing exit (model-probability based) ──────────────
        # If we have a model_probability, compute a price target based
        # on how much of the model's edge we want to capture, adjusted
        # for how long we've been holding.
        if market and getattr(market, "model_probability", 0) > 0:
            model_p = market.model_probability
            entry_p = pos.entry_price

            # Edge = model's fair value minus our entry
            if pos.side == Side.YES:
                edge = model_p - entry_p
            else:
                edge = (1.0 - model_p) - entry_p

            if edge > 0:
                # How much of the edge to capture (decays with time)
                capture_pct = self._time_adjusted_capture(age_h)

                # Target price = entry + capture_pct * edge
                target_price = entry_p + capture_pct * edge

                # Current price of the token we hold
                current_p = pos.current_price

                if current_p >= target_price and pos.pnl > 0:
                    return ExitSignal(
                        position=pos,
                        reason=(
                            f"SWING EXIT: price {current_p:.3f} >= target {target_price:.3f} "
                            f"(edge={edge:.3f}, capture={capture_pct:.0%}, age={age_h:.1f}h)"
                        ),
                        pnl=pos.pnl,
                        urgency="normal",
                    )

            # Edge has flipped negative (model now agrees with market
            # or disagrees with our side) — tighter stop
            if edge < -0.03 and age_h > 1.0:
                return ExitSignal(
                    position=pos,
                    reason=(
                        f"EDGE FLIP: model now {model_p:.3f} vs entry {entry_p:.3f} "
                        f"(edge={edge:+.3f}, age={age_h:.1f}h)"
                    ),
                    pnl=pos.pnl,
                    urgency="normal",
                )

        # ── 2. Flat take-profit (fallback when no model_probability) ─
        if pnl_pct >= self.rules.take_profit_pct:
            return ExitSignal(
                position=pos,
                reason=f"TAKE PROFIT: {pnl_pct:.1%} >= {self.rules.take_profit_pct:.0%}",
                pnl=pos.pnl,
                urgency="normal",
            )

        # ── 3. Stop-loss ─────────────────────────────────────────
        if pnl_pct <= self.rules.stop_loss_pct:
            return ExitSignal(
                position=pos,
                reason=f"STOP LOSS: {pnl_pct:.1%} <= {self.rules.stop_loss_pct:.0%}",
                pnl=pos.pnl,
                urgency="immediate",
            )

        # ── 4. Trailing stop ─────────────────────────────────────
        if self.rules.trailing_stop_pct > 0:
            key = pos.market_id
            current = pos.current_price
            peak = self._peak_prices.get(key, current)
            if current >= peak:
                self._peak_prices[key] = current
                peak = current
            if peak > 0:
                drawdown = (peak - current) / peak
                if drawdown >= self.rules.trailing_stop_pct:
                    return ExitSignal(
                        position=pos,
                        reason=f"TRAILING STOP: {drawdown:.1%} drawdown from peak {peak:.3f}",
                        pnl=pos.pnl,
                        urgency="immediate",
                    )

        # ── 5. Time-decay exit (aging unprofitable positions) ────
        # If position has been open > 24h and is barely profitable,
        # close it to free capital for better opportunities.
        if age_h >= self.rules.age_hours_min_target and pnl_pct >= self.rules.min_profit_to_exit:
            return ExitSignal(
                position=pos,
                reason=(
                    f"AGE EXIT: {age_h:.0f}h old, pnl={pnl_pct:.1%} >= "
                    f"min {self.rules.min_profit_to_exit:.0%} — freeing capital"
                ),
                pnl=pos.pnl,
                urgency="normal",
            )

        # ── 6. Max age — unconditional close to prevent zombie positions
        if age_h >= self.rules.max_age_hours:
            return ExitSignal(
                position=pos,
                reason=(
                    f"MAX AGE: {age_h:.0f}h old (limit={self.rules.max_age_hours:.0f}h), "
                    f"pnl={pnl_pct:.1%} — closing to free capital"
                ),
                pnl=pos.pnl,
                urgency="normal",
            )

        # ── 7. Resolution auto-close ─────────────────────────────
        if (
            market
            and market.hours_to_close <= self.rules.resolution_hours
            and pos.pnl > self.rules.resolution_min_profit
            and self.rules.resolution_min_profit > 0
        ):
            return ExitSignal(
                position=pos,
                reason=f"RESOLUTION EXIT: {market.hours_to_close:.1f}h left, PnL=${pos.pnl:.2f}",
                pnl=pos.pnl,
                urgency="normal",
            )

        # ── 7. Near-certain resolution ───────────────────────────
        if market and pos.pnl > 0:
            if pos.side == Side.YES and market.yes_price >= 0.95:
                return ExitSignal(
                    position=pos,
                    reason=f"NEAR CERTAIN YES: price={market.yes_price:.3f} pnl=${pos.pnl:.2f}",
                    pnl=pos.pnl,
                    urgency="normal",
                )
            if pos.side == Side.NO and market.no_price >= 0.95:
                return ExitSignal(
                    position=pos,
                    reason=f"NEAR CERTAIN NO: price={market.no_price:.3f} pnl=${pos.pnl:.2f}",
                    pnl=pos.pnl,
                    urgency="normal",
                )

        return None

    def clear_tracking(self, market_id: str) -> None:
        """Remove tracking data for a closed position."""
        self._peak_prices.pop(market_id, None)
