from __future__ import annotations
from typing import Optional
import redis.asyncio as aioredis
import redis.exceptions

NOTIFICATIONS_STREAM = "tasks:notifications"
CONSUMER_GROUP = "notification-workers"


class RedisClient:
    def __init__(self, url: str) -> None:
        self._redis: aioredis.Redis = aioredis.from_url(url, decode_responses=False)

    async def read_notification(
        self, consumer_group: str, consumer_id: str
    ) -> Optional[tuple[str, bytes]]:
        """XREADGROUP from tasks:notifications. Returns (message_id, proto_bytes) or None."""
        results = await self._redis.xreadgroup(
            groupname=consumer_group,
            consumername=consumer_id,
            streams={NOTIFICATIONS_STREAM: ">"},
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
            raise ValueError(f"Stream message {message_id} missing 'data' field. Got: {list(fields)!r}")
        return message_id, payload

    async def ack_notification(self, consumer_group: str, message_id: str) -> None:
        """XACK tasks:notifications."""
        await self._redis.xack(NOTIFICATIONS_STREAM, consumer_group, message_id)

    async def ensure_consumer_group(self) -> None:
        """XGROUP CREATE tasks:notifications notification-workers MKSTREAM. Ignores BUSYGROUP."""
        try:
            await self._redis.xgroup_create(
                NOTIFICATIONS_STREAM, CONSUMER_GROUP, id="0", mkstream=True
            )
        except redis.exceptions.ResponseError as exc:
            if "BUSYGROUP" not in str(exc):
                raise

    async def close(self) -> None:
        await self._redis.aclose()
