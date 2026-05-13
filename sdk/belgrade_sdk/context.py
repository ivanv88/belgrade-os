from __future__ import annotations
import asyncio
import json
import logging
import time
import uuid as _uuid
from dataclasses import dataclass
from typing import Any, Optional, Union
import httpx
from sqlalchemy.ext.asyncio import AsyncSession, AsyncEngine, async_sessionmaker
from sqlalchemy import text
from redis.asyncio import Redis

from . import defaults

logger = logging.getLogger(__name__)


class VaultAdapter:
    def __init__(self, ctx: AppContext):
        self.ctx = ctx

    async def write(self, path: str, content: Union[str, bytes]) -> None:
        """Indirect write to the vault via Redis stream."""
        from .gen import belgrade_os_pb2

        if isinstance(content, str):
            content_bytes = content.encode("utf-8")
        else:
            content_bytes = content

        op = belgrade_os_pb2.VaultOperation()
        op.trace_id = self.ctx.trace_id or ""
        op.app_id = self.ctx.app_id
        op.op = belgrade_os_pb2.VaultOperation.WRITE
        op.path = path
        op.content = content_bytes

        try:
            if not self.ctx._redis_pool:
                raise RuntimeError("Redis pool not initialized in AppContext")
            await self.ctx._redis_pool.xadd("tasks:vault_ops", {"data": op.SerializeToString()})
        except Exception as e:
            logger.error("Failed to publish vault operation: %s", e)

    async def delete(self, path: str) -> None:
        """Indirect delete from the vault via Redis stream."""
        from .gen import belgrade_os_pb2

        op = belgrade_os_pb2.VaultOperation()
        op.trace_id = self.ctx.trace_id or ""
        op.app_id = self.ctx.app_id
        op.op = belgrade_os_pb2.VaultOperation.DELETE
        op.path = path

        try:
            if not self.ctx._redis_pool:
                raise RuntimeError("Redis pool not initialized in AppContext")
            await self.ctx._redis_pool.xadd("tasks:vault_ops", {"data": op.SerializeToString()})
        except Exception as e:
            logger.error("Failed to publish vault operation: %s", e)


@dataclass
class InferenceResult:
    text: str
    tool_calls: list[str]
    trace_id: str


