"""
Whale tracker, copy trading, and leaderboard API endpoints.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from backend.state import system_state

logger = logging.getLogger(__name__)
router = APIRouter()


class CopyActionRequest(BaseModel):
    index: int


class CopyTargetModeRequest(BaseModel):
    target_name: str
    auto_copy: bool


@router.get("/leaderboard")
async def leaderboard():
    """Get top traders from Polymarket leaderboard."""
    return {"entries": system_state.leaderboard}


@router.get("/trades")
async def whale_trades(limit: int = Query(50, ge=1, le=200)):
    """Get recent whale trades."""
    return {"trades": system_state.whale_trades[:limit]}


@router.get("/copy-queue")
async def copy_queue():
    """Get pending copy trade intents awaiting confirmation."""
    return {"queue": system_state.copy_queue}


@router.post("/copy-queue/execute")
async def execute_copy(req: CopyActionRequest):
    """Execute a pending copy trade."""
    item = system_state.remove_from_copy_queue(req.index)
    if item is None:
        raise HTTPException(404, "Copy trade not found in queue")

    logger.info(f"Copy trade executed: {item.get('question', '')[:50]}")
    item["status"] = "executed"
    system_state.recent_signals.insert(0, item)
    await system_state.broadcast("copy_executed", item)
    return {"status": "executed", "trade": item}


@router.post("/copy-queue/skip")
async def skip_copy(req: CopyActionRequest):
    """Skip/reject a pending copy trade."""
    item = system_state.remove_from_copy_queue(req.index)
    if item is None:
        raise HTTPException(404, "Copy trade not found in queue")

    logger.info(f"Copy trade skipped: {item.get('question', '')[:50]}")
    return {"status": "skipped", "trade": item}


@router.put("/targets/mode")
async def set_target_mode(req: CopyTargetModeRequest):
    """Set a copy target to auto-copy or manual-confirm mode."""
    logger.info(f"Copy target {req.target_name} set to {'AUTO' if req.auto_copy else 'MANUAL'}")
    return {
        "target": req.target_name,
        "auto_copy": req.auto_copy,
    }


@router.get("/smart-money-index")
async def smart_money_index():
    """Get Smart Money Index (0-100)."""
    smi = system_state.smart_money_index
    bias = "bullish" if smi > 60 else "bearish" if smi < 40 else "neutral"
    return {"smi": smi, "bias": bias}
