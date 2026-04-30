from __future__ import annotations
import logging
from typing import Any, Optional
import httpx
import redis.asyncio as aioredis
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy import text

logger = logging.getLogger(__name__)


class AppContext:
    def __init__(
        self,
        app_id: str,
        user_id: Optional[str],
        tenant_id: Optional[str],
        trace_id: str,
        bridge_url: str,
        db_url: Optional[str] = None,
        redis_url: str = "redis://localhost:6379",
        notification_driver: str = "ntfy",
    ) -> None:
        self.app_id = app_id
        self.user_id = user_id
        self.tenant_id = tenant_id
        self.trace_id = trace_id
        self._bridge_url = bridge_url
        self._db_url = db_url
        self._redis_url = redis_url
        self._notification_driver = notification_driver
        self._db_session: Optional[AsyncSession] = None
        self._redis_client: Optional[aioredis.Redis] = None

    @property
    async def db(self) -> AsyncSession:
        """Returns a DB session scoped to the current tenant and app."""
        if not self._db_url:
            raise RuntimeError("Database not configured for this app.")
        if not self._db_session:
            engine = create_async_engine(self._db_url)
            session_factory = async_sessionmaker(engine, expire_on_commit=False)
            self._db_session = session_factory()
            if self.tenant_id:
                schema_name = f"app_{self.tenant_id.replace('-', '_')}_{self.app_id.replace('-', '_')}"
                await self._db_session.execute(text(f"SET search_path TO {schema_name}, public"))
        return self._db_session

    async def _get_redis(self) -> aioredis.Redis:
        if not self._redis_client:
            self._redis_client = aioredis.from_url(self._redis_url, decode_responses=False)
        return self._redis_client

    async def notify(
        self,
        title: str,
        body: str = "",
        priority: str = "NORMAL",
        tags: list[str] | None = None,
    ) -> None:
        """Publish a notification to tasks:notifications stream.

        The notification service picks it up and dispatches via the driver
        configured in BEG_OS_NOTIFICATION_DRIVER (stamped by the platform
        controller from the app's manifest.json).
        """
        from gen import belgrade_os_pb2

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

        try:
            r = await self._get_redis()
            await r.xadd("tasks:notifications", {"data": req.SerializeToString()})
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
        """Closes the DB session and Redis client if opened."""
        if self._db_session:
            await self._db_session.close()
        if self._redis_client:
            await self._redis_client.aclose()
