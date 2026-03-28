#!/bin/bash
# ══════════════════════════════════════════════════════════════
# Polymarket Intelligence System — Deploy Script
#
# Usage:
#   ./deploy.sh              # Deploy everything
#   ./deploy.sh backend      # Backend only
#   ./deploy.sh frontend     # Frontend only
#   ./deploy.sh stop         # Stop everything
# ══════════════════════════════════════════════════════════════

set -e

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
BACKEND_PORT=8000
FRONTEND_PORT=3000

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

log() { echo -e "${GREEN}[DEPLOY]${NC} $1"; }
warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
err() { echo -e "${RED}[ERROR]${NC} $1"; }

# ── Stop everything ────────────────────────────────────────────
stop_all() {
    log "Stopping all services..."
    pkill -f "uvicorn backend.main" 2>/dev/null || true
    pkill -f "next-server" 2>/dev/null || true
    pkill -f "node.*next" 2>/dev/null || true
    sleep 1
    log "All services stopped"
}

# ── Check .env ─────────────────────────────────────────────────
check_env() {
    if [ ! -f "$PROJECT_DIR/.env" ]; then
        warn ".env file not found. Copying from .env.example..."
        cp "$PROJECT_DIR/.env.example" "$PROJECT_DIR/.env"
        warn "Edit .env with your API keys before going live!"
    fi
}

# ── Backend ────────────────────────────────────────────────────
deploy_backend() {
    log "Deploying backend..."
    cd "$PROJECT_DIR"

    # Install Python deps
    if [ ! -d "venv" ]; then
        python3 -m venv venv 2>/dev/null || true
    fi
    if [ -d "venv" ]; then
        source venv/bin/activate
    fi
    pip install -r requirements.txt -q

    # Kill existing backend
    pkill -f "uvicorn backend.main" 2>/dev/null || true
    sleep 1

    # Start backend
    nohup uvicorn backend.main:app \
        --host 0.0.0.0 \
        --port $BACKEND_PORT \
        > data/logs/backend.log 2>&1 &

    log "Backend started on port $BACKEND_PORT (PID: $!)"

    # Wait for health check
    for i in {1..10}; do
        if curl -s http://localhost:$BACKEND_PORT/health > /dev/null 2>&1; then
            log "Backend health check: OK"
            return 0
        fi
        sleep 1
    done
    warn "Backend health check timed out — check data/logs/backend.log"
}

# ── Frontend ───────────────────────────────────────────────────
deploy_frontend() {
    log "Deploying frontend..."
    cd "$PROJECT_DIR/frontend"

    # Install Node deps
    if [ ! -d "node_modules" ]; then
        npm install
    fi

    # Set the API URL to point to the backend
    # On a VPS, this should be the public IP or localhost
    export NEXT_PUBLIC_API_URL="http://localhost:$BACKEND_PORT"
    export NEXT_PUBLIC_WS_URL="ws://localhost:$BACKEND_PORT/ws/live"

    # Build
    log "Building Next.js..."
    npx next build

    # Kill existing frontend
    pkill -f "next-server" 2>/dev/null || true
    pkill -f "node.*next" 2>/dev/null || true
    sleep 1

    # Start production server
    nohup npx next start -p $FRONTEND_PORT \
        > "$PROJECT_DIR/data/logs/frontend.log" 2>&1 &

    log "Frontend started on port $FRONTEND_PORT (PID: $!)"

    # Wait for it
    for i in {1..15}; do
        if curl -s http://localhost:$FRONTEND_PORT > /dev/null 2>&1; then
            log "Frontend health check: OK"
            return 0
        fi
        sleep 1
    done
    warn "Frontend health check timed out — check data/logs/frontend.log"
}

# ── Main ───────────────────────────────────────────────────────
mkdir -p "$PROJECT_DIR/data/logs"

case "${1:-all}" in
    stop)
        stop_all
        ;;
    backend)
        check_env
        deploy_backend
        ;;
    frontend)
        deploy_frontend
        ;;
    all|"")
        check_env
        stop_all
        deploy_backend
        deploy_frontend
        echo ""
        log "═══════════════════════════════════════════"
        log "  POLYMARKET INTELLIGENCE SYSTEM"
        log "  Backend:   http://localhost:$BACKEND_PORT"
        log "  Dashboard: http://localhost:$FRONTEND_PORT"
        log "  Logs:      $PROJECT_DIR/data/logs/"
        log "═══════════════════════════════════════════"
        ;;
    *)
        echo "Usage: $0 [all|backend|frontend|stop]"
        exit 1
        ;;
esac
