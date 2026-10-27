"""How an app process runs.

SubprocessRunner — child process of the controller (local dev, tests).
SystemdRunner    — sandboxed unit belgrade-os-app@<id>.service with its own
                   DynamicUser (production; audit findings H1/H2). The controller
                   may start/stop only those units, via a polkit rule.
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import signal
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Callable, Dict, Optional

logger = logging.getLogger(__name__)

SYSTEMCTL_TIMEOUT_S = 10
_SYSTEMCTL = ["systemctl", "--no-ask-password"]  # polkit miss fails fast, never prompts
_UNIT_NOT_LOADED = 5  # systemctl exit status for "unit not loaded"

_APP_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_ENV_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
# Characters that would end/alter a double-quoted EnvironmentFile value:
# newline/CR start a new assignment, '"' closes the quote, '\' starts an escape.
# '$' and '`' are deliberately allowed: systemd.exec(5) performs no variable
# expansion or command substitution on EnvironmentFile= contents (that applies
# only to Exec* command lines), and '%' specifiers are not expanded there
# either, so DB URLs containing '%', '@' or '$' pass through verbatim.
# Backslash is special inside the quotes (before $ \ " and `), and a trailing
# backslash continues the value onto the next line, swallowing the next
# assignment (verified on systemd 255), so it is rejected outright.
_ENV_VALUE_FORBIDDEN = "\n\r\"\\"


class SubprocessRunner:
    stop_timeout_s: float = 5

    def __init__(self) -> None:
        self._procs: Dict[str, subprocess.Popen] = {}

    async def start(self, app_id: str, app_dir: Path, env: Dict[str, str]) -> None:
        await self.stop(app_id)
        # The child inherits its own fd; our handle is closed even if Popen raises.
        with open(app_dir / "app.log", "a") as log_file:
            log_file.write(f"\n--- App started at {datetime.now()} ---\n")
            log_file.flush()
            self._procs[app_id] = subprocess.Popen(
                [sys.executable, str(app_dir / "main.py")],
                env=env, stdout=log_file, stderr=log_file, preexec_fn=os.setsid,
            )

    async def stop(self, app_id: str) -> None:
        proc = self._procs.pop(app_id, None)
        if proc is None:
            return
        try:
            pgid = os.getpgid(proc.pid)
            os.killpg(pgid, signal.SIGTERM)
            try:
                await asyncio.to_thread(proc.wait, self.stop_timeout_s)
            except subprocess.TimeoutExpired:
                logger.warning("App %s ignored SIGTERM; sending SIGKILL", app_id)
                os.killpg(pgid, signal.SIGKILL)
                await asyncio.to_thread(proc.wait, self.stop_timeout_s)
        except ProcessLookupError:
            pass  # already gone
        except Exception:
            logger.exception("Error stopping app %s (pid %s)", app_id, proc.pid)

    def is_running(self, app_id: str) -> bool:
        proc = self._procs.get(app_id)
        return proc is not None and proc.poll() is None

    def pid(self, app_id: str) -> Optional[int]:
        proc = self._procs.get(app_id)
        return proc.pid if proc is not None and proc.poll() is None else None


class SystemdRunner:
    def __init__(self, env_dir: Path, run: Callable[..., subprocess.CompletedProcess] = subprocess.run) -> None:
        self.env_dir = Path(env_dir)
        self._run = run

    @staticmethod
    def unit(app_id: str) -> str:
        if not _APP_ID_RE.match(app_id):
            raise ValueError(f"invalid app_id {app_id!r}")
        return f"belgrade-os-app@{app_id}.service"

    def _write_env_file(self, app_id: str, env: Dict[str, str]) -> None:
        if not self.env_dir.is_dir():
            raise RuntimeError(
                f"env dir {self.env_dir} does not exist; it is created by the platform_controller "
                "systemd drop-in RuntimeDirectory=belgrade-os/apps "
                "(deploy/platform_controller-runtime-dir.conf)"
            )
        # Validate everything first so a rejected entry never touches disk.
        lines = []
        for key, value in sorted(env.items()):
            if not _ENV_KEY_RE.match(key) or any(c in value for c in _ENV_VALUE_FORBIDDEN):
                raise ValueError(f"env entry not safe for an EnvironmentFile: {key!r}")
            lines.append(f'{key}="{value}"\n')

        # mkstemp creates the file 0600, so the secrets are never readable by
        # others, even when replacing a stale world-readable file; os.replace
        # then swaps it in atomically.
        path = self.env_dir / f"{app_id}.env"
        fd, tmp = tempfile.mkstemp(dir=self.env_dir, prefix=f".{app_id}.env.")
        try:
            with os.fdopen(fd, "w") as f:
                f.writelines(lines)
            os.replace(tmp, path)
        except BaseException:
            try:
                os.unlink(tmp)
            except FileNotFoundError:
                pass
            raise

    async def start(self, app_id: str, app_dir: Path, env: Dict[str, str]) -> None:
        """Write the env file and restart the app's unit.

        ``app_dir`` is ignored: the unit template hard-codes ``@ROOT@/apps/%i``,
        so in systemd mode the supervisor's apps_root must be ``$ROOT/apps``.
        """
        unit = self.unit(app_id)
        self._write_env_file(app_id, env)
        # restart, not start: a running unit must pick up the rewritten env file.
        try:
            await asyncio.to_thread(
                self._run, _SYSTEMCTL + ["restart", unit],
                check=True, capture_output=True, text=True, timeout=SYSTEMCTL_TIMEOUT_S,
            )
        except subprocess.CalledProcessError as e:
            stderr = (e.stderr or "").strip()
            raise RuntimeError(f"systemctl restart {unit} failed: {stderr}") from e
        except subprocess.TimeoutExpired as e:
            raise RuntimeError(f"systemctl restart {unit} timed out after {SYSTEMCTL_TIMEOUT_S}s") from e

    async def stop(self, app_id: str) -> None:
        unit = self.unit(app_id)
        try:
            r = await asyncio.to_thread(
                self._run, _SYSTEMCTL + ["stop", unit],
                capture_output=True, text=True, timeout=SYSTEMCTL_TIMEOUT_S,
            )
        except subprocess.TimeoutExpired as e:
            raise RuntimeError(f"systemctl stop {unit} timed out after {SYSTEMCTL_TIMEOUT_S}s") from e
        if r.returncode not in (0, _UNIT_NOT_LOADED):
            raise RuntimeError(f"systemctl stop {unit} failed: {(r.stderr or '').strip()}")

    def is_running(self, app_id: str) -> bool:
        unit = self.unit(app_id)
        try:
            r = self._run(_SYSTEMCTL + ["is-active", "--quiet", unit],
                          capture_output=True, text=True, timeout=SYSTEMCTL_TIMEOUT_S)
        except subprocess.TimeoutExpired:
            logger.error("systemctl is-active %s timed out", unit)
            return False
        return r.returncode == 0

    def pid(self, app_id: str) -> Optional[int]:
        unit = self.unit(app_id)
        try:
            r = self._run(_SYSTEMCTL + ["show", "-p", "MainPID", "--value", unit],
                          capture_output=True, text=True, timeout=SYSTEMCTL_TIMEOUT_S)
        except subprocess.TimeoutExpired:
            logger.error("systemctl show %s timed out", unit)
            return None
        try:
            pid = int(r.stdout.strip())
        except ValueError:
            return None
        return pid or None
