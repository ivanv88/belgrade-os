#!/usr/bin/env bash
# Installs Belgrade OS as systemd services. Run once with sudo on the host:
#   sudo scripts/install-systemd.sh [service ...]
# With no arguments, enables the services the MCP path needs.
set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
    echo "run with sudo" >&2
    exit 1
fi

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
RUN_USER="${SUDO_USER:-$(stat -c %U "$ROOT")}"
if [ $# -gt 0 ]; then
    SERVICES=("$@")
else
    SERVICES=(bridge mcp_server platform_controller)
fi
UNIT_DIR=/etc/systemd/system

# Redis (uid/gid 999 in the container) must read the ACL file without it being world-readable.
if [ -f "$ROOT/config/redis.acl" ]; then
    chgrp 999 "$ROOT/config/redis.acl"
    chmod 640 "$ROOT/config/redis.acl"
fi

sed -e "s|@ROOT@|$ROOT|g" -e "s|@USER@|$RUN_USER|g" \
    "$ROOT/deploy/belgrade-os@.service" > "$UNIT_DIR/belgrade-os@.service"
install -m 644 "$ROOT/deploy/belgrade-os.target" "$UNIT_DIR/belgrade-os.target"
install -d "$UNIT_DIR/belgrade-os@platform_controller.service.d"
install -m 644 "$ROOT/deploy/platform_controller-wait-bridge.conf" \
    "$UNIT_DIR/belgrade-os@platform_controller.service.d/wait-bridge.conf"

systemctl daemon-reload
systemctl enable belgrade-os.target
for svc in "${SERVICES[@]}"; do
    systemctl enable "belgrade-os@$svc.service"
done

echo "Installed for user $RUN_USER at $ROOT: ${SERVICES[*]}"
echo "Start with: sudo systemctl start belgrade-os.target"
echo "Logs:       journalctl -u 'belgrade-os@*' -f"
