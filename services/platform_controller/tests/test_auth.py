from __future__ import annotations
import os
import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch, AsyncMock, MagicMock


def _get_client(token: str = "test-secret-token"):
    """Import app fresh with a test token set in env."""
    import asyncio
    import importlib
    # Ensure a fresh event loop exists before reloading main, because
    # PermissionSyncManager.__init__ calls aioredis.from_url() at module level
    # which requires a running event loop on Python 3.9.
    try:
        loop = asyncio.get_event_loop()
        if loop.is_closed():
            raise RuntimeError("closed")
    except RuntimeError:
        asyncio.set_event_loop(asyncio.new_event_loop())

    with patch.dict(os.environ, {"CONTROLLER_API_TOKEN": token}):
        import main as m
        importlib.reload(m)
        return TestClient(m.app), m


def test_reload_without_token_returns_401():
    client, _ = _get_client("test-secret")
    resp = client.post("/apps/reload", json={"app_id": "shopping"})
    assert resp.status_code in (401, 403), f"Expected 401/403, got {resp.status_code}"


def test_reload_with_wrong_token_returns_403():
    client, _ = _get_client("test-secret")
    resp = client.post(
        "/apps/reload",
        json={"app_id": "shopping"},
        headers={"Authorization": "Bearer wrong-token"},
    )
    assert resp.status_code == 403


def test_reload_with_correct_token_is_accepted():
    client, m = _get_client("test-secret")
    with patch.object(m.app_supervisor, "start_app", new_callable=AsyncMock):
        resp = client.post(
            "/apps/reload",
            json={"app_id": "shopping"},
            headers={"Authorization": "Bearer test-secret"},
        )
    assert resp.status_code == 200


def test_reload_rejects_path_traversal_app_id():
    client, m = _get_client("test-secret")
    resp = client.post(
        "/apps/reload",
        json={"app_id": "../etc/something"},
        headers={"Authorization": "Bearer test-secret"},
    )
    assert resp.status_code == 400


def test_reload_rejects_empty_app_id():
    client, m = _get_client("test-secret")
    resp = client.post(
        "/apps/reload",
        json={"app_id": ""},
        headers={"Authorization": "Bearer test-secret"},
    )
    assert resp.status_code == 400


def test_reload_accepts_valid_app_id():
    client, m = _get_client("test-secret")
    with patch.object(m.app_supervisor, "start_app", new_callable=AsyncMock):
        resp = client.post(
            "/apps/reload",
            json={"app_id": "my-app_01"},
            headers={"Authorization": "Bearer test-secret"},
        )
    assert resp.status_code == 200


# --- Schedule endpoint auth ---

def test_create_schedule_without_token_returns_401():
    client, _ = _get_client("test-secret")
    resp = client.post("/schedules", json={
        "id": "s1", "app_id": "shopping", "user_id": "u1", "tenant_id": "t1",
        "cron": "0 9 * * *", "tool_name": "shopping:remind", "params": {},
    })
    assert resp.status_code in (401, 403)


def test_create_schedule_with_wrong_token_returns_403():
    client, _ = _get_client("test-secret")
    resp = client.post(
        "/schedules",
        json={
            "id": "s1", "app_id": "shopping", "user_id": "u1", "tenant_id": "t1",
            "cron": "0 9 * * *", "tool_name": "shopping:remind", "params": {},
        },
        headers={"Authorization": "Bearer wrong-token"},
    )
    assert resp.status_code == 403


def test_delete_schedule_without_token_returns_401():
    client, _ = _get_client("test-secret")
    resp = client.delete("/schedules/s1")
    assert resp.status_code in (401, 403)


def test_list_schedules_without_token_returns_401():
    client, _ = _get_client("test-secret")
    resp = client.get("/schedules")
    assert resp.status_code in (401, 403)
