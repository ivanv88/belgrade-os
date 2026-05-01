from __future__ import annotations
import logging
from gen import belgrade_os_pb2
from drivers.base import NotificationDriver

log = logging.getLogger(__name__)


async def process_notification(
    req: belgrade_os_pb2.NotificationRequest,
    driver: NotificationDriver,
) -> bool:
    """Dispatch a single notification. Returns True if successful, False otherwise."""
    try:
        await driver.send(req)
        log.info(
            "notification sent app_id=%s title=%r driver=%s trace_id=%s",
            req.app_id, req.title, req.driver, req.trace_id,
        )
        return True
    except Exception:
        log.exception(
            "failed to deliver notification app_id=%s title=%r driver=%s",
            req.app_id, req.title, req.driver,
        )
        return False
