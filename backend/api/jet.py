"""
Jet tracker API endpoints — flight tracking and signal data.
"""

from __future__ import annotations

from fastapi import APIRouter, Query

from backend.state import system_state

router = APIRouter()


@router.get("/active")
async def active_flights():
    """Get currently tracked flights."""
    return {"flights": system_state.jet_flights}


@router.get("/signals")
async def jet_signals():
    """Get active jet-based trading signals."""
    return {"signals": system_state.jet_signals}


@router.get("/history")
async def signal_history(limit: int = Query(50, ge=1, le=200)):
    """Get last 24h jet signal log."""
    return {"events": system_state.jet_history[:limit]}
