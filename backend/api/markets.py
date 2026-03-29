"""
Market data API endpoints — with AI debate trigger and order book.
"""

from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Query, HTTPException
from pydantic import BaseModel

from backend.data_layer.gamma_client import GammaClient
from backend.quant.entropy import market_entropy, score_market
from backend.state import system_state

logger = logging.getLogger(__name__)
router = APIRouter()
_gamma = GammaClient()


class DebateRequest(BaseModel):
    model_probability: float = 0.5
    context: str = ""


@router.get("")
async def list_markets(
    limit: int = Query(50, ge=1, le=200),
    min_liquidity: float = Query(0, ge=0),
    active: bool = True,
):
    """List active markets — uses cached scheduler data if available, else Gamma API."""
    # Prefer cached markets from the scheduler (faster, already enriched)
    if system_state.markets:
        markets = system_state.markets
        if min_liquidity > 0:
            markets = [m for m in markets if m.liquidity >= min_liquidity]
        return [
            {
                "id": m.market_id,
                "condition_id": m.condition_id,
                "question": m.question,
                "category": m.category,
                "yes_price": m.yes_price,
                "no_price": m.no_price,
                "liquidity": m.liquidity,
                "volume_24h": m.volume_24h,
                "end_date": m.end_date.isoformat() if m.end_date else None,
                "spread": m.spread,
                "entropy_bits": m.entropy_bits,
                "best_bid": m.best_bid,
                "best_ask": m.best_ask,
                "arb_edge": m.arb_edge,
            }
            for m in markets[:limit]
        ]

    # Fallback to direct Gamma API call
    try:
        gamma_markets = await _gamma.get_markets(limit=limit, active=active)
        if min_liquidity > 0:
            gamma_markets = [m for m in gamma_markets if m.liquidity >= min_liquidity]
        if gamma_markets:
            return [
                {
                    "id": m.id,
                    "condition_id": m.condition_id,
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
                for m in gamma_markets
            ]
    except Exception as e:
        logger.error(f"Gamma API fallback failed: {e}")

    # Last resort: return empty list (not 500)
    return []


@router.get("/{market_id}")
async def get_market(market_id: str):
    """Get detailed market data including entropy metrics."""
    # Check cached state first
    cached = system_state.get_market(market_id)
    if cached:
        return {
            "id": cached.market_id,
            "condition_id": cached.condition_id,
            "question": cached.question,
            "category": cached.category,
            "yes_price": cached.yes_price,
            "no_price": cached.no_price,
            "best_bid": cached.best_bid,
            "best_ask": cached.best_ask,
            "spread": cached.spread,
            "liquidity": cached.liquidity,
            "volume_24h": cached.volume_24h,
            "end_date": cached.end_date.isoformat() if cached.end_date else None,
            "arb_edge": cached.arb_edge,
            "entropy_bits": cached.entropy_bits,
            "model_probability": cached.model_probability,
            "kl_divergence": cached.kl_divergence,
        }

    # Fallback
    try:
        m = await _gamma.get_market(market_id)
        if m:
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
                "entropy_bits": market_entropy(m.yes_price),
            }
    except Exception as e:
        logger.error(f"Market detail fetch failed: {e}")

    raise HTTPException(404, "Market not found")


@router.get("/{market_id}/entropy")
async def market_entropy_detail(
    market_id: str,
    model_probability: float = Query(0.5, ge=0.01, le=0.99),
    bankroll: float = Query(10000, ge=100),
):
    """Compute full entropy scoring for a market."""
    # Try cached first
    cached = system_state.get_market(market_id)
    if cached:
        scored = score_market(
            market_id=cached.market_id,
            question=cached.question,
            market_price=cached.yes_price,
            model_probability=model_probability,
            bankroll=bankroll,
        )
    else:
        try:
            m = await _gamma.get_market(market_id)
            if not m:
                raise HTTPException(404, "Market not found")
            scored = score_market(
                market_id=m.id,
                question=m.question,
                market_price=m.yes_price,
                model_probability=model_probability,
                bankroll=bankroll,
            )
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(503, f"Market data unavailable: {e}")

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


