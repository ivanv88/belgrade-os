import asyncio
import json
import os
import signal
import subprocess
import logging
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, ValidationError
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy import text, Column, String, JSON, DateTime
from sqlalchemy.orm import declarative_base

from scheduler import SchedulerManager, ScheduleEntry, PermissionSyncManager
from ephemeral_runner import EphemeralRunner, OutputValidationError

import re
import secrets
from fastapi import Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# --- Database Setup ---
DB_URL = os.getenv("DATABASE_URL", "postgresql+asyncpg://postgres:postgres@localhost:5432/postgres")
REDIS_URL = os.getenv("CONTROLLER_REDIS_URL") or os.getenv("BEG_OS_REDIS_URL", "redis://localhost:6379")
CONTROLLER_TOKEN = os.getenv("CONTROLLER_API_TOKEN", "")
SECCOMP_PROFILE = os.getenv("SECCOMP_PROFILE", "/config/seccomp-untrusted.json")
APPS_ROOT = Path(os.getenv("APPS_ROOT", str(Path(__file__).parent.parent / "apps")))

_ephemeral_runner = EphemeralRunner(seccomp_profile=SECCOMP_PROFILE, apps_root=APPS_ROOT)
_bearer = HTTPBearer(auto_error=False)
_APP_ID_RE = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")


def _require_token(
    creds: Optional[HTTPAuthorizationCredentials] = Depends(_bearer),
) -> None:
    token = os.environ.get("CONTROLLER_API_TOKEN", CONTROLLER_TOKEN)
    if not token:
        raise HTTPException(status_code=500, detail="CONTROLLER_API_TOKEN not set")
    if creds is None or not secrets.compare_digest(creds.credentials, token):
        raise HTTPException(status_code=403, detail="forbidden")


engine = create_async_engine(DB_URL)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False)

# --- Manifest Models (inline, no SDK dependency) ---
class _NotificationsManifest(BaseModel):
    driver: Optional[str] = None

class _UIBundleManifest(BaseModel):
    type: str = "spa"
    path: str = "static/"
    entry: str = "index.html"
    required_role: Optional[str] = None

class _AppUIManifest(BaseModel):
    enabled: bool = False
    bundles: Dict[str, _UIBundleManifest] = {}

class _AppManifest(BaseModel):
    app_id: str
    name: Optional[str] = None
    ui: Optional[_AppUIManifest] = None
    related_apps: List[str] = []
    notifications: Optional[_NotificationsManifest] = None


# --- App Supervision ---
class AppProcess:
    def __init__(self, app_id: str, path: Path, port: int):
        self.app_id = app_id
        self.path = path
        self.port = port
        self.process: Optional[subprocess.Popen] = None

    def _load_manifest(self) -> Optional["_AppManifest"]:
        """Load and validate manifest.json from the app directory.

        Returns None when manifest.json is absent.
        Raises ValueError for invalid JSON or schema violations.
        """
        manifest_path = self.path / "manifest.json"
        if not manifest_path.exists():
            return None
        try:
            data = json.loads(manifest_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"manifest.json for app '{self.app_id}' is not valid JSON: {exc}"
            ) from exc
        try:
            return _AppManifest.model_validate(data)
        except ValidationError as exc:
            raise ValueError(
                f"manifest.json for app '{self.app_id}' failed schema validation:\n{exc}"
            ) from exc

    async def start(self):
        manifest = self._load_manifest()
        # Per-app driver from manifest overrides the global env var.
        notification_driver = (
            (manifest.notifications.driver if manifest and manifest.notifications else None)
            or os.getenv("BEG_OS_NOTIFICATION_DRIVER", "ntfy")
        )

        env = os.environ.copy()
        env["BEG_OS_APP_ID"] = self.app_id
        env["BEG_OS_CALLBACK_URL"] = f"http://localhost:{self.port}"
        env["BEG_OS_BRIDGE_URL"] = os.getenv("BEG_OS_BRIDGE_URL", "http://localhost:8081")
        env["BEG_OS_DB_URL"] = DB_URL
        env["BEG_OS_REDIS_URL"] = os.getenv("APP_REDIS_URL") or os.getenv("BEG_OS_REDIS_URL", "redis://localhost:6379")
        env["BEG_OS_NOTIFICATION_DRIVER"] = notification_driver

        cmd = ["python3", str(self.path / "main.py")]

        log_file_path = self.path / "app.log"
        logger.info(
            "Starting app %s on port %s (driver=%s, logs=%s)",
            self.app_id, self.port, notification_driver, log_file_path,
        )

        log_file = open(log_file_path, "a")
        log_file.write(f"\n--- App started at {datetime.now()} ---\n")
        log_file.flush()

        self.process = subprocess.Popen(
            cmd,
            env=env,
            stdout=log_file,
            stderr=log_file,
            preexec_fn=os.setsid,
        )

    async def stop(self):
        if self.process:
            logger.info(f"Stopping app {self.app_id} (PID: {self.process.pid})...")
            try:
                os.killpg(os.getpgid(self.process.pid), signal.SIGTERM)
                self.process.wait(timeout=5)
            except Exception as e:
                logger.error(f"Error stopping app {self.app_id}: {e}")
            self.process = None

