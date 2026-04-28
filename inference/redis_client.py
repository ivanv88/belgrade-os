from __future__ import annotations

from typing import Optional

import redis.asyncio as aioredis
import redis.exceptions

INBOUND_STREAM = "tasks:inbound"
TOOL_CALLS_STREAM = "tasks:tool_calls"
TOOL_RESULTS_STREAM = "tasks:tool_results"

_MAX_TOOL_RESULT_ITERATIONS = 10


class RedisClient:
    def __init__(self, url: str) -> None:
        self._redis: aioredis.Redis = aioredis.from_url(url, decode_responses=False)

    async def read_task(
        self, consumer_group: str, consumer_id: str
    ) -> Optional[tuple[str, bytes]]:
        """XREADGROUP from tasks:inbound.

        Returns (message_id, proto_bytes) or None on timeout/empty.
        """
        results = await self._redis.xreadgroup(
            groupname=consumer_group,
            consumername=consumer_id,
            streams={INBOUND_STREAM: ">"},
            count=1,
            block=2000,
        )
        if not results:
            return None
        # results: [(stream_name, [(msg_id, fields), ...])]
        _stream, messages = results[0]
        if not messages:
            return None
        msg_id, fields = messages[0]
        # msg_id may be bytes; normalise to str
        message_id = msg_id.decode() if isinstance(msg_id, bytes) else msg_id
        return message_id, fields[b"data"]

    async def ack_task(self, consumer_group: str, message_id: str) -> None:
        """XACK tasks:inbound."""
        await self._redis.xack(INBOUND_STREAM, consumer_group, message_id)

    async def publish_thought(self, task_id: str, proto_bytes: bytes) -> None:
        """PUBLISH sse:{task_id} proto_bytes."""
        await self._redis.publish(f"sse:{task_id}", proto_bytes)

    async def push_tool_call(self, proto_bytes: bytes) -> None:
        """XADD tasks:tool_calls * data proto_bytes."""
        await self._redis.xadd(TOOL_CALLS_STREAM, {"data": proto_bytes})

    async def read_tool_result(
        self, consumer_group: str, consumer_id: str, task_id: str
    ) -> Optional[tuple[str, bytes]]:
        """XREADGROUP from tasks:tool_results, filtered to matching task_id.

        Loops up to _MAX_TOOL_RESULT_ITERATIONS times.  ACKs and discards
        messages whose task_id does not match.  Returns (message_id,
        proto_bytes) for the first match, or None if nothing matches.
        """
        expected_task_id = task_id.encode()
        for _ in range(_MAX_TOOL_RESULT_ITERATIONS):
            results = await self._redis.xreadgroup(
                groupname=consumer_group,
                consumername=consumer_id,
                streams={TOOL_RESULTS_STREAM: ">"},
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
            if fields.get(b"task_id") == expected_task_id:
                return message_id, fields[b"data"]
            # Mismatch — ACK and discard, then keep looping.
            await self._redis.xack(TOOL_RESULTS_STREAM, consumer_group, message_id)
        return None

    async def ack_tool_result(self, consumer_group: str, message_id: str) -> None:
        """XACK tasks:tool_results."""
        await self._redis.xack(TOOL_RESULTS_STREAM, consumer_group, message_id)

    async def ensure_consumer_groups(self) -> None:
        """Create consumer groups for tasks:inbound and tasks:tool_results.

        Uses MKSTREAM so the streams are created if they don't exist yet.
        Ignores BUSYGROUP errors (group already exists).
        """
        for stream in (INBOUND_STREAM, TOOL_RESULTS_STREAM):
            try:
                await self._redis.xgroup_create(stream, "inference", id="0", mkstream=True)
            except redis.exceptions.ResponseError as exc:
                if "BUSYGROUP" not in str(exc):
                    raise

    async def close(self) -> None:
        await self._redis.aclose()
