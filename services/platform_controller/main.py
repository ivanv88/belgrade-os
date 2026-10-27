import asyncio
import json
import os
import logging
import time
from pathlib import Path
from typing import Awaitable, Callable, Dict, List, Literal, Mapping, Optional
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, ValidationError
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy import text, Column, String, JSON, DateTime
from sqlalchemy.orm import declarative_base
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError

from scheduler import SchedulerManager, ScheduleEntry, PermissionSyncManager
from ephemeral_runner import EphemeralRunner, OutputValidationError
from config import load_config as _load_config
from app_runner import SubprocessRunner, SystemdRunner
from app_db import app_db_url, app_role_name, app_role_password, ensure_app_role

import re
import secrets
from fastapi import Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

_cfg = _load_config()

# --- Database Setup ---
DB_URL = _cfg.db_url
REDIS_URL = _cfg.redis_url
CONTROLLER_TOKEN = os.getenv("CONTROLLER_API_TOKEN", "")
SECCOMP_PROFILE = os.getenv("SECCOMP_PROFILE", "/config/seccomp-untrusted.json")
APPS_ROOT = Path(os.getenv("APPS_ROOT", str(Path(__file__).parent.parent.parent / "apps")))
# Apps import belgrade_sdk from here; the controller puts it on each app's PYTHONPATH.
SDK_ROOT = Path(__file__).resolve().parent.parent.parent / "sdk"

_ephemeral_runner = EphemeralRunner(seccomp_profile=SECCOMP_PROFILE, apps_root=APPS_ROOT)
_bearer = HTTPBearer(auto_error=False)
_APP_ID_RE = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")
# Process-managed apps also get a DB role (app_db.app_role_name): lowercase, no hyphens.
_RELOAD_APP_ID_RE = re.compile(r"^[a-z0-9_]{1,59}$")


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
    runtime: Literal["platform_process", "container"] = "platform_process"
    endpoint: Optional[str] = None
    name: Optional[str] = None
    ui: Optional[_AppUIManifest] = None
    related_apps: List[str] = []
    notifications: Optional[_NotificationsManifest] = None
    mcp: bool = False


# --- App Supervision ---
# Apps are less trusted than the platform. The controller's environment holds every
# platform secret (MCP_JWT_SECRET, CF_TUNNEL_TOKEN, per-service Redis passwords, ...),
# so apps get an allowlist instead of a copy: process basics, the BEG_OS_* namespace
# meant for apps, and the values AppProcess.build_env() sets explicitly.
_APP_ENV_PASSTHROUGH = frozenset({
    "PATH", "HOME", "USER", "LOGNAME", "LANG", "LC_ALL", "LC_CTYPE", "TZ", "TMPDIR",
    "PYTHONPATH", "PYTHONUNBUFFERED",
})


_APP_IDENTITY_VARS = ("HOME", "USER", "LOGNAME", "TMPDIR")


def _app_base_env(parent: Mapping[str, str]) -> Dict[str, str]:
    return {
        k: v for k, v in parent.items()
        if k in _APP_ENV_PASSTHROUGH or k.startswith("BEG_OS_")
    }


class AppProcess:
    def __init__(self, app_id: str, path: Path, port: int):
        self.app_id = app_id
        self.path = path
        self.port = port

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
            manifest = _AppManifest.model_validate(data)
        except ValidationError as exc:
            raise ValueError(
                f"manifest.json for app '{self.app_id}' failed schema validation:\n{exc}"
            ) from exc
        if manifest.app_id != self.app_id:
            raise ValueError(
                f"manifest.json app_id '{manifest.app_id}' does not match directory name '{self.app_id}'"
            )
        return manifest

    def build_env(self, db_url: str, inherit_identity: bool = True) -> Dict[str, str]:
        """The environment the app runs with. `inherit_identity=False` drops the
        controller's HOME/USER/LOGNAME/TMPDIR: under systemd the unit's DynamicUser
        sets its own, and EnvironmentFile assignments would override them."""
        manifest = self._load_manifest()
        # Per-app driver from manifest overrides the global env var.
        notification_driver = (
            (manifest.notifications.driver if manifest and manifest.notifications else None)
            or os.getenv("BEG_OS_NOTIFICATION_DRIVER", "ntfy")
        )

        env = _app_base_env(os.environ)
        if not inherit_identity:
            for key in _APP_IDENTITY_VARS:
                env.pop(key, None)
        env["PYTHONPATH"] = os.pathsep.join(
            [str(SDK_ROOT)] + [p for p in env.get("PYTHONPATH", "").split(os.pathsep) if p]
        )
        env["BEG_OS_APP_ID"] = self.app_id
        env["BEG_OS_PORT"] = str(self.port)
        env["BEG_OS_CALLBACK_URL"] = f"http://localhost:{self.port}"
        env["BEG_OS_BRIDGE_URL"] = _cfg.bridge_url
        env["BEG_OS_DB_URL"] = db_url
        env["BEG_OS_REDIS_URL"] = os.getenv("APP_REDIS_URL") or os.getenv("BEG_OS_REDIS_URL", "redis://localhost:6379")
        env["BEG_OS_NOTIFICATION_DRIVER"] = notification_driver
        if manifest and manifest.mcp:
            env["BEG_OS_MCP_ENABLED"] = "true"
        else:
            env.pop("BEG_OS_MCP_ENABLED", None)
        return env


