"""
FastAPI backend — WebSocket streaming + REST endpoints.

Run: uvicorn backend.main:app --host 0.0.0.0 --port 8000 --reload
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from backend.api import markets, signals, portfolio, whale, jet
from backend.config import settings
from backend.data_layer.gamma_client import GammaClient
from backend.data_layer.storage import DuckDBStorage, SQLiteState
from backend.observability.logger import setup_logging

setup_logging()
logger = logging.getLogger(__name__)


# ── shared state ────────────────────────────────────────────────────
gamma_client = GammaClient()
duckdb = DuckDBStorage()
sqlite = SQLiteState()

# WebSocket connections for live streaming
ws_connections: set[WebSocket] = set()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown hooks."""
    logger.info("Starting Polymarket Intelligence System")
    logger.info(f"Paper trading: {settings.trading.paper_trading}")
    duckdb.connect()
    sqlite.connect()
    yield
    await gamma_client.close()
    duckdb.close()
    sqlite.close()
    logger.info("System shutdown complete")


app = FastAPI(
    title="Polymarket Intelligence System",
    description="Quantitative trading engine + dashboard backend",
    version="1.0.0",
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
            # Keep connection alive; clients receive broadcast messages
            data = await ws.receive_text()
            # Handle client messages (subscribe to specific markets, etc.)
    except WebSocketDisconnect:
        ws_connections.discard(ws)
        logger.info(f"WebSocket client disconnected ({len(ws_connections)} total)")


async def broadcast(event_type: str, data: dict) -> None:
    """Broadcast an event to all connected WebSocket clients."""
    message = {"type": event_type, "data": data}
    disconnected = set()
    for ws in ws_connections:
        try:
            await ws.send_json(message)
        except Exception:
            disconnected.add(ws)
    ws_connections -= disconnected


# ── health check ────────────────────────────────────────────────────
@app.get("/health")
async def health():
    return {
        "status": "ok",
        "paper_trading": settings.trading.paper_trading,
        "strategies_enabled": {
            "entropy": settings.strategies.entropy,
            "avellaneda": settings.strategies.avellaneda,
            "arb": settings.strategies.arb,
            "ensemble": settings.strategies.ensemble,
            "jet": settings.strategies.jet,
            "copy": settings.strategies.copy,
            "theta": settings.strategies.theta,
        },
    }
