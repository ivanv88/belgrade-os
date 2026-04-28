from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import redis.exceptions

from redis_client import (
    INBOUND_STREAM,
    TOOL_CALLS_STREAM,
    TOOL_RESULTS_STREAM,
    RedisClient,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_client() -> tuple[RedisClient, MagicMock]:
    """Return a RedisClient whose internal redis connection is fully mocked."""
    with patch("redis_client.aioredis.from_url") as mock_from_url:
        mock_redis = MagicMock()
        mock_from_url.return_value = mock_redis
        client = RedisClient("redis://localhost:6379")
    return client, mock_redis


# ---------------------------------------------------------------------------
# read_task
# ---------------------------------------------------------------------------

async def test_read_task_returns_message():
    client, mock_redis = _make_client()
    msg_id = b"1234-0"
    proto = b"\x08\x01"
    mock_redis.xreadgroup = AsyncMock(
        return_value=[(INBOUND_STREAM, [(msg_id, {b"data": proto})])]
    )

    result = await client.read_task("g", "w-1")

    assert result == ("1234-0", proto)
    mock_redis.xreadgroup.assert_awaited_once_with(
        groupname="g",
        consumername="w-1",
        streams={INBOUND_STREAM: ">"},
        count=1,
        block=2000,
    )


async def test_read_task_returns_none_on_empty():
    client, mock_redis = _make_client()
    mock_redis.xreadgroup = AsyncMock(return_value=[])

    result = await client.read_task("g", "w-1")

    assert result is None


# ---------------------------------------------------------------------------
# ack_task
# ---------------------------------------------------------------------------

async def test_ack_task_calls_xack():
    client, mock_redis = _make_client()
    mock_redis.xack = AsyncMock()

    await client.ack_task("1234-0")

    mock_redis.xack.assert_awaited_once_with(INBOUND_STREAM, "inference", "1234-0")


# ---------------------------------------------------------------------------
# publish_thought
# ---------------------------------------------------------------------------

async def test_publish_thought_calls_publish():
    client, mock_redis = _make_client()
    mock_redis.publish = AsyncMock()
    proto = b"\x08\x02"

    await client.publish_thought("task-abc", proto)

    mock_redis.publish.assert_awaited_once_with("sse:task-abc", proto)


# ---------------------------------------------------------------------------
# push_tool_call
# ---------------------------------------------------------------------------

async def test_push_tool_call_calls_xadd():
    client, mock_redis = _make_client()
    mock_redis.xadd = AsyncMock()
    proto = b"\x08\x03"

    await client.push_tool_call(proto)

    mock_redis.xadd.assert_awaited_once_with(TOOL_CALLS_STREAM, {"data": proto})


# ---------------------------------------------------------------------------
# read_tool_result
# ---------------------------------------------------------------------------

async def test_read_tool_result_returns_matching():
    client, mock_redis = _make_client()
    msg_id = b"5678-0"
    proto = b"\x08\x04"
    mock_redis.xreadgroup = AsyncMock(
        return_value=[
            (TOOL_RESULTS_STREAM, [(msg_id, {b"task_id": b"task-xyz", b"data": proto})])
        ]
    )

    result = await client.read_tool_result("g", "w-1", "task-xyz")

    assert result == ("5678-0", proto)


async def test_read_tool_result_skips_mismatched():
    client, mock_redis = _make_client()
    mock_redis.xack = AsyncMock()

    mismatch_id = b"1111-0"
    mismatch_proto = b"\x01"
    match_id = b"2222-0"
    match_proto = b"\x02"

    mock_redis.xreadgroup = AsyncMock(
        side_effect=[
            # First call — wrong task_id
            [(TOOL_RESULTS_STREAM, [(mismatch_id, {b"task_id": b"task-other", b"data": mismatch_proto})])],
            # Second call — correct task_id
            [(TOOL_RESULTS_STREAM, [(match_id, {b"task_id": b"task-xyz", b"data": match_proto})])],
        ]
    )

    result = await client.read_tool_result("g", "w-1", "task-xyz")

    assert result == ("2222-0", match_proto)
    # The mismatched message must have been ACK'd
    mock_redis.xack.assert_awaited_once_with(TOOL_RESULTS_STREAM, "g", "1111-0")


async def test_read_tool_result_returns_none_when_empty():
    client, mock_redis = _make_client()
    mock_redis.xreadgroup = AsyncMock(return_value=[])

    result = await client.read_tool_result("g", "w-1", "task-xyz")

    assert result is None


# ---------------------------------------------------------------------------
# ensure_consumer_groups
# ---------------------------------------------------------------------------

async def test_ensure_consumer_groups_creates_groups():
    client, mock_redis = _make_client()
    mock_redis.xgroup_create = AsyncMock()

    await client.ensure_consumer_groups()

    assert mock_redis.xgroup_create.await_count == 2
    calls = {call.args[0] for call in mock_redis.xgroup_create.await_args_list}
    assert INBOUND_STREAM in calls
    assert TOOL_RESULTS_STREAM in calls


async def test_ensure_consumer_groups_ignores_busygroup():
    client, mock_redis = _make_client()
    mock_redis.xgroup_create = AsyncMock(
        side_effect=redis.exceptions.ResponseError("BUSYGROUP Consumer Group name already exists")
    )

    # Should not raise
    await client.ensure_consumer_groups()
