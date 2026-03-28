"""
FastAPI backend — WebSocket streaming + REST endpoints.

Run: uvicorn backend.main:app --host 0.0.0.0 --port 8000 --reload
"""

from __future__ import annotations

import asyncio
import json
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from backend.api import markets, signals, portfolio, whale, jet
from backend.config import settings
from backend.data_layer.storage import DuckDBStorage, SQLiteState
from backend.observability.logger import setup_logging
from backend.state import system_state

setup_logging()
logger = logging.getLogger(__name__)


# ── shared state ────────────────────────────────────────────────────
duckdb = DuckDBStorage()
sqlite = SQLiteState()

# WebSocket connections for live streaming
ws_connections: set[WebSocket] = set()


async def broadcast(event_type: str, data: dict) -> None:
    """Broadcast an event to all connected WebSocket clients. Never raises."""
    if not ws_connections:
        return
    message = {"type": event_type, "data": data}
    disconnected: set[WebSocket] = set()
    for ws in list(ws_connections):  # copy to avoid mutation during iteration
        try:
            await ws.send_json(message)
        except Exception:
            disconnected.add(ws)
    ws_connections.difference_update(disconnected)


# Wire the broadcast function into the shared state
system_state.set_broadcast(broadcast)

trading_scheduler = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown hooks."""
    global trading_scheduler
    logger.info("=" * 60)
    logger.info("  POLYMARKET INTELLIGENCE SYSTEM")
    logger.info(f"  Paper trading: {settings.trading.paper_trading}")
    logger.info("=" * 60)
    duckdb.connect()
    sqlite.connect()

    # Start the trading scheduler
    try:
        from backend.scheduler import TradingScheduler

        trading_scheduler = TradingScheduler()
        trading_scheduler.start()
        system_state.scheduler_running = True
        logger.info("Trading scheduler started — all strategies active")
    except Exception as e:
        logger.warning(f"Scheduler failed to start (non-fatal): {e}")
        system_state.scheduler_running = False

    yield

    # Shutdown
    system_state.scheduler_running = False
    if trading_scheduler:
        await trading_scheduler.stop()
    duckdb.close()
    sqlite.close()
    logger.info("System shutdown complete")


app = FastAPI(
    title="Polymarket Intelligence System",
    description="Quantitative trading engine + 13-panel dashboard backend",
    version="1.0.0",
    redirect_slashes=False,
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── mount route modules ─────────────────────────────────────────────
app.include_router(markets.router, prefix="/api/markets", tags=["Markets"])
app.include_router(signals.router, prefix="/api/signals", tags=["Signals"])
app.include_router(portfolio.router, prefix="/api/portfolio", tags=["Portfolio"])
app.include_router(whale.router, prefix="/api/whale", tags=["Whale Tracker"])
app.include_router(jet.router, prefix="/api/jet", tags=["Jet Tracker"])


# ── WebSocket endpoint ──────────────────────────────────────────────
@app.websocket("/ws/live")
async def websocket_endpoint(ws: WebSocket):
    """Live streaming of signals, trades, and price updates."""
    await ws.accept()
    ws_connections.add(ws)
    logger.info(f"WebSocket client connected ({len(ws_connections)} total)")
    try:
        while True:
            raw = await ws.receive_text()
            try:
                msg = json.loads(raw)
                msg_type = msg.get("type", "")
                if msg_type == "subscribe":
                    logger.debug(f"Client subscribed to {msg.get('market')}")
                elif msg_type == "unsubscribe":
                    logger.debug(f"Client unsubscribed from {msg.get('market')}")
            except json.JSONDecodeError:
                pass
    except WebSocketDisconnect:
        ws_connections.discard(ws)
        logger.info(f"WebSocket client disconnected ({len(ws_connections)} total)")


# ── health check ────────────────────────────────────────────────────
@app.get("/health")
async def health():
    return system_state.get_stats()


# ── kill switch ─────────────────────────────────────────────────────
@app.post("/api/kill")
async def kill_switch():
    """Emergency stop — cancel all orders, halt all strategies."""
    logger.critical("KILL SWITCH ACTIVATED via API")
    if trading_scheduler:
        trading_scheduler.risk.kill()
        if not trading_scheduler.executor.paper_trading:
            await trading_scheduler.executor.cancel_all_live()
    return {"status": "killed", "message": "All trading halted"}


@app.post("/api/unkill")
async def unkill():
    """Resume trading after kill switch."""
    if trading_scheduler:
        trading_scheduler.risk.unkill()
    return {"status": "resumed"}
