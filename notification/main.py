from __future__ import annotations
import asyncio
import logging
import redis.exceptions
from gen import belgrade_os_pb2
from config import load_config
from redis_client import RedisClient, CONSUMER_GROUP
from worker import process_notification
from drivers import get_driver

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)


async def _consumer_loop(redis_client: RedisClient, driver, worker_id: str) -> None:
    while True:
        try:
            result = await redis_client.read_notification(CONSUMER_GROUP, worker_id)
            if result is None:
                continue
            msg_id, data = result
            try:
                req = belgrade_os_pb2.NotificationRequest()
                req.ParseFromString(data)
                success = await process_notification(req, driver)
                if not success:
                    # DLO: Move to failed stream
                    await redis_client.move_to_failed(msg_id, data)
            except Exception:
                log.exception("unhandled error processing notification msg=%s", msg_id)
                await redis_client.move_to_failed(msg_id, data)
            finally:
                await redis_client.ack_notification(CONSUMER_GROUP, msg_id)
        except (redis.exceptions.ConnectionError, redis.exceptions.TimeoutError):
            log.warning("Redis connection lost. Retrying in 5s...")
            await asyncio.sleep(5)
        except Exception:
            log.exception("Permanent error in consumer loop. Backing off for 10s...")
            await asyncio.sleep(10)


async def main() -> None:
    cfg = load_config()
    redis_client = RedisClient(cfg.redis_url)
    driver = get_driver(cfg)

    await redis_client.ensure_consumer_group()
    worker_id = cfg.effective_worker_id
    log.info(
        "notification service starting worker_id=%s driver=%s",
        worker_id, cfg.notification_driver,
    )

    try:
        await _consumer_loop(redis_client, driver, worker_id)
    finally:
        await redis_client.close()
        if hasattr(driver, "close"):
            await driver.close()


if __name__ == "__main__":
    asyncio.run(main())
