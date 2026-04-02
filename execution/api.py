"""
FastAPI endpoints for the execution layer.
"""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from execution.orchestrator import TradeOrchestrator
from execution.comparator import ExecutionComparator

logger = logging.getLogger(__name__)
router = APIRouter()

_orchestrator: Optional[TradeOrchestrator] = None
_comparator = ExecutionComparator()


def _get_orchestrator() -> TradeOrchestrator:
    global _orchestrator
    if _orchestrator is None:
        _orchestrator = TradeOrchestrator()
    return _orchestrator


class ModeRequest(BaseModel):
    mode: str  # "paper" or "live"


class TradeSignal(BaseModel):
    market_id: str
    market_title: str = ""
    token_id: str = ""
    direction: str = "YES"
    price: float | None = None
    size: float | None = None
    amount_usd: float | None = None
    opportunity_score: float = 0
    edge_estimate: float = 0
    fair_probability: float = 0.5


# ── Mode control ────────────────────────────────────────────

@router.get("/mode")
async def get_mode():
    orch = _get_orchestrator()
    return {"mode": orch.engine.mode}


@router.post("/mode")
async def set_mode(req: ModeRequest):
    global _orchestrator
    if req.mode not in ("paper", "live"):
        raise HTTPException(400, "Mode must be 'paper' or 'live'")

    # Update the execution orchestrator
    _orchestrator = TradeOrchestrator(mode=req.mode)

    # Update the global system state so the dashboard reflects the change
    try:
        from backend.state import system_state
        system_state.paper_trading = (req.mode == "paper")

        if req.mode == "live":
            # Reset P&L counters for live tracking
            system_state.realized_pnl = 0.0
            system_state.unrealized_pnl = 0.0
            system_state.trades_today = 0
            system_state.total_exposure = 0.0
            # Fetch real wallet balance
            try:
                real_balance = await _orchestrator.engine.get_balance()
                if real_balance > 0:
                    system_state.balance = real_balance
            except Exception:
                pass
    except Exception:
        pass

    logger.info(f"Execution mode changed to: {req.mode}")
    return {"mode": req.mode, "status": "active", "paper_trading": req.mode == "paper"}


@router.post("/kill")
async def kill_switch():
    orch = _get_orchestrator()
    result = await orch.emergency_shutdown()
    try:
        from backend.state import system_state
        system_state.paper_trading = True
    except Exception:
        pass
    return result


@router.post("/resume")
async def resume():
    orch = _get_orchestrator()
    orch.safety.deactivate_kill_switch()
    return {"status": "resumed", "kill_switch": False}


@router.get("/status")
async def get_status():
    orch = _get_orchestrator()
    status = orch.get_status()
    try:
        from backend.state import system_state
        status["paper_trading"] = system_state.paper_trading
        status["starting_capital"] = orch.safety.config.STARTING_CAPITAL

        # If live mode, fetch real wallet balance
        if orch.engine.mode == "live":
            try:
                real_balance = await orch.engine.get_balance()
                if real_balance > 0:
                    status["live_balance"] = real_balance
                    system_state.balance = real_balance
            except Exception:
                pass
    except Exception:
        pass
    return status


# ── Trading ─────────────────────────────────────────────────

@router.post("/trade")
async def execute_trade(signal: TradeSignal):
    orch = _get_orchestrator()
    result = await orch.process_signal(signal.model_dump())
    if result is None:
        return {"status": "blocked", "message": "Trade failed safety checks"}
    return {
        "status": result.status,
        "trade_id": result.trade_id,
        "order_id": result.order_id,
        "mode": result.mode,
        "fill_price": result.fill_price,
        "filled_size": result.filled_size,
        "amount_usd": result.amount_usd,
    }


# ── Live info ───────────────────────────────────────────────

@router.get("/balance")
async def get_balance():
    orch = _get_orchestrator()
    bal = await orch.engine.get_balance()
    return {"balance_usd": bal, "mode": orch.engine.mode}


@router.get("/orders/open")
async def open_orders():
    orch = _get_orchestrator()
    orders = await orch.engine.get_open_orders()
    return {"orders": orders, "count": len(orders)}


@router.post("/orders/cancel-all")
async def cancel_all():
    orch = _get_orchestrator()
    success = await orch.engine.cancel_all_orders()
    return {"cancelled": success}


# ── Comparison ──────────────────────────────────────────────

@router.get("/comparison")
async def comparison_stats():
    return _comparator.get_aggregate()


# ── Safety ──────────────────────────────────────────────────

@router.get("/safety/config")
async def safety_config():
    orch = _get_orchestrator()
    c = orch.safety.config
    return {
        "starting_capital": c.STARTING_CAPITAL,
        "max_trade_size": c.MAX_TRADE_SIZE_USD,
        "max_daily_loss": c.MAX_DAILY_LOSS_USD,
        "max_daily_trades": c.MAX_DAILY_TRADES,
        "max_portfolio_exposure": c.MAX_PORTFOLIO_EXPOSURE_USD,
        "max_single_market_pct": c.MAX_SINGLE_MARKET_PCT,
        "max_drawdown_pct": c.MAX_DRAWDOWN_PCT,
        "min_edge": c.MIN_EDGE_TO_TRADE,
        "min_opportunity_score": c.MIN_OPPORTUNITY_SCORE,
    }


@router.get("/safety/daily-stats")
async def daily_stats():
    orch = _get_orchestrator()
    return orch.safety.get_daily_stats()
