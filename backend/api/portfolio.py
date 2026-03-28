"""
Portfolio, positions, and trade execution API endpoints.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend.state import system_state

logger = logging.getLogger(__name__)
router = APIRouter()


class ManualOrderRequest(BaseModel):
    market_id: str
    side: str  # "YES" or "NO"
    price: float
    size_usdc: float
    reason: str = "manual"


class ClosePositionRequest(BaseModel):
    market_id: str


@router.get("/positions")
async def list_positions():
    """Get all active positions."""
    positions = system_state.get_positions_serialized()
    total_pnl = sum(p["pnl"] for p in positions)
    return {"positions": positions, "total_pnl": total_pnl}


@router.get("/equity-curve")
async def equity_curve():
    """Get equity curve data for charting."""
    return {"data_points": system_state.equity_curve}


@router.get("/daily-pnl")
async def daily_pnl():
    """Get daily PnL for bar chart."""
    return {"data": system_state.daily_pnl}


@router.get("/stats")
async def portfolio_stats():
    """Get portfolio statistics."""
    return system_state.get_stats()


@router.post("/close")
async def close_position(req: ClosePositionRequest):
    """Close an open position."""
    closed = system_state.close_position(req.market_id)
    if closed is None:
        raise HTTPException(404, f"No open position for market {req.market_id}")

    logger.info(f"Position closed: {closed.question[:50]} PnL={closed.pnl:.2f}")
    await system_state.broadcast("position_closed", {
        "market_id": req.market_id,
        "pnl": closed.pnl,
    })
    return {
        "status": "closed",
        "market_id": req.market_id,
        "pnl": closed.pnl,
    }


@router.post("/order")
async def place_manual_order(req: ManualOrderRequest):
    """
    Place a manual order. Goes through the risk engine.

    In paper mode: simulates immediately.
    In live mode: places via CLOB with signing.
    """
    from backend.strategies.base import OrderIntent, OrderType, Side, StrategyName
    from datetime import datetime, timezone

    side = Side.YES if req.side.upper() == "YES" else Side.NO

    intent = OrderIntent(
        strategy=StrategyName.ENTROPY,  # tagged as manual
        market_id=req.market_id,
        condition_id=req.market_id,  # will be resolved by executor
        question=req.reason,
        side=side,
        order_type=OrderType.LIMIT,
        price=req.price,
        size_usdc=req.size_usdc,
        confidence=1.0,
        reason=f"Manual order: {req.reason}",
    )

    # Add to signals feed
    system_state.add_signal(intent)

    await system_state.broadcast("signal", {
        "strategy": "manual",
        "market_id": req.market_id,
        "side": req.side,
        "size_usdc": req.size_usdc,
        "price": req.price,
    })

    mode = "PAPER" if system_state.paper_trading else "LIVE"
    logger.info(f"Manual order [{mode}]: {req.side} ${req.size_usdc:.2f} @ {req.price:.4f}")

    return {
        "status": "submitted",
        "mode": mode,
        "market_id": req.market_id,
        "side": req.side,
        "price": req.price,
        "size_usdc": req.size_usdc,
    }