class AppSupervisor:
    def __init__(
        self,
        apps_root: Path,
        runner=None,
        db_url_for_app: Optional[Callable[[str], Awaitable[str]]] = None,
    ):
        self.apps_root = apps_root
        self.runner = runner or SubprocessRunner()
        # A production (isolated) runner must never fall back to the platform's superuser DB URL.
        if self.runner.isolated and db_url_for_app is None:
            raise ValueError("an isolating app runner requires db_url_for_app (no superuser DB fallback)")
        self.db_url_for_app = db_url_for_app
        self.running_apps: Dict[str, AppProcess] = {}
        # Apps whose last start failed; the watchdog keeps retrying them.
        self.failed_apps: set[str] = set()
        self._locks: Dict[str, asyncio.Lock] = {}
        # Ports are sticky per app_id so restarts keep the bridge callback URL valid.
        self.app_ports: Dict[str, int] = {}
        self.next_port = 9001

    def _lock(self, app_id: str) -> asyncio.Lock:
        return self._locks.setdefault(app_id, asyncio.Lock())

    async def discover_and_start(self):
        if not self.apps_root.exists():
            logger.warning(f"Apps root {self.apps_root} does not exist.")
            return
        for app_dir in self.apps_root.iterdir():
            if not app_dir.is_dir():
                continue
            has_main = (app_dir / "main.py").exists()
            has_manifest = (app_dir / "manifest.json").exists()
            if has_main or has_manifest:
                await self._start_isolated(app_dir.name)

    async def _start_isolated(self, app_id: str) -> bool:
        """start_app, but nothing an app does can escape to its siblings or the caller."""
        try:
            return await self.start_app(app_id)
        except Exception:
            logger.exception("Unexpected error starting app %s", app_id)
            self.failed_apps.add(app_id)
            return False

    async def start_app(self, app_id: str) -> bool:
        """Start (or restart) an app. True if it is running or needs no process."""
        async with self._lock(app_id):
            return await self._start_app_locked(app_id)

    async def _start_app_locked(self, app_id: str) -> bool:
        app_path = self.apps_root / app_id

        # Container apps self-manage their lifecycle — skip process spawning.
        manifest_path = app_path / "manifest.json"
        if manifest_path.exists():
            try:
                data = json.loads(manifest_path.read_text(encoding="utf-8"))
                if data.get("runtime") == "container":
                    logger.info("App %s is a container app — skipping process management", app_id)
                    self.failed_apps.discard(app_id)
                    return True
            except (json.JSONDecodeError, OSError):
                pass  # Let AppProcess validate fully below

        # Manifest-only dirs are static UI bundles served by the gateway — no process.
        # Spawning them would crash-loop under the watchdog.
        if not (app_path / "main.py").exists():
            logger.info("App %s has no main.py — skipping process management", app_id)
            self.failed_apps.discard(app_id)
            return True

        if app_id not in self.app_ports:
            self.app_ports[app_id] = self.next_port
            self.next_port += 1

        app_process = AppProcess(app_id, app_path, self.app_ports[app_id])
        try:
            db_url = await self.db_url_for_app(app_id) if self.db_url_for_app else DB_URL
            env = app_process.build_env(db_url, inherit_identity=not self.runner.isolated)
            await self.runner.start(app_id, app_path, env)
        except (ValueError, RuntimeError, OSError) as exc:
            logger.error("Skipping app %s — %s", app_id, exc)
            self.running_apps.pop(app_id, None)
            self.failed_apps.add(app_id)
            return False

        self.running_apps[app_id] = app_process
        self.failed_apps.discard(app_id)
        return True

    async def stop_app(self, app_id: str):
        async with self._lock(app_id):
            if app_id in self.running_apps:
                try:
                    await self.runner.stop(app_id)
                except RuntimeError as exc:
                    logger.error("Failed to stop app %s — %s", app_id, exc)
                    return  # still running as far as we know; keep it visible
                del self.running_apps[app_id]

    async def _watch_tick(self) -> None:
        """Restart apps that have exited, and retry apps whose last start failed."""
        names = list(self.running_apps)
        alive = await asyncio.gather(
            *(self.runner.is_running(a) for a in names), return_exceptions=True
        )
        dead = [a for a, ok in zip(names, alive) if ok is not True]
        for app_id, ok in zip(names, alive):
            if isinstance(ok, Exception):
                logger.error("status check of app %s failed: %r", app_id, ok)
        for app_id in dead:
            logger.warning("app %s is not running — restarting", app_id)
            await self._start_isolated(app_id)
        for app_id in sorted(self.failed_apps - set(dead)):
            logger.warning("retrying failed start of app %s", app_id)
            await self._start_isolated(app_id)

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
        # Validate cron before persisting — an invalid cron would cause add_schedule()
        # to raise after the DB commit, leaving a bad row that wedges the consumer on restart.
        try:
            from apscheduler.triggers.cron import CronTrigger
            CronTrigger.from_crontab(entry.cron)
        except ValueError:
            logger.error("invalid cron %r in ScheduleOp id=%r — discarding", op.cron, op.schedule_id)
            return
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


