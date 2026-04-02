"""
Safety configuration for the execution layer.
Tuned for $300 starting capital. All values overridable via env vars.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


def _f(var: str, default: float) -> float:
    v = os.getenv(var)
    return float(v) if v else default


def _i(var: str, default: int) -> int:
    v = os.getenv(var)
    return int(v) if v else default


@dataclass
class SafetyConfig:
    STARTING_CAPITAL: float = _f("EXEC_STARTING_CAPITAL", 300.0)
    MAX_TRADE_SIZE_USD: float = _f("EXEC_MAX_TRADE_SIZE", 30.0)
    MAX_DAILY_LOSS_USD: float = _f("EXEC_MAX_DAILY_LOSS", 45.0)
    MAX_DAILY_TRADES: int = _i("EXEC_MAX_DAILY_TRADES", 50)
    MAX_PORTFOLIO_EXPOSURE_USD: float = _f("EXEC_MAX_EXPOSURE", 250.0)
    MAX_SINGLE_MARKET_PCT: float = _f("EXEC_MAX_SINGLE_MARKET", 0.15)
    MAX_CORRELATED_EXPOSURE_PCT: float = _f("EXEC_MAX_CORRELATED", 0.30)
    MAX_DRAWDOWN_PCT: float = _f("EXEC_MAX_DRAWDOWN", 0.35)
    MIN_EDGE_TO_TRADE: float = _f("EXEC_MIN_EDGE", 0.03)
    MIN_OPPORTUNITY_SCORE: float = _f("EXEC_MIN_OPP_SCORE", 60)
    DEFAULT_ORDER_TYPE: str = os.getenv("EXEC_ORDER_TYPE", "limit")
    MAX_SLIPPAGE_PCT: float = _f("EXEC_MAX_SLIPPAGE", 2.0)
    ORDER_TIMEOUT_SECONDS: int = _i("EXEC_ORDER_TIMEOUT", 60)
