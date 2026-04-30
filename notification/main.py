from __future__ import annotations
import asyncio
import logging
from gen import belgrade_os_pb2
from config import load_config
from redis_client import RedisClient, CONSUMER_GROUP
from worker import process_notification
from drivers import get_driver

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)


async def _consumer_loop(redis: RedisClient, driver, worker_id: str) -> None:
    while True:
        result = await redis.read_notification(CONSUMER_GROUP, worker_id)
        if result is None:
            continue
        msg_id, data = result
        try:
            req = belgrade_os_pb2.NotificationRequest()
            req.ParseFromString(data)
            await process_notification(req, driver)
        except Exception:
            log.exception("unhandled error processing notification msg=%s", msg_id)
        finally:
            await redis.ack_notification(CONSUMER_GROUP, msg_id)


async def main() -> None:
    cfg = load_config()
    redis = RedisClient(cfg.redis_url)
    driver = get_driver(cfg)

    await redis.ensure_consumer_group()
    worker_id = cfg.effective_worker_id
    log.info(
        "notification service starting worker_id=%s driver=%s",
        worker_id, cfg.notification_driver,
    )

    try:
        await _consumer_loop(redis, driver, worker_id)
    finally:
        await redis.close()


if __name__ == "__main__":
    asyncio.run(main())
