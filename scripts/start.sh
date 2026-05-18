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
echo "[1/7] Bridge"
start_service bridge ./services/bridge/target/release/bridge
wait_tcp bridge localhost 8081

# 2. Vault service
echo "[2/7] Vault service"
start_service vault_service ./venv/bin/python3 services/vault_service/main.py

# 3. Notification service
echo "[3/7] Notification service"
start_service notification ./venv/bin/python3 services/notification/main.py

# 4. Inference worker
echo "[4/7] Inference worker"
start_service inference ./venv/bin/python3 services/inference/main.py

# 5. Runner
echo "[5/7] Runner"
start_service runner ./venv/bin/python3 services/runner/main.py

# 6. Gateway (Go binary)
echo "[6/7] Gateway"
start_service gateway ./services/gateway/gateway
wait_tcp gateway localhost "${PORT:-8080}"

# 7. Platform controller — last, because it starts app subprocesses
echo "[7/7] Platform controller"
start_service platform_controller ./venv/bin/python3 services/platform_controller/main.py
wait_tcp platform_controller localhost 8000

echo ""
echo "✅ All services started. Logs in logs/  PIDs in run/"
echo "   Stop with: make stop"
