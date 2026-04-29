from __future__ import annotations
import asyncio
from unittest.mock import AsyncMock, patch
import pytest
from gen import belgrade_os_pb2


def _make_call_bytes() -> bytes:
    c = belgrade_os_pb2.ToolCall()
    c.call_id = "c1"
    c.task_id = "t1"
    c.tool_name = "shop:add"
    c.input_json = "{}"
    c.trace_id = "tr1"
    return c.SerializeToString()


async def test_consumer_loop_processes_and_acks():
    from main import _consumer_loop, CONSUMER_GROUP
    mock_redis = AsyncMock()
    mock_bridge = AsyncMock()
    read_count = [0]

    async def fake_read(group, worker_id):
        read_count[0] += 1
        if read_count[0] == 1:
            return "msg-1", _make_call_bytes()
        await asyncio.sleep(9999)

    mock_redis.read_tool_call.side_effect = fake_read

    with patch("main.process_tool_call", new_callable=AsyncMock) as mock_process:
        try:
            await asyncio.wait_for(
                _consumer_loop(mock_redis, mock_bridge, worker_id="w1", lease_ttl_s=60),
                timeout=1.0,
            )
        except asyncio.TimeoutError:
            pass

    mock_process.assert_awaited_once()
    call_arg = mock_process.await_args.args[0]
    assert call_arg.task_id == "t1"
    mock_redis.ack_tool_call.assert_awaited_once_with(CONSUMER_GROUP, "msg-1")


async def test_consumer_loop_acks_even_after_exception():
    from main import _consumer_loop, CONSUMER_GROUP
    mock_redis = AsyncMock()
    mock_bridge = AsyncMock()
    read_count = [0]

    async def fake_read(group, worker_id):
        read_count[0] += 1
        if read_count[0] == 1:
            return "msg-2", _make_call_bytes()
        await asyncio.sleep(9999)

    mock_redis.read_tool_call.side_effect = fake_read

    with patch("main.process_tool_call", new_callable=AsyncMock, side_effect=Exception("boom")):
        try:
            await asyncio.wait_for(
                _consumer_loop(mock_redis, mock_bridge, worker_id="w1", lease_ttl_s=60),
                timeout=1.0,
            )
        except asyncio.TimeoutError:
            pass

    mock_redis.ack_tool_call.assert_awaited_once_with(CONSUMER_GROUP, "msg-2")


async def test_consumer_loop_skips_none_without_acking():
    from main import _consumer_loop
    mock_redis = AsyncMock()
    mock_bridge = AsyncMock()
    read_count = [0]

    async def fake_read(group, worker_id):
        read_count[0] += 1
        if read_count[0] == 1:
            return None
        await asyncio.sleep(9999)

    mock_redis.read_tool_call.side_effect = fake_read

    with patch("main.process_tool_call", new_callable=AsyncMock):
        try:
            await asyncio.wait_for(
                _consumer_loop(mock_redis, mock_bridge, worker_id="w1", lease_ttl_s=60),
                timeout=1.0,
            )
        except asyncio.TimeoutError:
            pass

    mock_redis.ack_tool_call.assert_not_awaited()
