"""
Jet tracker API endpoints.
"""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter()


@router.get("/active")
async def active_flights():
    """Get currently tracked flights."""
    return {"flights": []}


@router.get("/signals")
async def jet_signals():
    """Get active jet-based trading signals."""
    return {"signals": []}


@router.get("/history")
async def signal_history():
    """Get last 24h jet signal log."""
    return {"events": []}
