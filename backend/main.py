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

from backend.api import markets, signals, portfolio, whale, jet, entropy
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


vpn_guard = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown hooks."""
    global trading_scheduler, vpn_guard
    logger.info("=" * 60)
    logger.info("  POLYMARKET INTELLIGENCE SYSTEM")
    logger.info(f"  Paper trading: {settings.trading.paper_trading}")
    logger.info("=" * 60)

    # Configure proxy for all HTTP clients
    from backend.data_layer.proxy import configure_proxy
    configure_proxy(
        proxy_url=settings.vpn.proxy_url,
        vpn_required=settings.vpn.required,
    )

    # VPN startup gate — blocks trading if VPN is required but not working
    from backend.observability.vpn_guard import VPNGuard
    vpn_guard = VPNGuard(
        proxy_url=settings.vpn.proxy_url,
        check_url=settings.vpn.check_url,
        required=settings.vpn.required,
        check_interval=settings.vpn.check_interval,
    )

    if settings.vpn.required:
        vpn_ok = await vpn_guard.startup_gate()
        if not vpn_ok:
            logger.critical("VPN STARTUP GATE FAILED — trading disabled")
            system_state.scheduler_running = False
            # Still start the app (dashboard works) but don't start trading
            duckdb.connect()
            sqlite.connect()
            yield
            duckdb.close()
            sqlite.close()
            return

    duckdb.connect()
    sqlite.connect()

    # Start the trading scheduler
    try:
        from backend.scheduler import TradingScheduler

        trading_scheduler = TradingScheduler()
        trading_scheduler.start()
        system_state.scheduler_running = True
        logger.info("Trading scheduler started — all strategies active")

        # Start VPN runtime monitor (halts trading on VPN drop)
        async def on_vpn_drop():
            logger.critical("VPN DROP — halting all trading")
            if trading_scheduler:
                trading_scheduler.risk.kill()
                if not trading_scheduler.executor.paper_trading:
                    await trading_scheduler.executor.cancel_all_live()
            await system_state.broadcast("vpn_drop", {"status": "halted"})
            from backend.observability.alerts import TelegramAlert
            alert = TelegramAlert(
                bot_token=settings.alerts.telegram_bot_token,
                chat_id=settings.alerts.telegram_chat_id,
            )
            await alert.risk_alert("VPN DROP DETECTED — all trading halted. Check proxy.")
            await alert.close()

        vpn_guard.start_monitor(on_drop_callback=on_vpn_drop)

    except Exception as e:
        logger.warning(f"Scheduler failed to start (non-fatal): {e}")
        system_state.scheduler_running = False

    yield

    # Shutdown
    system_state.scheduler_running = False
    if vpn_guard:
        await vpn_guard.stop_monitor()
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
app.include_router(entropy.router, prefix="/api/entropy", tags=["Entropy"])
app.include_router(signals.router, prefix="/api/signals", tags=["Signals"])
app.include_router(portfolio.router, prefix="/api/portfolio", tags=["Portfolio"])
app.include_router(whale.router, prefix="/api/whale", tags=["Whale Tracker"])
# Alias: /api/whale-tracker routes to the same whale router
app.include_router(whale.router, prefix="/api/whale-tracker", tags=["Whale Tracker Alias"])
app.include_router(jet.router, prefix="/api/jet", tags=["Jet Tracker"])

# Prediction Intelligence Layer
try:
    from prediction_intelligence.api import router as intelligence_router
    app.include_router(intelligence_router, prefix="/api/v1/intelligence", tags=["Prediction Intelligence"])
    logger.info("Prediction Intelligence API mounted")
except ImportError as e:
    logger.warning(f"Prediction Intelligence not loaded: {e}")


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
@app.get("/api/health")
async def health():
    stats = system_state.get_stats()
    stats["status"] = "ok"
    if vpn_guard and vpn_guard.last_status:
        s = vpn_guard.last_status
        stats["vpn"] = {
            "healthy": s.healthy,
            "ip": s.ip,
            "country": s.country,
            "error": s.error,
        }
    return stats


@app.get("/api/vpn/status")
async def vpn_status():
    """Check VPN status — run a live check through the proxy."""
    if not vpn_guard:
        return {"enabled": False, "message": "VPN not configured"}
    status = await vpn_guard.check()
    return {
        "enabled": settings.vpn.required,
        "healthy": status.healthy,
        "ip": status.ip,
        "country": status.country,
        "org": status.org,
        "error": status.error,
    }


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
