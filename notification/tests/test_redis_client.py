from __future__ import annotations
from unittest.mock import AsyncMock, MagicMock, patch
import pytest
import redis.exceptions
from redis_client import RedisClient, NOTIFICATIONS_STREAM, CONSUMER_GROUP


def _make_client():
    with patch("redis_client.aioredis.from_url") as mock_from_url:
        mock_redis = MagicMock()
        mock_from_url.return_value = mock_redis
        client = RedisClient("redis://localhost:6379")
    return client, mock_redis


async def test_read_notification_returns_message():
    client, mock_redis = _make_client()
    mock_redis.xreadgroup = AsyncMock(return_value=[
        (b"tasks:notifications", [(b"1-0", {b"data": b"proto-bytes"})])
    ])
    result = await client.read_notification("g", "w1")
    assert result == ("1-0", b"proto-bytes")


async def test_read_notification_returns_none_on_empty():
    client, mock_redis = _make_client()
    mock_redis.xreadgroup = AsyncMock(return_value=[])
    result = await client.read_notification("g", "w1")
    assert result is None


async def test_read_notification_raises_on_missing_data_field():
    client, mock_redis = _make_client()
    mock_redis.xreadgroup = AsyncMock(return_value=[
        (b"tasks:notifications", [(b"1-0", {b"other": b"stuff"})])
    ])
    with pytest.raises(ValueError, match="missing 'data' field"):
        await client.read_notification("g", "w1")


async def test_ack_notification_calls_xack():
    client, mock_redis = _make_client()
    mock_redis.xack = AsyncMock()
    await client.ack_notification("g", "1-0")
    mock_redis.xack.assert_awaited_once_with(NOTIFICATIONS_STREAM, "g", "1-0")


async def test_ensure_consumer_group_creates_group():
    client, mock_redis = _make_client()
    mock_redis.xgroup_create = AsyncMock()
    await client.ensure_consumer_group()
    mock_redis.xgroup_create.assert_awaited_once_with(
        NOTIFICATIONS_STREAM, CONSUMER_GROUP, id="0", mkstream=True
    )


async def test_ensure_consumer_group_ignores_busygroup():
    client, mock_redis = _make_client()
    mock_redis.xgroup_create = AsyncMock(
        side_effect=redis.exceptions.ResponseError("BUSYGROUP Consumer Group name already exists")
    )
    await client.ensure_consumer_group()  # must not raise
