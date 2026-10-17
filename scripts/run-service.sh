#!/usr/bin/env bash
# Runs one Belgrade OS service in the foreground with .env loaded.
# Single source of truth for how each service starts — used by systemd
# (deploy/belgrade-os@.service) and by scripts/start.sh.
# Usage: scripts/run-service.sh <service>

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if [ ! -f .env ]; then
    echo ".env not found in $ROOT — run scripts/setup.sh first" >&2
    exit 1
fi
# Sourced (not systemd EnvironmentFile) so ${VAR} references inside .env expand.
set -a; source .env; set +a
export PYTHONPATH=.

case "${1:-}" in
    bridge)
        exec ./services/bridge/target/release/bridge ;;
    gateway)
        exec ./services/gateway/gateway ;;
    vault_service|notification|inference|runner|platform_controller|mcp_server)
        exec ./venv/bin/python3 "services/$1/main.py" ;;
    *)
        echo "usage: $0 {bridge|gateway|vault_service|notification|inference|runner|platform_controller|mcp_server}" >&2
        exit 2 ;;
esac
