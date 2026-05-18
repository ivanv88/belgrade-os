from __future__ import annotations
from typing import Optional
import redis.asyncio as aioredis
import redis.exceptions

VAULT_OPS_STREAM = "tasks:vault_ops"
CONSUMER_GROUP = "vault-workers"


class RedisClient:
    def __init__(self, url: str) -> None:
        self._redis: aioredis.Redis = aioredis.from_url(url, decode_responses=False)

    async def read_vault_op(
        self, consumer_group: str, consumer_id: str
    ) -> Optional[tuple[str, bytes]]:
        """XREADGROUP from tasks:vault_ops."""
        try:
            results = await self._redis.xreadgroup(
                groupname=consumer_group,
                consumername=consumer_id,
                streams={VAULT_OPS_STREAM: ">"},
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
            return message_id, payload
        except Exception:
            return None

    async def ack_vault_op(self, consumer_group: str, message_id: str) -> None:
        """XACK tasks:vault_ops."""
        await self._redis.xack(VAULT_OPS_STREAM, consumer_group, message_id)

    async def ensure_consumer_group(self) -> None:
        try:
            await self._redis.xgroup_create(
                VAULT_OPS_STREAM, CONSUMER_GROUP, id="0", mkstream=True
            )
        except redis.exceptions.ResponseError as exc:
            if "BUSYGROUP" not in str(exc):
                raise

    async def acquire_lock(self, path: str, ttl_s: int = 5) -> bool:
        """SET NX EX to prevent concurrent writes to the same path."""
        lock_key = f"vault:lock:{path}"
        return bool(await self._redis.set(lock_key, "1", ex=ttl_s, nx=True))

    async def release_lock(self, path: str) -> None:
        lock_key = f"vault:lock:{path}"
        await self._redis.delete(lock_key)

    async def close(self) -> None:
        await self._redis.aclose()