@router.post("/{market_id}/debate")
async def run_debate(market_id: str, req: DebateRequest):
    """
    Run the 7-agent AI Debate Floor on a market.

    Calls Claude + GPT-4o ensemble and returns structured result.
    """
    cached = system_state.get_market(market_id)
    if not cached:
        raise HTTPException(404, "Market not found — load markets first")

    try:
        from backend.config import settings
        from backend.strategies.ensemble_ai import EnsembleAI

        ensemble = EnsembleAI(
            anthropic_api_key=settings.ai.anthropic_api_key,
            openai_api_key=settings.ai.openai_api_key,
        )
        result = await ensemble.run_ensemble(
            market=cached,
            context=req.context,
        )

        # Update model probability in cached state
        cached.model_probability = result.ensemble_probability

        return {
            "market_id": market_id,
            "question": cached.question,
            "market_price": cached.yes_price,
            "ensemble_probability": result.ensemble_probability,
            "ensemble_confidence": result.ensemble_confidence,
            "recommended_action": result.recommended_action,
            "spread": result.spread,
            "debates": [
                {
                    "model": d.model_source,
                    "probability": d.final_probability,
                    "confidence": d.confidence,
                    "agents": d.agents,
                }
                for d in result.debates
            ],
        }
    except Exception as e:
        logger.error(f"Debate failed: {e}")
        raise HTTPException(500, f"Debate failed: {str(e)}")


@router.get("/{market_id}/orderbook")
async def get_orderbook(market_id: str):
    """
    Fetch live L2 order book from the CLOB REST API.

    Returns bids, asks, mid_price, spread, and depth summary.
    """
    from backend.data_layer.clob_ws import CLOBRestClient

    cached = system_state.get_market(market_id)
    clob = CLOBRestClient()
    try:
        token_id = cached.condition_id if cached else market_id

        book_data = await clob.get_order_book(token_id)

        bids = book_data.get("bids", [])
        asks = book_data.get("asks", [])

        parsed_bids = [
            [float(b.get("price", b[0]) if isinstance(b, dict) else b[0]),
             float(b.get("size", b[1]) if isinstance(b, dict) else b[1])]
            for b in bids
        ] if bids else []

        parsed_asks = [
            [float(a.get("price", a[0]) if isinstance(a, dict) else a[0]),
             float(a.get("size", a[1]) if isinstance(a, dict) else a[1])]
            for a in asks
        ] if asks else []

        parsed_bids.sort(key=lambda x: x[0], reverse=True)
        parsed_asks.sort(key=lambda x: x[0])

        best_bid = parsed_bids[0][0] if parsed_bids else 0
        best_ask = parsed_asks[0][0] if parsed_asks else 1
        mid = (best_bid + best_ask) / 2 if (parsed_bids and parsed_asks) else 0.5
        spread = best_ask - best_bid if (parsed_bids and parsed_asks) else 0

        bid_depth = sum(b[1] for b in parsed_bids[:10])
        ask_depth = sum(a[1] for a in parsed_asks[:10])

        return {
            "market_id": market_id,
            "bids": parsed_bids[:20],
            "asks": parsed_asks[:20],
            "mid_price": mid,
            "spread": spread,
            "best_bid": best_bid,
            "best_ask": best_ask,
            "depth_10": {"bid": bid_depth, "ask": ask_depth},
        }
    except Exception as e:
        logger.error(f"Order book fetch failed: {e}")
        if cached:
            return {
                "market_id": market_id,
                "bids": [],
                "asks": [],
                "mid_price": cached.mid_price,
                "spread": cached.spread,
                "best_bid": cached.best_bid,
                "best_ask": cached.best_ask,
                "depth_10": {"bid": 0, "ask": 0},
                "note": "Fallback from cached data",
            }
        raise HTTPException(503, f"Order book unavailable: {str(e)}")
    finally:
        await clob.close()
