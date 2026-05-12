#!/usr/bin/env bash
# Stops all Belgrade OS application services tracked in run/*.pid

set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

if [ ! -d run ] || [ -z "$(ls run/*.pid 2>/dev/null)" ]; then
    echo "No running services found in run/"
    exit 0
fi

echo "🇷🇸 Belgrade OS — stopping services"

# Stop in reverse start order (platform_controller first so apps get SIGTERM before bridge dies)
for name in platform_controller gateway runner inference notification vault_service bridge; do
    pf="run/$name.pid"
    if [ -f "$pf" ]; then
        pid=$(cat "$pf")
        if kill -0 "$pid" 2>/dev/null; then
            echo "  Stopping $name (PID $pid)..."
            kill "$pid"
        else
            echo "  $name (PID $pid) already dead"
        fi
        rm -f "$pf"
    fi
done

echo "✅ Done"
