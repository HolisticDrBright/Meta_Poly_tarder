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

    # The standalone TradeOrchestrator is used by the execution API's own
    # /trade endpoint. Constructing it can block (CLOB handshake over VPN),
    # so push it onto a thread and skip it entirely if we're just going back
    # to paper.
    import asyncio as _asyncio
    try:
        if req.mode == "live":
            _orchestrator = await _asyncio.to_thread(TradeOrchestrator, "live")
        else:
            _orchestrator = TradeOrchestrator(mode="paper")
    except Exception as e:
        logger.error(f"Orchestrator init failed: {e}")
        # Still fall through — the scheduler executor below is what actually
        # trades, so the UI should not be blocked by this.
        _orchestrator = None

    live_balance: float | None = None

    # Update the global system state + the scheduler's OrderExecutor so the
    # trading loop actually routes to real CLOB orders.
    try:
        from backend.state import system_state
        system_state.paper_trading = (req.mode == "paper")

        executor = getattr(system_state, "_executor", None)
        exec_status: dict = {}
        if executor is not None:
            # set_mode is sync and cheap for paper; for live it may touch
            # py-clob-client import, so run in a thread for safety.
            exec_status = await _asyncio.to_thread(executor.set_mode, req.mode)
            if not exec_status.get("ok"):
                raise HTTPException(
                    400,
                    f"Scheduler executor refused mode switch: "
                    f"{exec_status.get('error', 'unknown error')}",
                )

        if req.mode == "live":
            # Reset today's counters for the live session
            system_state.trades_today = 0
            # Fetch real wallet balance off the event loop
            if _orchestrator is not None:
                try:
                    real_balance = await _asyncio.to_thread(
                        lambda: _orchestrator.engine.client.get_balance()
                        if _orchestrator.engine.client else 0
                    )
                    if real_balance:
                        live_balance = float(real_balance) if not isinstance(
                            real_balance, dict
                        ) else float(real_balance.get("balance", 0))
                        if live_balance and live_balance > 0:
                            system_state.balance = live_balance
                except Exception as e:
                    logger.warning(f"Balance fetch failed: {e}")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"State update on mode switch failed: {e}")

    logger.info(f"Execution mode changed to: {req.mode}")
    return {
        "mode": req.mode,
        "status": "active",
        "paper_trading": req.mode == "paper",
        "live_balance": live_balance,
    }


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
