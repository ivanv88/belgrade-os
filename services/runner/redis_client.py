from __future__ import annotations
from typing import Optional
import redis.asyncio as aioredis
import redis.exceptions

TOOL_CALLS_STREAM   = "tasks:tool_calls"
TOOL_RESULTS_STREAM = "tasks:tool_results"
LEASE_KEY_PREFIX    = "lease"
_DEFAULT_GROUP      = "tool-runners"


class RedisClient:
    def __init__(self, url: str) -> None:
        self._redis: aioredis.Redis = aioredis.from_url(url, decode_responses=False)

    async def read_tool_call(
        self, consumer_group: str, consumer_id: str
    ) -> Optional[tuple[str, bytes]]:
        """XREADGROUP from tasks:tool_calls. Returns (message_id, proto_bytes) or None."""
        results = await self._redis.xreadgroup(
            groupname=consumer_group,
            consumername=consumer_id,
            streams={TOOL_CALLS_STREAM: ">"},
            count=1,
            block=2000,
        )
        if not results:
            return None
        _stream, messages = results[0]
        if not messages:
            return None
        msg_id, fields = messages[0]
        message_id = msg_id.decode() if isinstance(msg_id, bytes) else msg_id
        payload = fields.get(b"data")
        if payload is None:
            raise ValueError(f"Stream message {message_id} missing required 'data' field. Got keys: {list(fields)!r}")
        return message_id, payload

    async def ack_tool_call(self, consumer_group: str, message_id: str) -> None:
        """XACK tasks:tool_calls."""
        await self._redis.xack(TOOL_CALLS_STREAM, consumer_group, message_id)

    async def write_tool_result(self, task_id: str, proto_bytes: bytes) -> None:
        """XADD tasks:tool_results with both data and task_id fields.

        The task_id field is required by the Inference Controller's read_tool_result,
        which filters stream entries by b"task_id" to match results to their task.
        """
        await self._redis.xadd(
            TOOL_RESULTS_STREAM,
            {"data": proto_bytes, "task_id": task_id},
        )

    async def set_lease(self, worker_id: str, proto_bytes: bytes, ttl_s: int) -> None:
        """SET lease:{worker_id} to serialised WorkerLease proto with TTL."""
        if ttl_s <= 0:
            raise ValueError(f"ttl_s must be a positive integer, got {ttl_s!r}")
        await self._redis.set(f"{LEASE_KEY_PREFIX}:{worker_id}", proto_bytes, ex=ttl_s)

    async def delete_lease(self, worker_id: str) -> None:
        """DEL lease:{worker_id}."""
        await self._redis.delete(f"{LEASE_KEY_PREFIX}:{worker_id}")

    async def ensure_consumer_group(self) -> None:
        """XGROUP CREATE tasks:tool_calls tool-runners MKSTREAM. Ignores BUSYGROUP."""
        try:
            await self._redis.xgroup_create(
                TOOL_CALLS_STREAM, _DEFAULT_GROUP, id="0", mkstream=True
            )
        except redis.exceptions.ResponseError as exc:
            if "BUSYGROUP" not in str(exc):
                raise

    async def close(self) -> None:
        await self._redis.aclose()