class AppSupervisor:
    def __init__(self, apps_root: Path):
        self.apps_root = apps_root
        self.running_apps: Dict[str, AppProcess] = {}
        self.next_port = 9001

    async def discover_and_start(self):
        if not self.apps_root.exists():
            logger.warning(f"Apps root {self.apps_root} does not exist.")
            return
        for app_dir in self.apps_root.iterdir():
            if app_dir.is_dir() and (app_dir / "main.py").exists():
                await self.start_app(app_dir.name)

    async def start_app(self, app_id: str):
        if app_id in self.running_apps:
            await self.stop_app(app_id)

        app_path = self.apps_root / app_id
        app_process = AppProcess(app_id, app_path, self.next_port)
        try:
            await app_process.start()
        except ValueError as exc:
            logger.error("Skipping app %s — invalid manifest: %s", app_id, exc)
            return

        self.running_apps[app_id] = app_process
        self.next_port += 1

    async def stop_app(self, app_id: str):
        if app_id in self.running_apps:
            await self.running_apps[app_id].stop()
            del self.running_apps[app_id]

    async def _watch_tick(self) -> None:
        """Check all running apps; restart any that have exited."""
        dead = [
            app_id
            for app_id, proc in self.running_apps.items()
            if proc.process is not None and proc.process.poll() is not None
        ]
        for app_id in dead:
            logger.warning("app %s exited (rc=%s) — restarting", app_id,
                           self.running_apps[app_id].process.returncode)
            await self.start_app(app_id)

    async def watch(self, interval: int = 10) -> None:
        """Background loop: call _watch_tick every `interval` seconds."""
        while True:
            await asyncio.sleep(interval)
            await self._watch_tick()

async def process_untrusted_call(
    call_bytes: bytes,
    runner: EphemeralRunner,
    rdb,
) -> None:
    from gen import belgrade_os_pb2
    call = belgrade_os_pb2.ToolCall()
    call.ParseFromString(call_bytes)

    start_ms = int(time.time() * 1000)
    result = belgrade_os_pb2.ToolResult()
    result.call_id = call.call_id
    result.task_id = call.task_id
    result.user_id = call.user_id
    result.tenant_id = call.tenant_id

    raw_app_id = call.tool_name.split(":")[0] if ":" in call.tool_name else call.tool_name
    if not _APP_ID_RE.match(raw_app_id):
        result.success = False
        result.error = f"invalid app_id derived from tool_name: {raw_app_id!r}"
        result.duration_ms = 0
        await rdb.xadd(
            "tasks:tool_results",
            {b"data": result.SerializeToString(), b"task_id": call.task_id.encode()},
        )
        return

    try:
        output = await runner.run(
            task_id=call.task_id,
            app_id=raw_app_id,
            input_data={"tool_name": call.tool_name, "input_json": call.input_json},
        )
        result.success = True
        result.output_json = json.dumps(output)
    except asyncio.TimeoutError:
        result.success = False
        result.error = "execution timeout after 30s"
        logger.error("ephemeral timeout task_id=%s call_id=%s tool=%s",
                     call.task_id, call.call_id, call.tool_name)
    except (OutputValidationError, RuntimeError, ValueError) as exc:
        result.success = False
        result.error = str(exc)
        logger.error("ephemeral error task_id=%s call_id=%s: %s",
                     call.task_id, call.call_id, exc)

    result.duration_ms = int(time.time() * 1000) - start_ms
    await rdb.xadd(
        "tasks:tool_results",
        {b"data": result.SerializeToString(), b"task_id": call.task_id.encode()},
    )
    logger.info(
        "untrusted call done task_id=%s call_id=%s tool=%s success=%s duration_ms=%d",
        call.task_id, call.call_id, call.tool_name, result.success, result.duration_ms,
    )


