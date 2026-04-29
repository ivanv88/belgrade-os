from __future__ import annotations
import asyncio
import logging
from gen import belgrade_os_pb2
from config import Config, load_config
from redis_client import RedisClient
from bridge_client import BridgeClient
from worker import process_tool_call

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

CONSUMER_GROUP = "tool-runners"


async def _consumer_loop(
    redis: RedisClient,
    bridge: BridgeClient,
    worker_id: str,
    lease_ttl_s: int,
) -> None:
    while True:
        result = await redis.read_tool_call(CONSUMER_GROUP, worker_id)
        if result is None:
            continue
        msg_id, call_bytes = result
        call = belgrade_os_pb2.ToolCall()
        call.ParseFromString(call_bytes)
        try:
            await process_tool_call(call, redis, bridge, worker_id, lease_ttl_s)
        except Exception:
            log.exception("unhandled error for call %s task %s", call.call_id, call.task_id)
        finally:
            await redis.ack_tool_call(CONSUMER_GROUP, msg_id)


async def main() -> None:
    cfg = load_config()
    redis = RedisClient(cfg.redis_url)
    bridge = BridgeClient(base_url=cfg.bridge_url, timeout_s=cfg.tool_timeout_s)

    await redis.ensure_consumer_group()
    worker_id = cfg.effective_worker_id
    log.info("resource runner starting worker_id=%s bridge=%s", worker_id, cfg.bridge_url)

    try:
        await _consumer_loop(redis, bridge, worker_id, cfg.lease_ttl_s)
    finally:
        await bridge.close()
        await redis.close()


if __name__ == "__main__":
    asyncio.run(main())
