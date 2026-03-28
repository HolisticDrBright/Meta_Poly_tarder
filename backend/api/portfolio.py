"""
Portfolio and positions API endpoints.
"""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter()


@router.get("/positions")
async def list_positions():
    """Get all active positions."""
    return {"positions": [], "total_pnl": 0.0}


@router.get("/equity-curve")
async def equity_curve():
    """Get equity curve data for charting."""
    return {"data_points": []}


@router.get("/stats")
async def portfolio_stats():
    """Get portfolio statistics."""
    return {
        "balance": 10000.0,
        "total_exposure": 0.0,
        "unrealized_pnl": 0.0,
        "realized_pnl": 0.0,
        "win_rate": 0.0,
        "sharpe_ratio": 0.0,
        "max_drawdown": 0.0,
        "trades_today": 0,
    }
