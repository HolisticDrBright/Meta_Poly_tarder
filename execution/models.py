"""
Trade request and result models for the execution layer.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum


class OrderType(str, Enum):
    LIMIT = "limit"
    MARKET = "market"


class TradeDirection(str, Enum):
    YES = "YES"
    NO = "NO"


class TradeStatus(str, Enum):
    PENDING = "pending"
    FILLED = "filled"
    PARTIALLY_FILLED = "partially_filled"
    REJECTED = "rejected"
    CANCELLED = "cancelled"
    ERROR = "error"


@dataclass
class TradeRequest:
    market_id: str
    market_title: str
    token_id: str
    direction: str  # "YES" or "NO"
    order_type: str = "limit"  # "limit" or "market"
    price: float | None = None
    size: float | None = None
    amount_usd: float | None = None
    opportunity_score: float = 0.0
    edge_estimate: float = 0.0
    fair_probability: float = 0.5
    classification: str = "PAPER-TRADE"
    decision_id: str = ""
    max_slippage_pct: float = 2.0
    timeout_seconds: int = 30
    tick_size: str = "0.01"
    neg_risk: bool = False

    @property
    def effective_amount(self) -> float:
        if self.amount_usd:
            return self.amount_usd
        if self.size and self.price:
            return self.size * self.price
        return 0.0


@dataclass
class TradeResult:
    trade_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    order_id: str | None = None
    decision_id: str = ""
    mode: str = "paper"
    market_id: str = ""
    market_title: str = ""
    token_id: str = ""
    direction: str = ""
    order_type: str = ""
    requested_price: float | None = None
    fill_price: float = 0.0
    requested_size: float = 0.0
    filled_size: float = 0.0
    fill_percentage: float = 0.0
    amount_usd: float = 0.0
    fees_usd: float = 0.0
    slippage_bps: float = 0.0
    status: str = "pending"
    error_message: str | None = None
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    paper_fill_price: float | None = None
    execution_gap: float | None = None
