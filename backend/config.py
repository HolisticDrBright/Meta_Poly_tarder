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
    relayer_api_key: str = os.getenv("POLYMARKET_RELAYER_API_KEY", "")
    signature_type: int = _int(os.getenv("SIGNATURE_TYPE"), 0)

    # PAPER_TRADING: controls whether strategy fills go to the paper ledger
    # or the real Polymarket CLOB. Defaults True — safe by default.
    paper_trading: bool = _bool(os.getenv("PAPER_TRADING"), True)

    # POLYMARKET_LIVE: secondary hard gate for live order placement.
    # Both POLYMARKET_LIVE=true AND PAPER_TRADING=false must be set to
    # allow real orders to reach the CLOB. Either flag alone is not enough.
    # Defaults False — users must explicitly opt in to live trading.
    polymarket_live: bool = _bool(os.getenv("POLYMARKET_LIVE"), False)

    starting_capital: float = _float(os.getenv("STARTING_CAPITAL"), 300.0)


@dataclass(frozen=True)
class AIConfig:
    anthropic_api_key: str = os.getenv("ANTHROPIC_API_KEY", "")
    openai_api_key: str = os.getenv("OPENAI_API_KEY", "")
    ensemble_timeout: int = _int(os.getenv("AI_ENSEMBLE_TIMEOUT"), 30)


@dataclass(frozen=True)
class SpecialistConfig:
    """Specialist layer (news, on-chain, history, swarm)."""
    min_edge: float = _float(os.getenv("SPECIALIST_MIN_EDGE"), 0.05)
    dedupe_minutes: int = _int(os.getenv("SPECIALIST_DEDUPE_MINUTES"), 120)
    alchemy_polygon_url: str = os.getenv("ALCHEMY_POLYGON_URL", "")
    # MiroFish swarm — default OFF until the cheaper strategies are proven
    # profitable. ~$4.50/day at 500 agents on gpt-4o-mini, not worth it
    # until there's edge to amplify. Re-enable with MIROFISH_ENABLED=true.
    mirofish_enabled: bool = _bool(os.getenv("MIROFISH_ENABLED"), False)
    mirofish_agents: int = _int(os.getenv("MIROFISH_AGENTS"), 500)
    mirofish_model: str = os.getenv("MIROFISH_MODEL", "gpt-4o-mini")
    mirofish_weight: float = _float(os.getenv("MIROFISH_WEIGHT"), 0.05)
    mirofish_max_concurrency: int = _int(os.getenv("MIROFISH_MAX_CONCURRENCY"), 25)


@dataclass(frozen=True)
class StrategyFlags:
    entropy: bool = _bool(os.getenv("STRATEGY_ENTROPY"), True)
    # Avellaneda-Stoikov MM: ENABLED by default. An earlier comment here
    # said "unprofitable" based on 48 paper trades that netted ~$0.10 per
    # round trip. The friend running a live production bot with the same
    # strategy has 434 trades and +$331 P&L — the paper trader simulates
    # fills at mid-price so A-S looks breakeven, but in live it captures
    # real bid/ask spread and is the single largest profit source. The
    # learning loop must not starve it below its base weight (enforced
    # via per-strategy floor in backend/learning/weights.py).
    avellaneda: bool = _bool(os.getenv("STRATEGY_AVELLANEDA"), True)
    arb: bool = _bool(os.getenv("STRATEGY_ARB"), True)
    # Binance-vs-Polymarket crypto price arb — new primary profit engine
    # since A-S is disabled. Zero AI cost, pure quant, runs every 15s.
    binance_arb: bool = _bool(os.getenv("STRATEGY_BINANCE_ARB"), True)
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
    # Defaults tightened to match a production bot's proven sizing on
    # Sizing for a $300 bankroll. Previous $4/trade was too small to
    # cover API costs (~$0.01/trade for Claude+GPT-4o). At $15/trade
    # with 57% win rate, each winning trade earns ~$2-5 which covers
    # API costs and generates real profit.
    #   $15/trade × 10% market cap = $30 max per market = 2 positions
    #   $300 × 75% exposure = $225 deployed across ~7-8 markets
    max_portfolio_exposure: float = _float(os.getenv("MAX_PORTFOLIO_EXPOSURE"), 0.75)
    max_single_market_pct: float = _float(os.getenv("MAX_SINGLE_MARKET_PCT"), 0.10)
    max_daily_loss_pct: float = _float(os.getenv("MAX_DAILY_LOSS_PCT"), 0.15)
    max_trade_size_usdc: float = _float(os.getenv("MAX_TRADE_SIZE_USDC"), 15)
    min_balance_usdc: float = _float(os.getenv("MIN_BALANCE_USDC"), 10)


@dataclass(frozen=True)
class AlertConfig:
    telegram_bot_token: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
    telegram_chat_id: str = os.getenv("TELEGRAM_CHAT_ID", "")
    jet_manual_confirm: bool = _bool(os.getenv("JET_MANUAL_CONFIRM"), True)


@dataclass(frozen=True)
class VPNConfig:
    proxy_url: str = os.getenv("PROXY_URL", "")
    check_url: str = os.getenv("VPN_CHECK_URL", "https://ipinfo.io/json")

    # VPN_REQUIRED: optional — users route their own traffic at the OS level
    # if desired. When False (default), VPNGuard is a no-op and the app
    # starts cleanly without any proxy configured.
    # When True, the guard verifies the configured PROXY_URL is reachable
    # on startup and every check_interval seconds at runtime.
    required: bool = _bool(os.getenv("VPN_REQUIRED"), False)

    check_interval: int = _int(os.getenv("VPN_CHECK_INTERVAL_SECONDS"), 300)

    @property
    def enabled(self) -> bool:
        return bool(self.proxy_url) and self.required


@dataclass(frozen=True)
class Settings:
    trading: TradingConfig = field(default_factory=TradingConfig)
    ai: AIConfig = field(default_factory=AIConfig)
    strategies: StrategyFlags = field(default_factory=StrategyFlags)
    quant: QuantParams = field(default_factory=QuantParams)
    copy: CopyConfig = field(default_factory=CopyConfig)
    risk: RiskConfig = field(default_factory=RiskConfig)
    alerts: AlertConfig = field(default_factory=AlertConfig)
    vpn: VPNConfig = field(default_factory=VPNConfig)
    specialists: SpecialistConfig = field(default_factory=SpecialistConfig)


settings = Settings()
