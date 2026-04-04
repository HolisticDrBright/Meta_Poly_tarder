"""
Admin endpoints.

Destructive operations live here. Every endpoint requires a confirmation
token in the request body so accidental clicks can't wipe real data.

The token is generated fresh per backend restart and logged once at INFO
level — grab it from the logs the first time you need it.
"""

from __future__ import annotations

import logging
import secrets
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

logger = logging.getLogger(__name__)
router = APIRouter()

# Per-process confirmation token. Rotates on every restart.
_ADMIN_TOKEN: str = secrets.token_urlsafe(24)
logger.info(f"ADMIN TOKEN (rotates on restart): {_ADMIN_TOKEN}")


class ResetRequest(BaseModel):
    confirm_token: str
    reset_positions: bool = True       # also clear open positions
    keep_intelligence: bool = True     # keep prediction_intelligence decisions/outcomes


@router.get("/admin-token-hint")
async def admin_token_hint():
    """Returns a HINT so you can verify the server is running.
    Never returns the full token — grep the backend log for 'ADMIN TOKEN'."""
    return {
        "hint": f"{_ADMIN_TOKEN[:4]}…{_ADMIN_TOKEN[-4:]}",
        "length": len(_ADMIN_TOKEN),
        "note": "Full token is in the backend log, line 'ADMIN TOKEN (rotates on restart):'",
    }


@router.post("/reset-paper-trades")
async def reset_paper_trades(req: ResetRequest):
    """
    Wipe paper-trading history so we can start measuring performance
    cleanly after the P&L sign-flip fix. This does NOT touch:
      - Live trades (live wallet balances / real CLOB orders)
      - Prediction intelligence decisions/outcomes (learning loop data)
        unless keep_intelligence=False is explicitly passed
      - Market snapshots / whale tracking / signals feed

    What it DOES clear:
      - DuckDB `trades` table (the rows backing Dashboard/History stats)
      - In-memory realized_pnl / unrealized_pnl / trades_today / exposure
      - Open positions (mark-to-market closed with no P&L recorded)
      - Equity curve and daily P&L breakdown in memory
    """
    if not secrets.compare_digest(req.confirm_token, _ADMIN_TOKEN):
        raise HTTPException(403, "Invalid confirm_token — see backend log")

    result: dict = {"cleared": {}}

    # 1. DuckDB trades table
    try:
        from backend.state import system_state
        duckdb = getattr(system_state, "_duckdb", None)
        if duckdb is not None and duckdb._conn is not None:
            before = duckdb._conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0]
            duckdb._conn.execute("DELETE FROM trades")
            try:
                duckdb._conn.execute("CHECKPOINT")
            except Exception:
                pass
            after = duckdb._conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0]
            result["cleared"]["duckdb_trades"] = {"before": before, "after": after}
        else:
            result["cleared"]["duckdb_trades"] = "no connection"
    except Exception as e:
        logger.error(f"Admin reset: DuckDB clear failed: {e}")
        result["cleared"]["duckdb_trades"] = f"error: {e}"

    # 2. In-memory state
    try:
        from backend.state import system_state
        before_pnl = system_state.realized_pnl
        before_positions = len(system_state.positions)
        system_state.realized_pnl = 0.0
        system_state.unrealized_pnl = 0.0
        system_state.trades_today = 0
        system_state.total_exposure = 0.0
        if req.reset_positions:
            system_state.positions = []
        if hasattr(system_state, "equity_curve"):
            system_state.equity_curve = []
        if hasattr(system_state, "daily_pnl"):
            system_state.daily_pnl = []
        result["cleared"]["in_memory"] = {
            "realized_pnl_before": round(before_pnl, 2),
            "positions_before": before_positions,
            "realized_pnl_after": 0.0,
            "positions_after": len(system_state.positions),
        }
    except Exception as e:
        logger.error(f"Admin reset: state clear failed: {e}")
        result["cleared"]["in_memory"] = f"error: {e}"

    # 3. Optionally clear prediction_intelligence (off by default)
    if not req.keep_intelligence:
        try:
            from prediction_intelligence.logger import DecisionLogger
            dl = DecisionLogger()
            conn = dl._ensure_conn()
            d_before = conn.execute("SELECT COUNT(*) FROM decision_log").fetchone()[0]
            o_before = conn.execute("SELECT COUNT(*) FROM outcome_log").fetchone()[0]
            conn.execute("DELETE FROM outcome_log")
            conn.execute("DELETE FROM decision_log")
            result["cleared"]["prediction_intelligence"] = {
                "decisions_cleared": d_before,
                "outcomes_cleared": o_before,
            }
        except Exception as e:
            logger.error(f"Admin reset: PI clear failed: {e}")
            result["cleared"]["prediction_intelligence"] = f"error: {e}"
    else:
        result["cleared"]["prediction_intelligence"] = "preserved"

    # 4. Clear executor's internal dedupe state so specialists re-fire
    try:
        from backend.strategies.specialists.orchestrator import (
            get_specialist_orchestrator,
        )
        orch = get_specialist_orchestrator()
        dedupe_count = len(orch._last_run)
        orch._last_run.clear()
        result["cleared"]["specialist_dedupe"] = {"entries_cleared": dedupe_count}
    except Exception as e:
        result["cleared"]["specialist_dedupe"] = f"error: {e}"

    logger.warning(f"ADMIN RESET: paper trading history wiped. {result}")
    result["status"] = "ok"
    return result
