from __future__ import annotations
import time
import logging
from gen import belgrade_os_pb2
from redis_client import RedisClient
from bridge_client import BridgeClient

log = logging.getLogger(__name__)


async def process_tool_call(
    call: belgrade_os_pb2.ToolCall,
    redis: RedisClient,
    bridge: BridgeClient,
    worker_id: str,
    lease_ttl_s: int,
) -> None:
    now_ms = int(time.time() * 1000)
    lease = belgrade_os_pb2.WorkerLease(
        worker_id=worker_id,
        task_id=call.task_id,
        call_id=call.call_id,
        leased_at_ms=now_ms,
        expires_at_ms=now_ms + lease_ttl_s * 1000,
    )
    await redis.set_lease(worker_id, lease.SerializeToString(), lease_ttl_s)
    try:
        start_ms = int(time.time() * 1000)
        result = await bridge.execute(call)
        result.duration_ms = int(time.time() * 1000) - start_ms
        await redis.write_tool_result(call.task_id, result.SerializeToString())
    finally:
        await redis.delete_lease(worker_id)
