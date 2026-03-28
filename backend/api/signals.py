"""
Signal feed API — exposes live signals from all strategies.
"""

from __future__ import annotations

from fastapi import APIRouter, Query

from backend.state import system_state

router = APIRouter()


@router.get("")
async def list_signals(limit: int = Query(50, ge=1, le=200)):
    """Get recent trading signals from all strategies."""
    signals = system_state.recent_signals[:limit]
    return {"signals": signals, "count": len(signals)}


@router.get("/entropy")
async def entropy_signals():
    """Get entropy screener signals."""
    signals = [s for s in system_state.recent_signals if s["strategy"] == "entropy"]
    return {"strategy": "entropy", "signals": signals[:30]}


@router.get("/arb")
async def arb_signals():
    """Get arbitrage scanner signals."""
    signals = [s for s in system_state.recent_signals if s["strategy"] == "arb"]
    return {"strategy": "arb", "signals": signals[:30]}


@router.get("/jet")
async def jet_signals():
    """Get jet tracker signals."""
    signals = [s for s in system_state.recent_signals if s["strategy"] == "jet"]
    return {"strategy": "jet", "signals": signals[:30]}


@router.get("/all-strategies")
async def all_strategy_signals():
    """Get signals grouped by strategy."""
    grouped: dict[str, list] = {}
    for s in system_state.recent_signals[:100]:
        strategy = s["strategy"]
        if strategy not in grouped:
            grouped[strategy] = []
        grouped[strategy].append(s)
    return {"by_strategy": grouped, "total": len(system_state.recent_signals)}