async def _process_schedule_op(data: bytes) -> None:
    from gen import belgrade_os_pb2

    op = belgrade_os_pb2.ScheduleOp()
    op.ParseFromString(data)

    if not _APP_ID_RE.match(op.app_id):
        logger.error("invalid app_id in ScheduleOp: %r — discarding", op.app_id)
        return

    if op.op == belgrade_os_pb2.ScheduleOp.UPSERT:
        if not op.schedule_id or not op.cron or not op.tool_name:
            logger.error("malformed UPSERT ScheduleOp — missing schedule_id/cron/tool_name, discarding id=%r", op.schedule_id)
            return
        entry = ScheduleEntry(
            id=op.schedule_id,
            app_id=op.app_id,
            user_id=op.user_id,
            tenant_id=op.tenant_id,
            cron=op.cron,
            tool_name=op.tool_name,
            params=json.loads(op.params_json) if op.params_json else {},
        )
        async with SessionLocal() as session:
            await session.execute(text("""
                INSERT INTO shared.schedules (id, app_id, user_id, tenant_id, cron, tool_name, params, updated_at)
                VALUES (:id, :app_id, :user_id, :tenant_id, :cron, :tool_name, :params, NOW())
                ON CONFLICT (id) DO UPDATE SET
                    app_id     = EXCLUDED.app_id,
                    user_id    = EXCLUDED.user_id,
                    tenant_id  = EXCLUDED.tenant_id,
                    cron       = EXCLUDED.cron,
                    tool_name  = EXCLUDED.tool_name,
                    params     = EXCLUDED.params,
                    updated_at = NOW()
            """), entry.model_dump())
            await session.commit()
        await scheduler_manager.add_schedule(entry)
        logger.info("schedule upserted id=%s tool=%s cron=%s", op.schedule_id, op.tool_name, op.cron)

    elif op.op == belgrade_os_pb2.ScheduleOp.DELETE:
        if not op.schedule_id:
            logger.error("malformed DELETE ScheduleOp — empty schedule_id, discarding")
            return
        async with SessionLocal() as session:
            await session.execute(
                text("DELETE FROM shared.schedules WHERE id = :id"),
                {"id": op.schedule_id},
            )
            await session.commit()
        scheduler_manager.remove_schedule(op.schedule_id)
        logger.info("schedule deleted id=%s", op.schedule_id)


async def _schedule_ops_consumer_loop(redis_url: str) -> None:
    import redis.asyncio as aioredis
    import redis.exceptions

    STREAM = "tasks:schedule_ops"
    GROUP = "schedule-ops-runners"
    CONSUMER = "platform-controller"

    rdb = aioredis.from_url(redis_url, decode_responses=False)
    try:
        await rdb.xgroup_create(STREAM, GROUP, id="0", mkstream=True)
    except Exception:
        pass  # BUSYGROUP on restart

    logger.info("schedule ops consumer started stream=%s group=%s", STREAM, GROUP)
    while True:
        try:
            results = await rdb.xreadgroup(
                groupname=GROUP,
                consumername=CONSUMER,
                streams={STREAM: ">"},
                count=1,
                block=2000,
            )
            if not results:
                continue
            _stream, messages = results[0]
            for msg_id, fields in messages:
                data = fields.get(b"data")
                if data is None:
                    await rdb.xack(STREAM, GROUP, msg_id)
                    continue
                try:
                    await _process_schedule_op(data)
                    await rdb.xack(STREAM, GROUP, msg_id)
                except Exception:
                    logger.exception(
                        "unhandled error msg=%s — not ACKed, will retry on restart", msg_id
                    )
        except redis.exceptions.ConnectionError:
            logger.error("schedule ops consumer lost Redis connection, retrying in 5s")
            await asyncio.sleep(5)
        except Exception:
            logger.exception("schedule ops consumer unexpected error")
            await asyncio.sleep(1)


