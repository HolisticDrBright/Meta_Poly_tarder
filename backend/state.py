"""
Shared system state — single source of truth accessible by
all API routes, the scheduler, and WebSocket broadcasts.

This avoids passing the scheduler instance to every route.
Instead, the scheduler writes to this shared state, and
API routes read from it.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Optional

from backend.strategies.base import MarketState, OrderIntent, Position, ScoredIntent

logger = logging.getLogger(__name__)


@dataclass
class SystemState:
    """Global mutable state shared across the entire system."""

    # Markets
    markets: list[MarketState] = field(default_factory=list)

    # Signals (pending + recent executed)
    pending_signals: list[OrderIntent] = field(default_factory=list)
    recent_signals: list[dict] = field(default_factory=list)  # last 200 serialized

    # Positions
    positions: list[Position] = field(default_factory=list)

    # Whale tracking
    whale_trades: list[dict] = field(default_factory=list)  # last 100
    leaderboard: list[dict] = field(default_factory=list)
    copy_queue: list[dict] = field(default_factory=list)
    smart_money_index: int = 50

    # Jet tracking
    jet_flights: list[dict] = field(default_factory=list)
    jet_signals: list[dict] = field(default_factory=list)
    jet_history: list[dict] = field(default_factory=list)  # last 24h

    # Portfolio
    equity_curve: list[dict] = field(default_factory=list)
    daily_pnl: list[dict] = field(default_factory=list)
    balance: float = 300.0
    starting_capital: float = 300.0
    total_exposure: float = 0.0
    unrealized_pnl: float = 0.0
    realized_pnl: float = 0.0
    win_rate: float = 0.0
    sharpe_ratio: float = 0.0
    max_drawdown: float = 0.0
    trades_today: int = 0

    # Market maker statuses
    mm_statuses: list[dict] = field(default_factory=list)

    # System
    paper_trading: bool = True
    scheduler_running: bool = False
    last_update: Optional[datetime] = None

    # Shared DuckDB reference (set by scheduler, used by API for trade queries)
    _duckdb: Any = field(default=None, repr=False)

    # Shared OrderExecutor reference (set by scheduler, used by execution API
    # to flip paper<->live without restarting)
    _executor: Any = field(default=None, repr=False)

    # WebSocket broadcast callback (set by main.py)
    _broadcast_fn: Optional[Callable] = field(default=None, repr=False)

    def set_broadcast(self, fn: Callable) -> None:
        self._broadcast_fn = fn

    async def broadcast(self, event_type: str, data: dict) -> None:
        """Broadcast to all connected WebSocket clients. Never raises."""
        if not self._broadcast_fn:
            return
        try:
            result = self._broadcast_fn(event_type, data)
            if hasattr(result, "__await__"):
                await result
        except Exception:
            # Never let broadcast errors crash the caller (scheduler, etc.)
            pass

    # ── Market updates ──────────────────────────────────────────

    def update_markets(self, markets: list[MarketState]) -> None:
        """Merge fresh market data from the Gamma refresh while preserving
        derived fields that other subsystems (ensemble, entropy screener,
        specialist orchestrator) may have populated since the last refresh.

        Without this merge, every 45s refresh wipes `model_probability`,
        `kl_divergence`, and `entropy_bits` back to zero because
        `_gamma_to_market_state` builds fresh MarketState instances with
        dataclass defaults. The ensemble only runs every 3 min on top 10
        markets, so in the intervening 2m 45s the entropy screener sees
        model_probability=0 on every market and emits no signals.
        Same problem bit the specialist orchestrator's gate, which
        requires model_probability > 0.

        Merge rule: for each incoming market, if we already have a
        MarketState with the same market_id that has a non-default
        model_probability, carry those derived fields forward onto the
        new instance before replacing. All price/liquidity/volume data
        still comes from the fresh Gamma payload.
        """
        prior_by_id: dict[str, MarketState] = {m.market_id: m for m in self.markets}
        for m in markets:
            old = prior_by_id.get(m.market_id)
            if old is None:
                continue
            # Preserve derived fields computed elsewhere in the pipeline.
            if old.model_probability and old.model_probability > 0:
                m.model_probability = old.model_probability
            if old.kl_divergence:
                m.kl_divergence = old.kl_divergence
            # entropy_bits is computed fresh from yes_price in _gamma_to_market_state
            # so we do NOT preserve the old value — it should always match current price.
        self.markets = markets
        self.last_update = datetime.now(timezone.utc)

    def get_market(self, market_id: str) -> Optional[MarketState]:
        for m in self.markets:
            if m.market_id == market_id:
                return m
        return None

    # ── Signal updates ──────────────────────────────────────────

    def add_signal(self, intent: OrderIntent) -> None:
        serialized = {
            "id": f"{intent.strategy.value}-{intent.market_id[:8]}-{datetime.now(timezone.utc).timestamp():.0f}",
            "strategy": intent.strategy.value,
            "market_id": intent.market_id,
            "question": intent.question,
            "side": intent.side.value,
            "price": intent.price,
            "size_usdc": intent.size_usdc,
            "confidence": intent.confidence,
            "reason": intent.reason,
            "kl_divergence": intent.kl_divergence,
            "kelly_fraction": intent.kelly_fraction,
            "confluence_count": intent.confluence_count,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        self.recent_signals.insert(0, serialized)
        self.recent_signals = self.recent_signals[:200]

    # ── Position updates ────────────────────────────────────────

    def add_position(self, pos: Position) -> None:
        self.positions.append(pos)
        self.total_exposure += pos.size_usdc

    def close_position(self, market_id: str) -> Optional[Position]:
        for i, p in enumerate(self.positions):
            if p.market_id == market_id:
                closed = self.positions.pop(i)
                self.total_exposure -= closed.size_usdc
                self.realized_pnl += closed.pnl
                return closed
        return None

    def get_positions_serialized(self) -> list[dict]:
        return [
            {
                "id": i,
                "market_id": p.market_id,
                "question": p.question,
                "side": p.side.value,
                "entry_price": p.entry_price,
                "size_usdc": p.size_usdc,
                "current_price": p.current_price,
                "strategy": p.strategy.value,
                "opened_at": p.opened_at.isoformat(),
                "pnl": p.pnl,
                "pnl_pct": p.pnl_pct,
                "hours_to_close": None,
            }
            for i, p in enumerate(self.positions)
        ]

    # ── Whale updates ───────────────────────────────────────────

    def add_whale_trade(self, trade: dict) -> None:
        self.whale_trades.insert(0, trade)
        self.whale_trades = self.whale_trades[:100]

    def add_to_copy_queue(self, item: dict) -> None:
        self.copy_queue.append(item)

    def remove_from_copy_queue(self, index: int) -> Optional[dict]:
        if 0 <= index < len(self.copy_queue):
            return self.copy_queue.pop(index)
        return None

    # ── Jet updates ─────────────────────────────────────────────

    def add_jet_event(self, event: dict) -> None:
        self.jet_history.insert(0, event)
        self.jet_history = self.jet_history[:100]

    # ── Stats ───────────────────────────────────────────────────

    def get_stats(self) -> dict:
        # Try to get cumulative P&L from DuckDB if in-memory is lower
        db_pnl = self.realized_pnl
        if self._duckdb and self._duckdb._conn:
            try:
                rows = self._duckdb._conn.execute(
                    "SELECT COALESCE(SUM(pnl), 0) as total FROM trades WHERE trade_type = 'close' OR pnl != 0"
                ).fetchone()
                if rows and rows[0] and abs(rows[0]) > abs(db_pnl):
                    db_pnl = rows[0]
            except Exception:
                pass

        return {
            "balance": self.balance,
            "starting_capital": self.starting_capital,
            "total_exposure": self.total_exposure,
            "unrealized_pnl": self.unrealized_pnl,
            "realized_pnl": db_pnl,
            "win_rate": self.win_rate,
            "sharpe_ratio": self.sharpe_ratio,
            "max_drawdown": self.max_drawdown,
            "trades_today": self.trades_today,
            "paper_trading": self.paper_trading,
            "markets_count": len(self.markets),
            "positions_count": len(self.positions),
            "pending_signals": len(self.pending_signals),
            "scheduler_running": self.scheduler_running,
        }


# ── Singleton ───────────────────────────────────────────────────
system_state = SystemState()
