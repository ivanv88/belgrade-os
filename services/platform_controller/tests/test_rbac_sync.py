from __future__ import annotations
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from scheduler import PermissionSyncManager


@pytest.fixture
def mock_engine():
    engine = MagicMock()
    return engine


@pytest.fixture
def mock_redis():
    with patch("scheduler.RedisClient") as mock:
        client = mock.return_value
        client.sync_permissions = AsyncMock()
        yield client


async def test_permission_sync_all(mock_engine, mock_redis):
    # Mock DB result
    mock_result = MagicMock()
    mock_result.all.return_value = [
        ("ivan@example.com", "shopping", "web", "admin"),
        ("ivan@example.com", "shopping", "mobile", "admin"),
        ("wife@example.com", "shopping", "web", "viewer"),
    ]
    
    # Mock connection context manager
    conn = AsyncMock()
    conn.execute.return_value = mock_result
    
    # engine.connect() returns a context manager
    mock_engine.connect.return_value.__aenter__.return_value = conn

    manager = PermissionSyncManager(mock_engine, "redis://localhost")
    await manager.sync_all()

    # Check Redis sync calls
    assert mock_redis.sync_permissions.await_count == 2
    
    # Manually check calls since assert_any_awaited_with is not available in Python 3.9
    calls = [call.args for call in mock_redis.sync_permissions.await_args_list]
    
    ivan_call = next(c for c in calls if c[0] == "ivan@example.com")
    assert ivan_call[1] == {
        "shopping:web": "admin",
        "shopping:mobile": "admin"
    }
    
    wife_call = next(c for c in calls if c[0] == "wife@example.com")
    assert wife_call[1] == {
        "shopping:web": "viewer"
    }
