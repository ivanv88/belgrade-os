from __future__ import annotations
import redis.asyncio as aioredis

class RedisClient:
    def __init__(self, url: str) -> None:
        self._redis: aioredis.Redis = aioredis.from_url(url, decode_responses=False)

    async def sync_permissions(self, user_id: str, perms: dict[str, str]) -> None:
        """Replace all permissions for a user in Redis.
        Key: perms:{user_id}
        Fields: {app_id}:{bundle_id}
        Value: {role}
        """
        key = f"perms:{user_id}"
        async with self._redis.pipeline() as pipe:
            pipe.delete(key)
            if perms:
                pipe.hset(key, mapping=perms)
            await pipe.execute()

    async def close(self) -> None:
        await self._redis.aclose()