async def _untrusted_consumer_loop(redis_url: str) -> None:
    import redis.asyncio as aioredis
    import redis.exceptions

    STREAM = "tasks:untrusted_calls"
    GROUP = "untrusted-runners"
    CONSUMER = "platform-controller"

    rdb = aioredis.from_url(redis_url, decode_responses=False)
    try:
        await rdb.xgroup_create(STREAM, GROUP, id="0", mkstream=True)
    except Exception:
        pass  # BUSYGROUP on restart

    logger.info("untrusted consumer started stream=%s group=%s", STREAM, GROUP)
    while True:
        try:
            results = await rdb.xreadgroup(
                groupname=GROUP,
                consumername=CONSUMER,
                streams={STREAM: ">"},
                count=1,
                block=2000,
            )
            if not results:
                continue
            _stream, messages = results[0]
            for msg_id, fields in messages:
                call_bytes = fields.get(b"data")
                if call_bytes is None:
                    await rdb.xack(STREAM, GROUP, msg_id)
                    continue
                try:
                    await process_untrusted_call(call_bytes, _ephemeral_runner, rdb)
                    await rdb.xack(STREAM, GROUP, msg_id)
                except Exception:
                    logger.exception(
                        "unhandled error msg=%s — not ACKed, will retry on restart", msg_id
                    )
        except Exception as exc:
            if "ConnectionError" in type(exc).__name__:
                logger.error("untrusted consumer lost Redis connection, retrying in 5s")
                await asyncio.sleep(5)
            else:
                logger.exception("untrusted consumer unexpected error")
                await asyncio.sleep(1)


# --- FastAPI App ---
app = FastAPI(title="Belgrade Platform Controller")
bridge_url = os.getenv("BEG_OS_BRIDGE_URL", "http://localhost:8081")
app_supervisor = AppSupervisor(apps_root=Path(__file__).parent.parent / "apps")
scheduler_manager = SchedulerManager(bridge_url=bridge_url)
permission_sync = PermissionSyncManager(db_engine=engine, redis_url=REDIS_URL)

class AppAction(BaseModel):
    app_id: str

@app.on_event("startup")
async def startup_event():
    # 1. Initialize DB tables
    async with engine.begin() as conn:
        await conn.execute(text("CREATE SCHEMA IF NOT EXISTS shared"))
        await conn.execute(text("""
            CREATE TABLE IF NOT EXISTS shared.schedules (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                tenant_id TEXT NOT NULL,
                cron TEXT NOT NULL,
                tool_name TEXT NOT NULL,
                params JSONB DEFAULT '{}',
                updated_at TIMESTAMPTZ DEFAULT NOW()
            )
        """))
        await conn.execute(text(
            "ALTER TABLE shared.schedules ADD COLUMN IF NOT EXISTS app_id TEXT NOT NULL DEFAULT ''"
        ))
        await conn.execute(text("""
            CREATE TABLE IF NOT EXISTS shared.app_permissions (
                user_id TEXT NOT NULL,
                app_id TEXT NOT NULL,
                bundle_id TEXT NOT NULL DEFAULT 'default',
                role TEXT NOT NULL,
                PRIMARY KEY (user_id, app_id, bundle_id)
            )
        """))

    # 2. Start Apps
    await app_supervisor.discover_and_start()

    # 3. Start Scheduler & Load existing jobs
    scheduler_manager.start()
    permission_sync.start()
    await permission_sync.sync_all()

    async with SessionLocal() as session:
        result = await session.execute(text("SELECT id, app_id, user_id, tenant_id, cron, tool_name, params FROM shared.schedules"))
        for row in result.all():
            entry = ScheduleEntry(
                id=row[0],
                app_id=row[1],
                user_id=row[2],
                tenant_id=row[3],
                cron=row[4],
                tool_name=row[5],
                params=row[6]
            )
            await scheduler_manager.add_schedule(entry)

    # 4. Start untrusted calls consumer
    asyncio.create_task(_untrusted_consumer_loop(REDIS_URL))
    asyncio.create_task(_schedule_ops_consumer_loop(REDIS_URL))
    asyncio.create_task(app_supervisor.watch())