class InferenceAdapter:
    def __init__(self, ctx: "AppContext"):
        self.ctx = ctx

    async def request(self, prompt: str) -> dict:
        from .gen import belgrade_os_pb2

        if not self.ctx._redis_pool:
            raise RuntimeError("Redis pool not initialized in AppContext")

        task_id = str(_uuid.uuid4())
        trace_id = self.ctx.trace_id or str(_uuid.uuid4())

        task = belgrade_os_pb2.Task()
        task.task_id = task_id
        task.user_id = self.ctx.user_id or ""
        task.prompt = prompt
        task.created_at_ms = int(time.time() * 1000)
        task.trace_id = trace_id
        task.execution_mode = belgrade_os_pb2.ExecutionMode.Value("UNTRUSTED")
        task.app_id = self.ctx.app_id
        task.tenant_id = self.ctx.tenant_id or ""

        try:
            await self.ctx._redis_pool.xadd(
                defaults.STREAM_TASKS_INBOUND,
                {"data": task.SerializeToString()},
                maxlen=1000,
                approximate=True,
            )
        except Exception as e:
            logger.error("Failed to publish inference task: %s", e)
            raise

        return {"task_id": task_id, "trace_id": trace_id}

    async def cancel(self, task_id: str) -> None:
        if not self.ctx._redis_pool:
            raise RuntimeError("Redis pool not initialized in AppContext")
        await self.ctx._redis_pool.set(f"tasks:cancel:{task_id}", "1", ex=3600)

    async def stream(self, task_id: str, timeout: float = 300.0):
        from .gen import belgrade_os_pb2
        from .exceptions import InferenceTimeoutError

        if not self.ctx._redis_pool:
            raise RuntimeError("Redis pool not initialized in AppContext")

        pubsub = self.ctx._redis_pool.pubsub()
        await pubsub.subscribe(f"sse:{task_id}")
        try:
            deadline = asyncio.get_running_loop().time() + timeout
            while True:
                remaining = deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    raise InferenceTimeoutError(
                        f"No terminal event for task {task_id} within {timeout}s"
                    )
                try:
                    message = await asyncio.wait_for(
                        pubsub.get_message(ignore_subscribe_messages=True, timeout=0.5),
                        timeout=min(remaining, 2.0),
                    )
                except asyncio.TimeoutError:
                    continue
                if message is None:
                    continue
                event = belgrade_os_pb2.ThoughtEvent()
                event.ParseFromString(message["data"])
                yield event
                if event.type in (belgrade_os_pb2.DONE, belgrade_os_pb2.ERROR):
                    return
        finally:
            await pubsub.unsubscribe(f"sse:{task_id}")
            await pubsub.aclose()

    async def await_result(self, task_id: str, mode: str = "text", timeout: float = 300.0):
        from .gen import belgrade_os_pb2
        from .exceptions import InferenceError

        events = []
        async for event in self.stream(task_id, timeout=timeout):
            events.append(event)

        for event in events:
            if event.type == belgrade_os_pb2.ERROR:
                raise InferenceError(event.content)

        if mode == "text":
            return "".join(
                e.content for e in events if e.type == belgrade_os_pb2.RESPONSE_CHUNK
            )
        if mode == "full":
            return events
        if mode == "summary":
            text = "".join(
                e.content for e in events if e.type == belgrade_os_pb2.RESPONSE_CHUNK
            )
            tool_calls = [
                e.content for e in events if e.type == belgrade_os_pb2.TOOL_USE
            ]
            trace_id = events[0].trace_id if events else ""
            return InferenceResult(text=text, tool_calls=tool_calls, trace_id=trace_id)
        raise ValueError(f"Unknown mode {mode!r}. Use 'text', 'full', or 'summary'.")


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
    def vault(self) -> VaultAdapter:
        return VaultAdapter(self)

    @property
    def inference(self) -> "InferenceAdapter":
        return InferenceAdapter(self)

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

    async def schedule(
        self,
        name: str,
        cron: str,
        tool_name: str,
        params: dict | None = None,
    ) -> None:
        """Schedule a recurring tool call via tasks:schedule_ops stream."""
        from .gen import belgrade_os_pb2

        # Raise on failure — unlike notify(), schedule registration must be confirmed.
        if not self._redis_pool:
            raise RuntimeError("Redis pool not initialized in AppContext")

        op = belgrade_os_pb2.ScheduleOp()
        op.op = belgrade_os_pb2.ScheduleOp.UPSERT
        op.schedule_id = f"{self.app_id}:{self.user_id or ''}:{name}"
        op.app_id = self.app_id
        op.user_id = self.user_id or ""
        op.tenant_id = self.tenant_id or ""
        op.cron = cron
        op.tool_name = tool_name
        op.params_json = json.dumps(params or {})
        op.trace_id = self.trace_id or ""

        await self._redis_pool.xadd(
            defaults.STREAM_SCHEDULE_OPS,
            {"data": op.SerializeToString()},
        )

    async def unschedule(self, name: str) -> None:
        """Cancel a scheduled tool call via tasks:schedule_ops stream."""
        from .gen import belgrade_os_pb2

        # Raise on failure — unlike notify(), schedule registration must be confirmed.
        if not self._redis_pool:
            raise RuntimeError("Redis pool not initialized in AppContext")

        op = belgrade_os_pb2.ScheduleOp()
        op.op = belgrade_os_pb2.ScheduleOp.DELETE
        op.schedule_id = f"{self.app_id}:{self.user_id or ''}:{name}"
        op.app_id = self.app_id
        op.user_id = self.user_id or ""
        op.trace_id = self.trace_id or ""
        op.params_json = "{}"

        await self._redis_pool.xadd(
            defaults.STREAM_SCHEDULE_OPS,
            {"data": op.SerializeToString()},
        )

    async def cleanup(self) -> None:
        """Closes the DB session if it was opened. The shared engine and redis pool are NOT closed here."""
        if self._db_session:
            await self._db_session.close()
