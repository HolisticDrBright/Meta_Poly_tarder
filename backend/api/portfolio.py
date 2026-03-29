"""
Portfolio, positions, and trade execution API endpoints.

Merges local tracked positions with real CLOB positions when
L2 auth is configured.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend.state import system_state

logger = logging.getLogger(__name__)
router = APIRouter()

# Lazy-init CLOB auth client
_clob_auth = None


def _get_clob_auth():
    global _clob_auth
    if _clob_auth is None:
        from backend.config import settings
        from backend.data_layer.clob_auth import CLOBAuthClient
        _clob_auth = CLOBAuthClient(
            private_key=settings.trading.private_key,
            wallet_address=settings.trading.wallet_address,
            signature_type=settings.trading.signature_type,
        )
    return _clob_auth


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
    """
    Get all open positions — merges local tracked + real CLOB positions.

    If L2 auth is configured, fetches real positions from the CLOB API.
    Otherwise returns locally tracked positions from paper/live execution.
    """
    local_positions = system_state.get_positions_serialized()

    # Try to fetch real positions from CLOB
    clob = _get_clob_auth()
    clob_positions = []
    if clob.available:
        try:
            clob_positions = await clob.get_positions()
        except Exception as e:
            logger.debug(f"CLOB positions fetch failed (using local only): {e}")

    # Merge: local positions + any CLOB positions not already tracked
    local_ids = {p["market_id"] for p in local_positions}
    for cp in clob_positions:
        if cp.get("market_id") and cp["market_id"] not in local_ids:
            local_positions.append({
                "id": len(local_positions),
                "market_id": cp["market_id"],
                "question": cp.get("question", ""),
                "side": cp.get("side", "YES"),
                "entry_price": cp.get("price", 0),
                "size_usdc": cp.get("size", 0),
                "current_price": cp.get("price", 0),
                "strategy": "clob",
                "opened_at": "",
                "pnl": 0,
                "pnl_pct": 0,
                "hours_to_close": None,
                "source": "clob_api",
            })

    total_pnl = sum(p.get("pnl", 0) for p in local_positions)
    return {"positions": local_positions, "total_pnl": total_pnl}


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
    """
    Portfolio statistics — includes real CLOB balance when available.
    """
    stats = system_state.get_stats()

    # Try to fetch real balance from CLOB
    clob = _get_clob_auth()
    if clob.available:
        try:
            real_balance = await clob.get_balance()
            if real_balance > 0:
                stats["clob_balance"] = real_balance
                stats["balance"] = real_balance
        except Exception:
            pass

    return stats


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

    side = Side.YES if req.side.upper() == "YES" else Side.NO

    intent = OrderIntent(
        strategy=StrategyName.ENTROPY,
        market_id=req.market_id,
        condition_id=req.market_id,
        question=req.reason,
        side=side,
        order_type=OrderType.LIMIT,
        price=req.price,
        size_usdc=req.size_usdc,
        confidence=1.0,
        reason=f"Manual order: {req.reason}",
    )

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
