"""
Live vs Paper Comparison — tracks execution quality.

For every live trade, also simulates what paper would have done.
"""

from __future__ import annotations

from dataclasses import dataclass
from execution.models import TradeRequest, TradeResult


@dataclass
class ComparisonRecord:
    trade_id: str
    live_fill_price: float
    paper_fill_price: float
    slippage_gap_bps: float
    live_fees: float
    paper_fees: float
    effective_cost_difference: float


class ExecutionComparator:
    """Compare live execution against paper simulation."""

    def __init__(self) -> None:
        self.records: list[ComparisonRecord] = []

    async def compare(self, live_result: TradeResult, trade: TradeRequest) -> ComparisonRecord:
        """Run paper sim alongside live and compare."""
        paper_price = trade.price or 0.5
        live_price = live_result.fill_price
        gap_bps = abs(live_price - paper_price) / max(paper_price, 0.001) * 10000

        record = ComparisonRecord(
            trade_id=live_result.trade_id,
            live_fill_price=live_price,
            paper_fill_price=paper_price,
            slippage_gap_bps=round(gap_bps, 1),
            live_fees=live_result.fees_usd,
            paper_fees=0.0,
            effective_cost_difference=round(live_result.fees_usd + abs(live_price - paper_price) * (live_result.filled_size or 0), 4),
        )
        self.records.append(record)
        return record

    def get_aggregate(self, last_n: int = 100) -> dict:
        recent = self.records[-last_n:]
        if not recent:
            return {"count": 0, "avg_slippage_bps": 0, "avg_fee_impact": 0, "total_cost_difference": 0}
        return {
            "count": len(recent),
            "avg_slippage_bps": round(sum(r.slippage_gap_bps for r in recent) / len(recent), 1),
            "avg_fee_impact": round(sum(r.live_fees for r in recent) / len(recent), 4),
            "total_cost_difference": round(sum(r.effective_cost_difference for r in recent), 2),
        }
