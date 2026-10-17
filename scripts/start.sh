#!/usr/bin/env bash
# Starts all Belgrade OS application services in dependency order.
# Requires: make dev already running (redis, db, docker-socket-proxy, tunnel).
# Usage: ./start.sh [--no-wait]

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
export PYTHONPATH=.

# Load .env
if [ ! -f .env ]; then
    echo "❌ .env not found — run ./setup.sh first"
    exit 1
fi
set -a; source .env; set +a

mkdir -p run logs

# --- helpers ----------------------------------------------------------------

pid_file() { echo "run/$1.pid"; }

is_running() {
    local pf; pf="$(pid_file "$1")"
    [ -f "$pf" ] && kill -0 "$(cat "$pf")" 2>/dev/null
}

start_service() {
    local name=$1; shift
    if is_running "$name"; then
        echo "⚠️  $name already running (PID $(cat "$(pid_file "$name")"))"
        return
    fi
    echo -n "  Starting $name ... "
    "$@" >> "logs/$name.log" 2>&1 &
    echo $! > "$(pid_file "$name")"
    echo "PID $!"
}

wait_tcp() {
    local name=$1 host=$2 port=$3 retries=${4:-20}
    echo -n "  Waiting for $name on $host:$port "
    for i in $(seq 1 "$retries"); do
        if nc -z "$host" "$port" 2>/dev/null; then
            echo " ready"
            return
        fi
        echo -n "."
        sleep 0.5
    done
    echo " TIMEOUT — $name may not have started correctly"
}

# ---------------------------------------------------------------------------

echo "🇷🇸 Belgrade OS — starting services"
echo ""

# 1. Bridge (Rust binary)
echo "[1/8] Bridge"
start_service bridge ./scripts/run-service.sh bridge
wait_tcp bridge localhost "${BRIDGE_PORT:-8081}"

# 2. Vault service
echo "[2/8] Vault service"
start_service vault_service ./scripts/run-service.sh vault_service

# 3. Notification service
echo "[3/8] Notification service"
start_service notification ./scripts/run-service.sh notification

# 4. Inference worker
echo "[4/8] Inference worker"
start_service inference ./scripts/run-service.sh inference

# 5. Runner
echo "[5/8] Runner"
start_service runner ./scripts/run-service.sh runner

# 6. Gateway (Go binary)
echo "[6/8] Gateway"
start_service gateway ./scripts/run-service.sh gateway
wait_tcp gateway localhost "${GATEWAY_PORT:-${PORT:-8080}}"

# 7. MCP server
echo "[7/8] MCP server"
start_service mcp_server ./scripts/run-service.sh mcp_server
wait_tcp mcp_server localhost "${MCP_PORT:-8083}"

# 8. Platform controller — last, because it starts app subprocesses
echo "[8/8] Platform controller"
start_service platform_controller ./scripts/run-service.sh platform_controller
wait_tcp platform_controller localhost "${CONTROLLER_PORT:-8000}"

echo ""
echo "✅ All services started. Logs in logs/  PIDs in run/"
echo "   Stop with: make stop"
