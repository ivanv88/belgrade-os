from __future__ import annotations
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
import pytest
from gen import belgrade_os_pb2
from worker import process_notification
from drivers.base import NotificationDriver


def _make_req(title="Alert", body="Something happened"):
    req = belgrade_os_pb2.NotificationRequest()
    req.trace_id = "tr1"
    req.app_id = "shopping"
    req.user_id = "ivan"
    req.title = title
    req.body = body
    req.priority = belgrade_os_pb2.NOTIFICATION_NORMAL
    req.driver = "ntfy"
    return req


async def test_process_notification_calls_driver_send():
    mock_driver = AsyncMock(spec=NotificationDriver)
    req = _make_req()
    await process_notification(req, mock_driver)
    mock_driver.send.assert_awaited_once_with(req)


async def test_process_notification_logs_and_continues_on_driver_failure():
    mock_driver = AsyncMock(spec=NotificationDriver)
    mock_driver.send.side_effect = Exception("ntfy is down")
    req = _make_req()
    # Must not raise — delivery failures are logged and swallowed
    await process_notification(req, mock_driver)
    mock_driver.send.assert_awaited_once()


async def test_consumer_loop_processes_and_acks():
    from main import _consumer_loop, CONSUMER_GROUP
    mock_redis = AsyncMock()
    mock_driver = AsyncMock(spec=NotificationDriver)

    req = belgrade_os_pb2.NotificationRequest()
    req.title = "Test"
    req.priority = belgrade_os_pb2.NOTIFICATION_NORMAL
    proto_bytes = req.SerializeToString()

    read_count = [0]

    async def fake_read(group, worker_id):
        read_count[0] += 1
        if read_count[0] == 1:
            return "msg-1", proto_bytes
        await asyncio.sleep(9999)

    mock_redis.read_notification.side_effect = fake_read

    with patch("main.process_notification", new_callable=AsyncMock) as mock_process:
        try:
            await asyncio.wait_for(
                _consumer_loop(mock_redis, mock_driver, "w1"),
                timeout=1.0,
            )
        except asyncio.TimeoutError:
            pass

    mock_process.assert_awaited_once()
    mock_redis.ack_notification.assert_awaited_once_with(CONSUMER_GROUP, "msg-1")


async def test_consumer_loop_acks_even_after_exception():
    from main import _consumer_loop, CONSUMER_GROUP
    mock_redis = AsyncMock()
    mock_driver = AsyncMock(spec=NotificationDriver)

    req = belgrade_os_pb2.NotificationRequest()
    req.title = "Test"
    proto_bytes = req.SerializeToString()

    read_count = [0]

    async def fake_read(group, worker_id):
        read_count[0] += 1
        if read_count[0] == 1:
            return "msg-2", proto_bytes
        await asyncio.sleep(9999)

    mock_redis.read_notification.side_effect = fake_read

    with patch("main.process_notification", new_callable=AsyncMock, side_effect=Exception("boom")):
        try:
            await asyncio.wait_for(
                _consumer_loop(mock_redis, mock_driver, "w1"),
                timeout=1.0,
            )
        except asyncio.TimeoutError:
            pass

    mock_redis.ack_notification.assert_awaited_once_with(CONSUMER_GROUP, "msg-2")


async def test_consumer_loop_skips_none_without_acking():
    from main import _consumer_loop
    mock_redis = AsyncMock()
    mock_driver = AsyncMock(spec=NotificationDriver)

    read_count = [0]

    async def fake_read(group, worker_id):
        read_count[0] += 1
        if read_count[0] == 1:
            return None
        await asyncio.sleep(9999)

    mock_redis.read_notification.side_effect = fake_read

    with patch("main.process_notification", new_callable=AsyncMock):
        try:
            await asyncio.wait_for(
                _consumer_loop(mock_redis, mock_driver, "w1"),
                timeout=1.0,
            )
        except asyncio.TimeoutError:
            pass

    mock_redis.ack_notification.assert_not_awaited()
