import asyncio
import os
import signal
import subprocess
import logging
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy import text, Column, String, JSON, DateTime
from sqlalchemy.orm import declarative_base

from scheduler import SchedulerManager, ScheduleEntry

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# --- Database Setup ---
DB_URL = os.getenv("DATABASE_URL", "postgresql+asyncpg://postgres:postgres@localhost:5432/postgres")
engine = create_async_engine(DB_URL)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False)

# --- App Supervision ---
class AppProcess:
    def __init__(self, app_id: str, path: Path, port: int):
        self.app_id = app_id
        self.path = path
        self.port = port
        self.process: Optional[subprocess.Popen] = None

    def _load_manifest(self) -> dict:
        """Load manifest.json from the app directory. Returns {} if absent or invalid."""
        manifest_path = self.path / "manifest.json"
        if manifest_path.exists():
            try:
                import json
                with open(manifest_path) as f:
                    return json.load(f)
            except Exception:
                logger.warning("Failed to load manifest for %s", self.app_id)
        return {}

    async def start(self):
        manifest = self._load_manifest()
        # Per-app driver from manifest overrides the global env var.
        notification_driver = (
            manifest.get("notifications", {}).get("driver")
            or os.getenv("BEG_OS_NOTIFICATION_DRIVER", "ntfy")
        )

        env = os.environ.copy()
        env["BEG_OS_APP_ID"] = self.app_id
        env["BEG_OS_CALLBACK_URL"] = f"http://localhost:{self.port}"
        env["BEG_OS_BRIDGE_URL"] = os.getenv("BEG_OS_BRIDGE_URL", "http://localhost:8081")
        env["BEG_OS_DB_URL"] = DB_URL
        env["BEG_OS_REDIS_URL"] = os.getenv("BEG_OS_REDIS_URL", "redis://localhost:6379")
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
        await app_process.start()
        
        self.running_apps[app_id] = app_process
        self.next_port += 1

    async def stop_app(self, app_id: str):
        if app_id in self.running_apps:
            await self.running_apps[app_id].stop()
            del self.running_apps[app_id]

# --- FastAPI App ---
app = FastAPI(title="Belgrade Platform Controller")
bridge_url = os.getenv("BEG_OS_BRIDGE_URL", "http://localhost:8081")
app_supervisor = AppSupervisor(apps_root=Path(__file__).parent.parent / "apps")
scheduler_manager = SchedulerManager(bridge_url=bridge_url)

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

    # 2. Start Apps
    await app_supervisor.discover_and_start()

    # 3. Start Scheduler & Load existing jobs
    scheduler_manager.start()
    async with SessionLocal() as session:
        result = await session.execute(text("SELECT id, user_id, tenant_id, cron, tool_name, params FROM shared.schedules"))
        for row in result.all():
            entry = ScheduleEntry(
                id=row[0],
                user_id=row[1],
                tenant_id=row[2],
                cron=row[3],
                tool_name=row[4],
                params=row[5]
            )
            await scheduler_manager.add_schedule(entry)

@app.post("/apps/reload")
async def reload_app(action: AppAction):
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
            INSERT INTO shared.schedules (id, user_id, tenant_id, cron, tool_name, params, updated_at)
            VALUES (:id, :user_id, :tenant_id, :cron, :tool_name, :params, NOW())
            ON CONFLICT (id) DO UPDATE SET
                user_id = EXCLUDED.user_id,
                tenant_id = EXCLUDED.tenant_id,
                cron = EXCLUDED.cron,
                tool_name = EXCLUDED.tool_name,
                params = EXCLUDED.params,
                updated_at = NOW()
        """), entry.dict())
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
async def list_schedules():
    async with SessionLocal() as session:
        result = await session.execute(text("SELECT id, user_id, tenant_id, cron, tool_name, params FROM shared.schedules"))
        return [dict(row._mapping) for row in result.all()]

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
