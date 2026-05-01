from __future__ import annotations
import logging
from typing import Any, Optional
import httpx
from sqlalchemy.ext.asyncio import AsyncSession, AsyncEngine, async_sessionmaker
from sqlalchemy import text
from redis.asyncio import Redis

from . import defaults

logger = logging.getLogger(__name__)


class AppContext:
    def __init__(
        self,
        app_id: str,
        user_id: Optional[str],
        tenant_id: Optional[str],
        trace_id: str,
        bridge_url: str,
        db_engine: Optional[AsyncEngine] = None,
        redis_pool: Optional[Redis] = None,
        notification_driver: str = defaults.DEFAULT_NOTIFICATION_DRIVER,
    ) -> None:
        self.app_id = app_id
        self.user_id = user_id
        self.tenant_id = tenant_id
        self.trace_id = trace_id
        self._bridge_url = bridge_url
        self._db_engine = db_engine
        self._redis_pool = redis_pool
        self._notification_driver = notification_driver
        self._db_session: Optional[AsyncSession] = None

    @property
    async def db(self) -> AsyncSession:
        """Returns a DB session scoped to the current tenant and app."""
        if not self._db_engine:
            raise RuntimeError("Database not configured for this app.")
        if not self._db_session:
            session_factory = async_sessionmaker(self._db_engine, expire_on_commit=False)
            self._db_session = session_factory()
            if self.tenant_id:
                schema_name = f"app_{self.tenant_id.replace('-', '_')}_{self.app_id.replace('-', '_')}"
                await self._db_session.execute(text(f"SET search_path TO {schema_name}, public"))
        return self._db_session

    async def notify(
        self,
        title: str,
        body: str = "",
        priority: str = "NORMAL",
        tags: list[str] | None = None,
        click_url: str | None = None,
    ) -> None:
        """Publish a notification to tasks:notifications stream."""
        from .gen import belgrade_os_pb2

        priority_map = {
            "LOW": belgrade_os_pb2.NOTIFICATION_LOW,
            "NORMAL": belgrade_os_pb2.NOTIFICATION_NORMAL,
            "HIGH": belgrade_os_pb2.NOTIFICATION_HIGH,
        }

        req = belgrade_os_pb2.NotificationRequest()
        req.trace_id = self.trace_id or ""
        req.app_id = self.app_id
        req.user_id = self.user_id or ""
        req.title = title
        req.body = body
        req.priority = priority_map.get(priority.upper(), belgrade_os_pb2.NOTIFICATION_NORMAL)
        req.driver = self._notification_driver
        req.tags.extend(tags or [])
        if click_url:
            req.click_url = click_url

        try:
            if not self._redis_pool:
                raise RuntimeError("Redis pool not initialized in AppContext")
            await self._redis_pool.xadd(defaults.STREAM_NOTIFICATIONS, {"data": req.SerializeToString()})
        except Exception as e:
            logger.error("Failed to publish notification: %s", e)

    async def emit(self, topic: str, payload: Any) -> None:
        """Publishes an event to the internal Event Bus via the Bridge."""
        try:
            async with httpx.AsyncClient() as client:
                await client.post(
                    f"{self._bridge_url}/v1/events/publish",
                    json={
                        "topic": topic,
                        "payload": payload,
                        "app_id": self.app_id,
                        "tenant_id": self.tenant_id,
                        "trace_id": self.trace_id,
                    },
                )
        except Exception as e:
            logger.error("Failed to emit event %s: %s", topic, e)

    async def cleanup(self) -> None:
        """Closes the DB session if it was opened. The shared engine and redis pool are NOT closed here."""
        if self._db_session:
            await self._db_session.close()
