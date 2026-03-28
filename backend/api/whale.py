"""
Whale tracker and copy trade API endpoints.
"""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter()


@router.get("/leaderboard")
async def leaderboard():
    """Get top traders from Polymarket leaderboard."""
    return {"entries": []}


@router.get("/trades")
async def whale_trades():
    """Get recent whale trades."""
    return {"trades": []}


@router.get("/copy-queue")
async def copy_queue():
    """Get pending copy trade intents."""
    return {"queue": []}


@router.get("/smart-money-index")
async def smart_money_index():
    """Get Smart Money Index (0-100)."""
    return {"smi": 50, "bias": "neutral"}
