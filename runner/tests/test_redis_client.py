from __future__ import annotations
from unittest.mock import AsyncMock, MagicMock, patch
import pytest
import redis.exceptions
from redis_client import RedisClient, TOOL_CALLS_STREAM, TOOL_RESULTS_STREAM, LEASE_KEY_PREFIX


def _make_client():
    with patch("redis_client.aioredis.from_url") as mock_from_url:
        mock_redis = MagicMock()
        mock_from_url.return_value = mock_redis
        client = RedisClient("redis://localhost:6379")
    return client, mock_redis


async def test_read_tool_call_returns_message():
    client, mock_redis = _make_client()
    mock_redis.xreadgroup = AsyncMock(return_value=[
        (b"tasks:tool_calls", [(b"1-0", {b"data": b"proto-bytes"})])
    ])
    result = await client.read_tool_call("g", "w1")
    assert result == ("1-0", b"proto-bytes")


async def test_read_tool_call_returns_none_on_empty():
    client, mock_redis = _make_client()
    mock_redis.xreadgroup = AsyncMock(return_value=[])
    result = await client.read_tool_call("g", "w1")
    assert result is None


async def test_ack_tool_call_calls_xack():
    client, mock_redis = _make_client()
    mock_redis.xack = AsyncMock()
    await client.ack_tool_call("g", "1-0")
    mock_redis.xack.assert_awaited_once_with(TOOL_CALLS_STREAM, "g", "1-0")


async def test_write_tool_result_includes_task_id_field():
    client, mock_redis = _make_client()
    mock_redis.xadd = AsyncMock()
    await client.write_tool_result("task-1", b"result-bytes")
    mock_redis.xadd.assert_awaited_once_with(
        TOOL_RESULTS_STREAM,
        {"data": b"result-bytes", "task_id": "task-1"},
    )


async def test_set_lease_calls_set_with_ex():
    client, mock_redis = _make_client()
    mock_redis.set = AsyncMock()
    await client.set_lease("worker-1", b"lease-bytes", ttl_s=60)
    mock_redis.set.assert_awaited_once_with(
        f"{LEASE_KEY_PREFIX}:worker-1", b"lease-bytes", ex=60
    )


async def test_delete_lease_calls_delete():
    client, mock_redis = _make_client()
    mock_redis.delete = AsyncMock()
    await client.delete_lease("worker-1")
    mock_redis.delete.assert_awaited_once_with(f"{LEASE_KEY_PREFIX}:worker-1")


async def test_ensure_consumer_group_creates_group():
    client, mock_redis = _make_client()
    mock_redis.xgroup_create = AsyncMock()
    await client.ensure_consumer_group()
    mock_redis.xgroup_create.assert_awaited_once_with(
        TOOL_CALLS_STREAM, "tool-runners", id="0", mkstream=True
    )


async def test_ensure_consumer_group_ignores_busygroup():
    client, mock_redis = _make_client()
    mock_redis.xgroup_create = AsyncMock(
        side_effect=redis.exceptions.ResponseError("BUSYGROUP Consumer Group name already exists")
    )
    await client.ensure_consumer_group()  # must not raise


async def test_read_tool_call_raises_on_missing_data_field():
    client, mock_redis = _make_client()
    mock_redis.xreadgroup = AsyncMock(return_value=[
        (b"tasks:tool_calls", [(b"1-0", {b"other": b"stuff"})])
    ])
    with pytest.raises(ValueError, match="missing required 'data' field"):
        await client.read_tool_call("g", "w1")


async def test_set_lease_rejects_non_positive_ttl():
    client, mock_redis = _make_client()
    with pytest.raises(ValueError, match="ttl_s must be a positive integer"):
        await client.set_lease("worker-1", b"bytes", ttl_s=0)
