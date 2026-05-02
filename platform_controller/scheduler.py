from __future__ import annotations
import logging
import json
import httpx
from datetime import datetime
from typing import Dict, List, Optional, Any
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from pydantic import BaseModel
from sqlalchemy import text
from redis_client import RedisClient

logger = logging.getLogger(__name__)

class ScheduleEntry(BaseModel):
    id: str
    user_id: str
    tenant_id: str
    cron: str
    tool_name: str
    params: Dict = {}

class SchedulerManager:
    def __init__(self, bridge_url: str):
        self.scheduler = AsyncIOScheduler()
        self.bridge_url = bridge_url
        self.active_jobs: Dict[str, Any] = {}

    def start(self):
        self.scheduler.start()
        logger.info("Dynamic Scheduler started")

    async def add_schedule(self, entry: ScheduleEntry):
        """Adds a job to the scheduler. If ID exists, it replaces it."""
        if entry.id in self.active_jobs:
            self.remove_schedule(entry.id)

        async def job_wrapper():
            await self._execute_tool(entry)

        trigger = CronTrigger.from_crontab(entry.cron)
        job = self.scheduler.add_job(
            job_wrapper,
            trigger,
            id=entry.id,
            replace_existing=True
        )
        self.active_jobs[entry.id] = job
        logger.info(f"Scheduled tool {entry.tool_name} for user {entry.user_id} (Cron: {entry.cron})")

    def remove_schedule(self, schedule_id: str):
        if schedule_id in self.active_jobs:
            self.scheduler.remove_job(schedule_id)
            del self.active_jobs[schedule_id]
            logger.info(f"Removed schedule: {schedule_id}")

    async def _execute_tool(self, entry: ScheduleEntry):
        """Dispatches the tool call to the Bridge."""
        import uuid
        call_id = str(uuid.uuid4())
        task_id = f"sched-{entry.id}-{datetime.now().strftime('%Y%m%d%H%M')}"
        
        payload = {
            "call_id": call_id,
            "task_id": task_id,
            "tool_name": entry.tool_name,
            "input_json": json.dumps(entry.params),
            "user_id": entry.user_id,
            "tenant_id": entry.tenant_id,
            "trace_id": f"trace-{uuid.uuid4()}"
        }
        
        logger.info(f"Firing scheduled tool: {entry.tool_name} (Task: {task_id})")
        
        async with httpx.AsyncClient() as client:
            try:
                # The bridge.execute endpoint expects the runner's ExecuteRequest shape
                resp = await client.post(f"{self.bridge_url}/v1/execute", json=payload)
                resp.raise_for_status()
                result = resp.json()
                if not result.get("success"):
                    logger.error(f"Scheduled tool {entry.tool_name} failed: {result.get('error')}")
            except Exception as e:
                logger.error(f"Failed to dispatch scheduled tool {entry.tool_name}: {e}")

class PermissionSyncManager:
    def __init__(self, db_engine, redis_url: str):
        self.engine = db_engine
        self.redis_url = redis_url
        self.redis = RedisClient(redis_url)
        self.scheduler = AsyncIOScheduler()

    def start(self):
        self.scheduler.add_job(
            self.sync_all,
            "interval",
            minutes=5,
            id="perm_sync",
            replace_existing=True
        )
        self.scheduler.start()
        logger.info("Permission Sync Manager started (5m interval)")

    async def sync_all(self):
        """Fetch all permissions from DB and push to Redis."""
        logger.info("Syncing permissions to Redis...")
        try:
            async with self.engine.connect() as conn:
                result = await conn.execute(text(
                    "SELECT user_id, app_id, bundle_id, role FROM shared.app_permissions"
                ))
                
                # Group by user_id
                user_perms = {}
                for row in result.all():
                    uid, app_id, bundle, role = row
                    if uid not in user_perms:
                        user_perms[uid] = {}
                    user_perms[uid][f"{app_id}:{bundle}"] = role

                # Sync each user
                for uid, perms in user_perms.items():
                    await self.redis.sync_permissions(uid, perms)
                
                logger.info(f"Synced permissions for {len(user_perms)} users")
        except Exception as e:
            logger.error(f"Failed to sync permissions: {e}")

    async def close(self):
        await self.redis.close()