@app.post("/apps/reload")
async def reload_app(action: AppAction, _: None = Depends(_require_token)):
    if not _APP_ID_RE.match(action.app_id):
        raise HTTPException(status_code=400, detail="invalid app_id: must match ^[a-zA-Z0-9_-]{1,64}$")
    await app_supervisor.start_app(action.app_id)
    return {"status": "reloaded", "app_id": action.app_id}

@app.get("/apps")
async def list_apps():
    return {
        app_id: {"port": proc.port, "pid": proc.process.pid if proc.process else None}
        for app_id, proc in app_supervisor.running_apps.items()
    }

@app.post("/schedules")
async def create_schedule(entry: ScheduleEntry):
    async with SessionLocal() as session:
        await session.execute(text("""
            INSERT INTO shared.schedules (id, app_id, user_id, tenant_id, cron, tool_name, params, updated_at)
            VALUES (:id, :app_id, :user_id, :tenant_id, :cron, :tool_name, :params, NOW())
            ON CONFLICT (id) DO UPDATE SET
                app_id = EXCLUDED.app_id,
                user_id = EXCLUDED.user_id,
                tenant_id = EXCLUDED.tenant_id,
                cron = EXCLUDED.cron,
                tool_name = EXCLUDED.tool_name,
                params = EXCLUDED.params,
                updated_at = NOW()
        """), entry.model_dump())
        await session.commit()
    
    await scheduler_manager.add_schedule(entry)
    return {"status": "scheduled", "id": entry.id}

@app.delete("/schedules/{schedule_id}")
async def delete_schedule(schedule_id: str):
    async with SessionLocal() as session:
        await session.execute(text("DELETE FROM shared.schedules WHERE id = :id"), {"id": schedule_id})
        await session.commit()
    
    scheduler_manager.remove_schedule(schedule_id)
    return {"status": "deleted", "id": schedule_id}

@app.get("/schedules")
async def list_schedules(app_id: Optional[str] = None, user_id: Optional[str] = None):
    async with SessionLocal() as session:
        if app_id and user_id:
            result = await session.execute(
                text("SELECT id, app_id, user_id, tenant_id, cron, tool_name, params FROM shared.schedules WHERE app_id = :app_id AND user_id = :user_id"),
                {"app_id": app_id, "user_id": user_id},
            )
        elif app_id:
            result = await session.execute(
                text("SELECT id, app_id, user_id, tenant_id, cron, tool_name, params FROM shared.schedules WHERE app_id = :app_id"),
                {"app_id": app_id},
            )
        elif user_id:
            result = await session.execute(
                text("SELECT id, app_id, user_id, tenant_id, cron, tool_name, params FROM shared.schedules WHERE user_id = :user_id"),
                {"user_id": user_id},
            )
        else:
            result = await session.execute(
                text("SELECT id, app_id, user_id, tenant_id, cron, tool_name, params FROM shared.schedules")
            )
        return [dict(row._mapping) for row in result.all()]


@app.delete("/apps/{app_id}/schedules")
async def delete_app_schedules(app_id: str, _: None = Depends(_require_token)):
    if not _APP_ID_RE.match(app_id):
        raise HTTPException(status_code=400, detail="invalid app_id")
    async with SessionLocal() as session:
        result = await session.execute(
            text("SELECT id FROM shared.schedules WHERE app_id = :app_id"),
            {"app_id": app_id},
        )
        ids = [row[0] for row in result.all()]
        for schedule_id in ids:
            scheduler_manager.remove_schedule(schedule_id)
        await session.execute(
            text("DELETE FROM shared.schedules WHERE app_id = :app_id"),
            {"app_id": app_id},
        )
        await session.commit()
    logger.info("deleted %d schedules for app_id=%s", len(ids), app_id)
    return {"cancelled": len(ids), "app_id": app_id}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
