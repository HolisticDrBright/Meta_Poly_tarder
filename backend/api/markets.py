"""
Market data API endpoints.
"""

from __future__ import annotations

from fastapi import APIRouter, Query

from backend.data_layer.gamma_client import GammaClient
from backend.quant.entropy import market_entropy, kl_divergence, score_market

router = APIRouter()
_gamma = GammaClient()


@router.get("/")
async def list_markets(
    limit: int = Query(50, ge=1, le=200),
    min_liquidity: float = Query(0, ge=0),
    active: bool = True,
):
    """List active markets from Gamma API."""
    markets = await _gamma.get_markets(limit=limit, active=active)
    if min_liquidity > 0:
        markets = [m for m in markets if m.liquidity >= min_liquidity]
    return [
        {
            "id": m.id,
            "question": m.question,
            "category": m.category,
            "yes_price": m.yes_price,
            "no_price": m.no_price,
            "liquidity": m.liquidity,
            "volume_24h": m.volume_24h,
            "end_date": m.end_date.isoformat() if m.end_date else None,
            "spread": m.spread,
            "entropy_bits": market_entropy(m.yes_price),
        }
        for m in markets
    ]


@router.get("/{market_id}")
async def get_market(market_id: str):
    """Get detailed market data including entropy metrics."""
    m = await _gamma.get_market(market_id)
    if not m:
        return {"error": "Market not found"}

    h = market_entropy(m.yes_price)
    return {
        "id": m.id,
        "condition_id": m.condition_id,
        "question": m.question,
        "category": m.category,
        "yes_price": m.yes_price,
        "no_price": m.no_price,
        "best_bid": m.best_bid,
        "best_ask": m.best_ask,
        "spread": m.spread,
        "liquidity": m.liquidity,
        "volume": m.volume,
        "volume_24h": m.volume_24h,
        "end_date": m.end_date.isoformat() if m.end_date else None,
        "arb_edge": 1.0 - m.yes_price - m.no_price,
        "entropy_bits": h,
    }


@router.get("/{market_id}/entropy")
async def market_entropy_detail(
    market_id: str,
    model_probability: float = Query(0.5, ge=0.01, le=0.99),
    bankroll: float = Query(10000, ge=100),
):
    """Compute full entropy scoring for a market."""
    m = await _gamma.get_market(market_id)
    if not m:
        return {"error": "Market not found"}

    scored = score_market(
        market_id=m.id,
        question=m.question,
        market_price=m.yes_price,
        model_probability=model_probability,
        bankroll=bankroll,
    )
    return {
        "market_id": scored.market_id,
        "question": scored.question,
        "market_price": scored.market_price,
        "model_probability": scored.model_probability,
        "entropy_bits": scored.entropy_bits,
        "kl_divergence": scored.kl_div_bits,
        "kelly_fraction": scored.kelly_f,
        "quarter_kelly": scored.quarter_kelly_f,
        "entropy_efficiency": scored.entropy_efficiency_r,
        "recommended_action": scored.recommended_action.value,
        "position_size_usdc": scored.position_size_usdc,
        "edge_strength": scored.edge_strength,
    }
