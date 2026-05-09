from __future__ import annotations

import asyncio
import json
import logging
import re
import shutil
from pathlib import Path

logger = logging.getLogger(__name__)

_MAX_OUTPUT_BYTES = 1_000_000  # 1 MB
_TIMEOUT_S = 30.0
_ID_RE = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")


class OutputValidationError(Exception):
    pass


class EphemeralRunner:
    def __init__(self, seccomp_profile: str, apps_root: Path) -> None:
        self.seccomp_profile = seccomp_profile
        self.apps_root = Path(apps_root)

    async def run(self, task_id: str, app_id: str, input_data: dict) -> dict:
        if not _ID_RE.match(task_id):
            raise ValueError(f"invalid task_id: {task_id!r}")
        if not _ID_RE.match(app_id):
            raise ValueError(f"invalid app_id: {app_id!r}")
        work_dir = Path(f"/tmp/beg-{task_id}")
        work_dir.mkdir(parents=True, exist_ok=True)
        try:
            (work_dir / "input.json").write_text(json.dumps(input_data))
            await asyncio.wait_for(
                self._run_container(task_id, app_id, work_dir),
                timeout=_TIMEOUT_S,
            )
            return self._read_output(work_dir)
        finally:
            shutil.rmtree(work_dir, ignore_errors=True)

    async def _run_container(self, task_id: str, app_id: str, work_dir: Path) -> None:
        image = f"beg-os-{app_id}:latest"
        container_name = f"beg-{task_id}"
        app_dir = self.apps_root / app_id
        try:
            app_dir.resolve().relative_to(self.apps_root.resolve())
        except ValueError:
            raise ValueError(f"app_id {app_id!r} escapes apps_root")
        proc = await asyncio.create_subprocess_exec(
            "docker", "run",
            "--rm",
            "--name", container_name,
            "--network", "none",
            "--security-opt", f"seccomp={self.seccomp_profile}",
            "--read-only",
            "--user", "1000:1000",
            "--tmpfs", "/tmp:size=32m,noexec,nosuid",
            "-v", f"{work_dir}:/workspace:rw",
            "-v", f"{app_dir}:/app:ro",
            image,
            "python3", "/app/main.py",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await proc.communicate()
        except asyncio.CancelledError:
            # Force-remove the container by name — more reliable than killing
            # the CLI process, which may leave the container running in the daemon.
            try:
                rm = await asyncio.create_subprocess_exec(
                    "docker", "rm", "-f", container_name,
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                )
                await rm.wait()
            except Exception:
                pass
            try:
                proc.kill()
            except ProcessLookupError:
                pass
            await proc.wait()
            raise
        if proc.returncode != 0:
            raise RuntimeError(
                f"Container exited {proc.returncode}: {stderr.decode(errors='replace')[:500]}"
            )
        logger.debug("container stdout task_id=%s: %s", task_id, stdout.decode(errors="replace")[:200])

    def _read_output(self, work_dir: Path) -> dict:
        output_file = work_dir / "output.json"
        if not output_file.exists():
            raise OutputValidationError("output.json not created by container")
        content = output_file.read_bytes()
        if len(content) > _MAX_OUTPUT_BYTES:
            raise OutputValidationError(
                f"output.json too large: {len(content)} bytes (max {_MAX_OUTPUT_BYTES})"
            )
        try:
            data = json.loads(content)
        except json.JSONDecodeError as exc:
            raise OutputValidationError(f"output.json not valid JSON: {exc}") from exc
        if not isinstance(data, dict):
            raise OutputValidationError(
                f"output.json must be a JSON object, got {type(data).__name__}"
            )
        return data
