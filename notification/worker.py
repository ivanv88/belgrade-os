from __future__ import annotations
import logging
from gen import belgrade_os_pb2
from drivers.base import NotificationDriver

log = logging.getLogger(__name__)


async def process_notification(
    req: belgrade_os_pb2.NotificationRequest,
    driver: NotificationDriver,
) -> None:
    """Dispatch a single notification. Logs on failure — never raises.

    Delivery failures are swallowed here so one bad notification cannot
    stall the consumer loop or lose other messages in the stream.
    """
    try:
        await driver.send(req)
        log.info(
            "notification sent app_id=%s title=%r driver=%s trace_id=%s",
            req.app_id, req.title, req.driver, req.trace_id,
        )
    except Exception:
        log.exception(
            "failed to deliver notification app_id=%s title=%r driver=%s",
            req.app_id, req.title, req.driver,
        )
