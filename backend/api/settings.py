"""
Live settings API — adjust bot parameters without restarting.

GET  /api/settings          → current values
POST /api/settings/update   → change one or more settings

All changes take effect immediately on the next cycle. Values are
NOT persisted to .env — a restart reloads defaults from config.
To persist permanently, edit .env on the droplet.
"""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from backend.state import system_state

logger = logging.getLogger(__name__)
router = APIRouter()


class SettingsResponse(BaseModel):
    # Sizing
    max_trade_size_usdc: float
    max_single_market_pct: float
    max_portfolio_exposure: float
    max_daily_loss_pct: float

    # Risk
    stop_loss_pct: float
    take_profit_pct: float
    trailing_stop_pct: float
    edge_capture_pct: float

    # Swing exit timing
    age_hours_full_target: float
    age_hours_min_target: float
    min_profit_to_exit: float

    # Strategy toggles
    avellaneda_enabled: bool
    entropy_enabled: bool
    theta_enabled: bool
    ensemble_enabled: bool
    binance_arb_enabled: bool

    # Mode
    paper_trading: bool


class SettingsUpdate(BaseModel):
    # Sizing (all optional — only update what you send)
    max_trade_size_usdc: Optional[float] = Field(None, ge=1, le=100)
    max_single_market_pct: Optional[float] = Field(None, ge=0.01, le=0.50)
    max_portfolio_exposure: Optional[float] = Field(None, ge=0.10, le=0.95)
    max_daily_loss_pct: Optional[float] = Field(None, ge=0.05, le=0.50)

    # Risk
    stop_loss_pct: Optional[float] = Field(None, ge=-0.80, le=-0.05)
    take_profit_pct: Optional[float] = Field(None, ge=0.05, le=1.00)
    trailing_stop_pct: Optional[float] = Field(None, ge=0.0, le=0.50)
    edge_capture_pct: Optional[float] = Field(None, ge=0.10, le=1.00)

    # Swing exit timing
    age_hours_full_target: Optional[float] = Field(None, ge=0.5, le=48)
    age_hours_min_target: Optional[float] = Field(None, ge=1, le=168)
    min_profit_to_exit: Optional[float] = Field(None, ge=0.005, le=0.20)

    # Strategy toggles
    avellaneda_enabled: Optional[bool] = None
    entropy_enabled: Optional[bool] = None
    theta_enabled: Optional[bool] = None
    ensemble_enabled: Optional[bool] = None
    binance_arb_enabled: Optional[bool] = None


def _get_scheduler():
    """Get the running scheduler instance from system_state."""
    sched = getattr(system_state, "_scheduler", None)
    if sched is None:
        # Try the older attribute name
        sched = getattr(system_state, "_trading_scheduler", None)
    return sched


@router.get("")
async def get_settings() -> SettingsResponse:
    """Get current live settings."""
    from backend.config import settings

    sched = _get_scheduler()

    # Read exit rules from the live exit manager if available
    exit_rules = None
    if sched and hasattr(sched, "exit_manager"):
        exit_rules = sched.exit_manager.rules

    # Read risk engine if available
    risk = sched.risk if sched else None

    return SettingsResponse(
        max_trade_size_usdc=(risk.max_trade_size_usdc if risk else settings.risk.max_trade_size_usdc),
        max_single_market_pct=(risk.max_single_market_pct if risk else settings.risk.max_single_market_pct),
        max_portfolio_exposure=(risk.max_portfolio_exposure if risk else settings.risk.max_portfolio_exposure),
        max_daily_loss_pct=(risk.max_daily_loss_pct if risk else settings.risk.max_daily_loss_pct),

        stop_loss_pct=(exit_rules.stop_loss_pct if exit_rules else -0.20),
        take_profit_pct=(exit_rules.take_profit_pct if exit_rules else 0.30),
        trailing_stop_pct=(exit_rules.trailing_stop_pct if exit_rules else 0.15),
        edge_capture_pct=(exit_rules.edge_capture_pct if exit_rules else 0.60),

        age_hours_full_target=(exit_rules.age_hours_full_target if exit_rules else 2.0),
        age_hours_min_target=(exit_rules.age_hours_min_target if exit_rules else 24.0),
        min_profit_to_exit=(exit_rules.min_profit_to_exit if exit_rules else 0.02),

        avellaneda_enabled=settings.strategies.avellaneda,
        entropy_enabled=settings.strategies.entropy,
        theta_enabled=settings.strategies.theta,
        ensemble_enabled=settings.strategies.ensemble,
        binance_arb_enabled=getattr(settings.strategies, "binance_arb", True),

        paper_trading=system_state.paper_trading,
    )


