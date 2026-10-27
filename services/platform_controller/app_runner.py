"""How an app process runs.

SubprocessRunner — child process of the controller (local dev, tests).
SystemdRunner    — sandboxed unit belgrade-os-app@<id>.service with its own
                   DynamicUser (production; audit findings H1/H2). The controller
                   may start/stop only those units, via a polkit rule.
"""
from __future__ import annotations

import asyncio
import os
import re
import signal
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Callable, Dict, Optional

_APP_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_ENV_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
# Characters that would end/alter a double-quoted EnvironmentFile value:
# newline/CR start a new assignment, '"' closes the quote, '\' starts an escape.
# '$' and '`' are deliberately allowed: systemd.exec(5) performs no variable
# expansion or command substitution on EnvironmentFile= contents (that applies
# only to Exec* command lines), and '%' specifiers are not expanded there
# either, so DB URLs containing '%', '@' or '$' pass through verbatim.
_ENV_VALUE_FORBIDDEN = "\n\r\"\\"


class SubprocessRunner:
    def __init__(self) -> None:
        self._procs: Dict[str, subprocess.Popen] = {}

    async def start(self, app_id: str, app_dir: Path, env: Dict[str, str]) -> None:
        await self.stop(app_id)
        log_file = open(app_dir / "app.log", "a")
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
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
            proc.wait(timeout=5)
        except Exception:
            pass

    def is_running(self, app_id: str) -> bool:
        proc = self._procs.get(app_id)
        return proc is not None and proc.poll() is None

    def pid(self, app_id: str) -> Optional[int]:
        proc = self._procs.get(app_id)
        return proc.pid if proc is not None else None


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
        unit = self.unit(app_id)
        self._write_env_file(app_id, env)
        # restart, not start: a running unit must pick up the rewritten env file.
        await asyncio.to_thread(self._run, ["systemctl", "restart", unit], check=True, capture_output=True, text=True)

    async def stop(self, app_id: str) -> None:
        await asyncio.to_thread(self._run, ["systemctl", "stop", self.unit(app_id)], capture_output=True, text=True)

    def is_running(self, app_id: str) -> bool:
        r = self._run(["systemctl", "is-active", "--quiet", self.unit(app_id)], capture_output=True, text=True)
        return r.returncode == 0

    def pid(self, app_id: str) -> Optional[int]:
        r = self._run(["systemctl", "show", "-p", "MainPID", "--value", self.unit(app_id)],
                      capture_output=True, text=True)
        try:
            pid = int(r.stdout.strip())
        except ValueError:
            return None
        return pid or None
