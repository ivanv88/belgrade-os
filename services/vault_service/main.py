from __future__ import annotations
import asyncio
import logging
import os
import socket
from pathlib import Path
from gen import belgrade_os_pb2
from redis_client import RedisClient, CONSUMER_GROUP
from worker import process_vault_op
from config import load_config as _load_config

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

_cfg = _load_config()
VAULT_ROOT = Path(_cfg.vault_path)
REDIS_URL  = _cfg.redis_url
WORKER_ID  = _cfg.effective_worker_id

async def _consumer_loop(redis: RedisClient) -> None:
    while True:
        result = await redis.read_vault_op(CONSUMER_GROUP, WORKER_ID)
        if result is None:
            continue
        msg_id, data = result
        try:
            op = belgrade_os_pb2.VaultOperation()
            op.ParseFromString(data)
            await process_vault_op(op, VAULT_ROOT, redis)
        except Exception:
            log.exception("unhandled error processing vault op msg=%s", msg_id)
        finally:
            await redis.ack_vault_op(CONSUMER_GROUP, msg_id)

async def main() -> None:
    redis = RedisClient(REDIS_URL)
    await redis.ensure_consumer_group()
    
    log.info("vault service starting worker_id=%s vault_root=%s", WORKER_ID, VAULT_ROOT)
    VAULT_ROOT.mkdir(parents=True, exist_ok=True)

    try:
        await _consumer_loop(redis)
    finally:
        await redis.close()

if __name__ == "__main__":
    asyncio.run(main())