@router.post("/update")
async def update_settings(body: SettingsUpdate):
    """Update live settings. Changes take effect on the next cycle."""
    sched = _get_scheduler()
    if not sched:
        raise HTTPException(status_code=503, detail="Scheduler not running")

    changes = []

    # --- Sizing updates (risk engine) ---
    risk = sched.risk
    if body.max_trade_size_usdc is not None:
        risk.max_trade_size_usdc = body.max_trade_size_usdc
        changes.append(f"max_trade_size=${body.max_trade_size_usdc}")
    if body.max_single_market_pct is not None:
        risk.max_single_market_pct = body.max_single_market_pct
        changes.append(f"max_market_pct={body.max_single_market_pct:.0%}")
    if body.max_portfolio_exposure is not None:
        risk.max_portfolio_exposure = body.max_portfolio_exposure
        changes.append(f"exposure_cap={body.max_portfolio_exposure:.0%}")
    if body.max_daily_loss_pct is not None:
        risk.max_daily_loss_pct = body.max_daily_loss_pct
        changes.append(f"daily_loss_cap={body.max_daily_loss_pct:.0%}")

    # --- Exit rule updates ---
    rules = sched.exit_manager.rules
    if body.stop_loss_pct is not None:
        rules.stop_loss_pct = body.stop_loss_pct
        changes.append(f"stop_loss={body.stop_loss_pct:.0%}")
    if body.take_profit_pct is not None:
        rules.take_profit_pct = body.take_profit_pct
        changes.append(f"take_profit={body.take_profit_pct:.0%}")
    if body.trailing_stop_pct is not None:
        rules.trailing_stop_pct = body.trailing_stop_pct
        changes.append(f"trailing_stop={body.trailing_stop_pct:.0%}")
    if body.edge_capture_pct is not None:
        rules.edge_capture_pct = body.edge_capture_pct
        changes.append(f"edge_capture={body.edge_capture_pct:.0%}")
    if body.age_hours_full_target is not None:
        rules.age_hours_full_target = body.age_hours_full_target
        changes.append(f"full_target_hours={body.age_hours_full_target}")
    if body.age_hours_min_target is not None:
        rules.age_hours_min_target = body.age_hours_min_target
        changes.append(f"min_target_hours={body.age_hours_min_target}")
    if body.min_profit_to_exit is not None:
        rules.min_profit_to_exit = body.min_profit_to_exit
        changes.append(f"min_profit_exit={body.min_profit_to_exit:.1%}")

    # --- Strategy toggles (config is frozen, so set on the settings module) ---
    from backend.config import settings
    strats = settings.strategies
    # frozen dataclass — use object.__setattr__ to bypass
    if body.avellaneda_enabled is not None:
        object.__setattr__(strats, "avellaneda", body.avellaneda_enabled)
        changes.append(f"avellaneda={'ON' if body.avellaneda_enabled else 'OFF'}")
    if body.entropy_enabled is not None:
        object.__setattr__(strats, "entropy", body.entropy_enabled)
        changes.append(f"entropy={'ON' if body.entropy_enabled else 'OFF'}")
    if body.theta_enabled is not None:
        object.__setattr__(strats, "theta", body.theta_enabled)
        changes.append(f"theta={'ON' if body.theta_enabled else 'OFF'}")
    if body.ensemble_enabled is not None:
        object.__setattr__(strats, "ensemble", body.ensemble_enabled)
        changes.append(f"ensemble={'ON' if body.ensemble_enabled else 'OFF'}")
    if body.binance_arb_enabled is not None:
        object.__setattr__(strats, "binance_arb", body.binance_arb_enabled)
        changes.append(f"binance_arb={'ON' if body.binance_arb_enabled else 'OFF'}")

    if changes:
        logger.info(f"Settings updated via API: {', '.join(changes)}")

    return {"updated": changes, "message": "Changes take effect on next cycle"}
