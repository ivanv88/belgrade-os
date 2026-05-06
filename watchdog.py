#!/usr/bin/env python3
"""Belgrade OS hardware watchdog — monitors CPU temperature, initiates staged shutdown."""
from __future__ import annotations
import glob
import logging
import os
import subprocess
import sys
import time

POLL_INTERVAL = 5        # seconds between thermal reads
WARN_TEMP    = 80_000    # millidegrees C (80°C) — log + notify
STAGE1_TEMP  = 85_000    # millidegrees C (85°C) — kill runner + inference containers
EMERGENCY_TEMP = 90_000  # millidegrees C (90°C) — docker compose down + exit

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    stream=sys.stdout,
)
log = logging.getLogger("watchdog")


def read_temps() -> list[int]:
    """Read all thermal zone temperatures. Returns millidegrees Celsius."""
    temps = []
    for path in glob.glob("/sys/class/thermal/thermal_zone*/temp"):
        try:
            with open(path) as f:
                temps.append(int(f.read().strip()))
        except (OSError, ValueError):
            pass
    return temps


def max_temp(temps: list[int]) -> int:
    return max(temps) if temps else 0


def notify_ntfy(message: str, priority: str = "default") -> None:
    url = os.getenv("WATCHDOG_NTFY_URL", "")
    if not url:
        return
    try:
        subprocess.run(
            ["curl", "-s", "-X", "POST", url, "-H", f"Priority: {priority}", "-d", message],
            timeout=5,
            check=False,
        )
    except Exception:
        pass


def kill_containers(label_value: str) -> None:
    """Kill all containers with label beg-os.role=<label_value>."""
    try:
        result = subprocess.run(
            ["docker", "ps", "-q", "--filter", f"label=beg-os.role={label_value}"],
            capture_output=True, text=True, timeout=10,
        )
        for cid in result.stdout.strip().split():
            if cid:
                subprocess.run(["docker", "kill", cid], timeout=10, check=False)
                log.info("killed container %s (role=%s)", cid, label_value)
    except Exception as e:
        log.error("Failed to kill containers role=%s: %s", label_value, e)


def main() -> None:
    log.info("Belgrade OS watchdog starting (poll=%ds)", POLL_INTERVAL)
    warned = False
    stage1_triggered = False

    while True:
        temps = read_temps()
        peak = max_temp(temps)

        if peak >= EMERGENCY_TEMP:
            msg = f"EMERGENCY: {peak // 1000}°C — shutting down Belgrade OS"
            log.critical(msg)
            notify_ntfy(msg, priority="urgent")
            subprocess.run(["docker", "compose", "down"], timeout=60, check=False)
            sys.exit(1)

        if peak >= STAGE1_TEMP:
            if not stage1_triggered:
                msg = f"WARNING: {peak // 1000}°C — stopping heavy workloads"
                log.warning(msg)
                notify_ntfy(msg, priority="high")
                kill_containers("runner")
                kill_containers("inference")
                stage1_triggered = True
        else:
            stage1_triggered = False

        if peak >= WARN_TEMP and not warned:
            msg = f"Notice: Belgrade OS CPU at {peak // 1000}°C"
            log.warning(msg)
            notify_ntfy(msg)
            warned = True
        elif peak < WARN_TEMP:
            warned = False

        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()
