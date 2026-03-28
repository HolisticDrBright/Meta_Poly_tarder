"""
Signal feed API endpoints.
"""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter()


@router.get("/")
async def list_signals():
    """Get current active signals from all strategies."""
    # Placeholder — will be populated by the signal aggregator
    return {"signals": [], "count": 0}


@router.get("/entropy")
async def entropy_signals():
    """Get entropy screener signals."""
    return {"strategy": "entropy", "signals": []}


@router.get("/arb")
async def arb_signals():
    """Get arbitrage scanner signals."""
    return {"strategy": "arb", "signals": []}


@router.get("/jet")
async def jet_signals():
    """Get jet tracker signals."""
    return {"strategy": "jet", "signals": []}
