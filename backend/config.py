"""
Central configuration — reads .env and exposes typed settings.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")


def _bool(val: str | None, default: bool = False) -> bool:
    if val is None:
        return default
    return val.strip().lower() in ("true", "1", "yes")


def _float(val: str | None, default: float = 0.0) -> float:
    if val is None:
        return default
    return float(val)


def _int(val: str | None, default: int = 0) -> int:
    if val is None:
        return default
    return int(val)


@dataclass(frozen=True)
class TradingConfig:
    private_key: str = os.getenv("POLYMARKET_PRIVATE_KEY", "")
    wallet_address: str = os.getenv("POLYMARKET_WALLET_ADDRESS", "")
    signature_type: int = _int(os.getenv("SIGNATURE_TYPE"), 0)
    paper_trading: bool = _bool(os.getenv("PAPER_TRADING"), True)


@dataclass(frozen=True)
class AIConfig:
    anthropic_api_key: str = os.getenv("ANTHROPIC_API_KEY", "")
    openai_api_key: str = os.getenv("OPENAI_API_KEY", "")
    mirofish_agents: int = _int(os.getenv("MIROFISH_AGENTS"), 20)
    ensemble_timeout: int = _int(os.getenv("AI_ENSEMBLE_TIMEOUT"), 30)


@dataclass(frozen=True)
class StrategyFlags:
    entropy: bool = _bool(os.getenv("STRATEGY_ENTROPY"), True)
    avellaneda: bool = _bool(os.getenv("STRATEGY_AVELLANEDA"), True)
    arb: bool = _bool(os.getenv("STRATEGY_ARB"), True)
    ensemble: bool = _bool(os.getenv("STRATEGY_ENSEMBLE"), True)
    jet: bool = _bool(os.getenv("STRATEGY_JET"), True)
    copy: bool = _bool(os.getenv("STRATEGY_COPY"), True)
    theta: bool = _bool(os.getenv("STRATEGY_THETA"), True)


@dataclass(frozen=True)
class QuantParams:
    entropy_threshold: float = _float(os.getenv("ENTROPY_THRESHOLD"), 0.08)
    entropy_efficiency_max: float = _float(os.getenv("ENTROPY_EFFICIENCY_MAX"), 0.35)
    kelly_fraction: float = _float(os.getenv("KELLY_FRACTION"), 0.25)
    as_gamma: float = _float(os.getenv("AS_GAMMA"), 0.1)
    as_kappa: float = _float(os.getenv("AS_KAPPA"), 1.5)
    as_session_hours: int = _int(os.getenv("AS_SESSION_HOURS"), 24)
    vpin_pause_threshold: float = _float(os.getenv("VPIN_PAUSE_THRESHOLD"), 0.70)
    min_arb_edge: float = _float(os.getenv("MIN_ARB_EDGE"), 0.015)


@dataclass(frozen=True)
class CopyConfig:
    targets: list[str] = field(
        default_factory=lambda: [
            t.strip()
            for t in os.getenv("COPY_TARGETS", "").split(",")
            if t.strip()
        ]
    )
    ratio: float = _float(os.getenv("COPY_RATIO"), 0.10)
    max_size_usdc: float = _float(os.getenv("COPY_MAX_SIZE_USDC"), 75)
    confluence_required: bool = _bool(os.getenv("COPY_CONFLUENCE_REQUIRED"), False)


@dataclass(frozen=True)
class RiskConfig:
    max_portfolio_exposure: float = _float(os.getenv("MAX_PORTFOLIO_EXPOSURE"), 0.80)
    max_single_market_pct: float = _float(os.getenv("MAX_SINGLE_MARKET_PCT"), 0.15)
    max_daily_loss_pct: float = _float(os.getenv("MAX_DAILY_LOSS_PCT"), 0.10)
    max_trade_size_usdc: float = _float(os.getenv("MAX_TRADE_SIZE_USDC"), 150)
    min_balance_usdc: float = _float(os.getenv("MIN_BALANCE_USDC"), 10)


@dataclass(frozen=True)
class AlertConfig:
    telegram_bot_token: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
    telegram_chat_id: str = os.getenv("TELEGRAM_CHAT_ID", "")
    jet_manual_confirm: bool = _bool(os.getenv("JET_MANUAL_CONFIRM"), True)


@dataclass(frozen=True)
class Settings:
    trading: TradingConfig = field(default_factory=TradingConfig)
    ai: AIConfig = field(default_factory=AIConfig)
    strategies: StrategyFlags = field(default_factory=StrategyFlags)
    quant: QuantParams = field(default_factory=QuantParams)
    copy: CopyConfig = field(default_factory=CopyConfig)
    risk: RiskConfig = field(default_factory=RiskConfig)
    alerts: AlertConfig = field(default_factory=AlertConfig)


settings = Settings()
