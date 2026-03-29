"""
Entropy analysis API — top markets by information edge.
"""

from __future__ import annotations

import logging
from fastapi import APIRouter, Query

from backend.quant.entropy import market_entropy, kl_divergence, kelly_fraction
from backend.state import system_state

logger = logging.getLogger(__name__)
router = APIRouter()


def _simple_model(price: float) -> float:
    """Contrarian heuristic model — nudges toward 0.5 by 10%.
    Replaced by real AI ensemble when API keys are configured."""
    nudge = (0.5 - price) * 0.10
    return max(0.05, min(0.95, price + nudge))


@router.get("/top")
async def entropy_top(limit: int = Query(20, ge=1, le=100)):
    """
    Top markets ranked by KL divergence (information edge).

    Returns markets where the model probability diverges most
    from the market price — the biggest opportunities.
    """
    if not system_state.markets:
        return {"markets": [], "count": 0}

    scored = []
    for m in system_state.markets:
        if m.yes_price < 0.02 or m.yes_price > 0.98:
            continue

        # Use model_probability from AI ensemble if available, else heuristic
        model_p = m.model_probability if m.model_probability > 0 else _simple_model(m.yes_price)

        h = market_entropy(m.yes_price)
        kl = kl_divergence(model_p, m.yes_price)
        f = kelly_fraction(model_p, m.yes_price)
        fq = f * 0.25

        edge = abs(model_p - m.yes_price)
        if kl > 0.15:
            strength = "strong"
        elif kl > 0.08:
            strength = "moderate"
        elif kl > 0.02:
            strength = "weak"
        else:
            strength = "none"

        action = "HOLD"
        if kl > 0.05:
            action = "BUY_YES" if model_p > m.yes_price else "BUY_NO"

        scored.append({
            "id": m.market_id,
            "question": m.question,
            "category": m.category,
            "market_price": m.yes_price,
            "model_probability": round(model_p, 4),
            "entropy_bits": round(h, 4),
            "kl_divergence": round(kl, 4),
            "kelly_fraction": round(f, 4),
            "quarter_kelly": round(fq, 4),
            "edge": round(edge, 4),
            "edge_strength": strength,
            "action": action,
            "liquidity": m.liquidity,
            "volume_24h": m.volume_24h,
        })

    scored.sort(key=lambda x: x["kl_divergence"], reverse=True)
    return {"markets": scored[:limit], "count": len(scored)}


@router.get("/scan")
async def entropy_scan(
    min_kl: float = Query(0.05, ge=0),
    min_liquidity: float = Query(10000, ge=0),
):
    """Scan for actionable entropy signals above thresholds."""
    if not system_state.markets:
        return {"signals": [], "count": 0}

    signals = []
    for m in system_state.markets:
        if m.yes_price < 0.05 or m.yes_price > 0.95:
            continue
        if m.liquidity < min_liquidity:
            continue

        model_p = m.model_probability if m.model_probability > 0 else _simple_model(m.yes_price)
        kl = kl_divergence(model_p, m.yes_price)

        if kl >= min_kl:
            f = kelly_fraction(model_p, m.yes_price)
            signals.append({
                "id": m.market_id,
                "question": m.question,
                "market_price": m.yes_price,
                "model_probability": round(model_p, 4),
                "kl_divergence": round(kl, 4),
                "kelly_fraction": round(f * 0.25, 4),
                "action": "BUY_YES" if model_p > m.yes_price else "BUY_NO",
                "liquidity": m.liquidity,
            })

    signals.sort(key=lambda x: x["kl_divergence"], reverse=True)
    return {"signals": signals, "count": len(signals)}