async def _provision_app_db(app_id: str) -> str:
    role = app_role_name(app_id)
    password = app_role_password(_cfg.app_db_secret, app_id)
    try:
        async with engine.begin() as conn:
            await ensure_app_role(conn, role, password, make_url(DB_URL).database)
    except DBAPIError as e:
        # The statement text can carry the role password; never let it reach the logs.
        raise RuntimeError(
            f"provisioning DB role for {app_id} failed: {type(e.orig).__name__}"
        ) from None
    return app_db_url(DB_URL, role, password)


def _check_apps_root(runner, apps_root: Path, repo_apps_root: Path) -> None:
    """The systemd unit template hard-codes <repo root>/apps; a different root would run nothing."""
    if runner.isolated and Path(apps_root).resolve() != Path(repo_apps_root).resolve():
        raise RuntimeError(
            f"APPS_ROOT {apps_root} is not {repo_apps_root} — the systemd unit template hard-codes "
            "that path; refusing to start with the sandboxed runner"
        )


def _build_supervisor(cfg, apps_root: Path = APPS_ROOT, repo_apps_root: Path = SDK_ROOT.parent / "apps") -> AppSupervisor:
    runner = (SystemdRunner(env_dir=Path(cfg.app_env_dir)) if cfg.app_runner == "systemd"
              else SubprocessRunner())
    _check_apps_root(runner, apps_root, repo_apps_root)
    if not cfg.app_db_secret:
        logger.warning("APP_DB_SECRET not set — apps share the platform DB user (dev only)")
    return AppSupervisor(
        apps_root=apps_root,
        runner=runner,
        db_url_for_app=_provision_app_db if cfg.app_db_secret else None,
    )


app_supervisor = _build_supervisor(_cfg)
scheduler_manager = SchedulerManager(bridge_url=_cfg.bridge_url)
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
    if not _RELOAD_APP_ID_RE.match(action.app_id):
        raise HTTPException(status_code=400, detail="invalid app_id: must match ^[a-z0-9_]{1,59}$")
    if not await app_supervisor.start_app(action.app_id):
        raise HTTPException(status_code=502, detail=f"app {action.app_id} failed to start; see controller logs")
    return {"status": "reloaded", "app_id": action.app_id}

@app.get("/apps")
async def list_apps():
    apps = {}
    for app_id, proc in list(app_supervisor.running_apps.items()):
        apps[app_id] = {"port": proc.port, "pid": await app_supervisor.runner.pid(app_id), "status": "running"}
    for app_id in sorted(app_supervisor.failed_apps):
        apps[app_id] = {"port": app_supervisor.app_ports.get(app_id), "pid": None, "status": "failed"}
    return apps

@app.post("/schedules")
async def create_schedule(entry: ScheduleEntry, _: None = Depends(_require_token)):
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
async def delete_schedule(schedule_id: str, _: None = Depends(_require_token)):
    async with SessionLocal() as session:
        await session.execute(text("DELETE FROM shared.schedules WHERE id = :id"), {"id": schedule_id})
        await session.commit()
    
    scheduler_manager.remove_schedule(schedule_id)
    return {"status": "deleted", "id": schedule_id}

@app.get("/schedules")
async def list_schedules(app_id: Optional[str] = None, user_id: Optional[str] = None, _: None = Depends(_require_token)):
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
    uvicorn.run(app, host=_cfg.host, port=_cfg.port)
