from __future__ import annotations
import json
import pytest
from unittest.mock import MagicMock
from belgrade_sdk.app import BelgradeApp
from belgrade_sdk.context import AppContext


def _make_app_with_pools() -> BelgradeApp:
    """Return a BelgradeApp with mock engine and redis pool already set (simulating post-startup state)."""
    bapp = BelgradeApp("test_app")
    bapp._db_engine = MagicMock(name="mock_engine")
    bapp._redis_pool = MagicMock(name="mock_pool")
    return bapp


def test_execute_passes_engine_and_pool_to_appcontext():
    """AppContext constructed inside /execute must receive db_engine and redis_pool, not URL strings."""
    from fastapi.testclient import TestClient

    bapp = _make_app_with_pools()
    captured: list[AppContext] = []

    @bapp.tool("ping", "Ping tool")
    async def ping(ctx: AppContext, msg: str) -> dict:
        captured.append(ctx)
        return {"pong": msg}

    client = TestClient(bapp.app, raise_server_exceptions=True)
    resp = client.post("/execute", json={
        "tool_name": "test_app:ping",
        "input_json": json.dumps({"msg": "hello"}),
        "user_id": "u1",
        "tenant_id": "t1",
        "trace_id": "tr1",
    })

    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is True, f"Tool failed: {data.get('error')}"
    assert len(captured) == 1

    ctx = captured[0]
    assert ctx._db_engine is bapp._db_engine
    assert ctx._redis_pool is bapp._redis_pool
    assert not hasattr(ctx, "db_url"), "db_url must not be an attribute on AppContext"
    assert not hasattr(ctx, "redis_url"), "redis_url must not be an attribute on AppContext"


def test_events_passes_engine_and_pool_to_appcontext():
    """AppContext constructed inside /events must receive db_engine and redis_pool."""
    from fastapi.testclient import TestClient

    bapp = _make_app_with_pools()
    captured: list[AppContext] = []

    @bapp.on_event("test.topic")
    async def handle(ctx: AppContext, payload: dict) -> None:
        captured.append(ctx)

    client = TestClient(bapp.app, raise_server_exceptions=True)
    resp = client.post("/events", json={
        "topic": "test.topic",
        "payload": {"x": 1},
        "tenant_id": "t1",
        "trace_id": "tr2",
    })

    assert resp.status_code == 200
    assert len(captured) == 1
    ctx = captured[0]
    assert ctx._db_engine is bapp._db_engine
    assert ctx._redis_pool is bapp._redis_pool


def test_shutdown_safe_when_pools_are_none():
    """on_shutdown must not raise when _db_engine and _redis_pool are None (never initialized)."""
    import asyncio
    bapp = BelgradeApp("test_app")
    # _db_engine and _redis_pool must exist as attributes (even if None)
    assert hasattr(bapp, "_db_engine"), "_db_engine must be defined in __init__"
    assert hasattr(bapp, "_redis_pool"), "_redis_pool must be defined in __init__"

    async def simulate_shutdown():
        if bapp._db_engine:
            await bapp._db_engine.dispose()
        if bapp._redis_pool:
            await bapp._redis_pool.aclose()

    asyncio.run(simulate_shutdown())  # must not raise


def test_run_listens_on_port_injected_by_platform(monkeypatch):
    """The Platform Controller assigns the port; run() must listen where the bridge will call back."""
    from unittest.mock import patch
    monkeypatch.setenv("BEG_OS_PORT", "9123")
    bapp = BelgradeApp("test_app")
    with patch("belgrade_sdk.app.uvicorn.run") as mock_run:
        bapp.run()
    assert mock_run.call_args.kwargs["port"] == 9123


def test_run_uses_explicit_port_when_platform_port_absent(monkeypatch):
    """Standalone runs (no Platform Controller) keep working with an explicit port."""
    from unittest.mock import patch
    monkeypatch.delenv("BEG_OS_PORT", raising=False)
    bapp = BelgradeApp("test_app")
    with patch("belgrade_sdk.app.uvicorn.run") as mock_run:
        bapp.run(port=9555)
    assert mock_run.call_args.kwargs["port"] == 9555


def test_run_listens_on_loopback_by_default(monkeypatch):
    """Apps are called by the bridge on localhost; their /execute has no auth of its own."""
    from unittest.mock import patch
    monkeypatch.delenv("BEG_OS_HOST", raising=False)
    bapp = BelgradeApp("test_app")
    with patch("belgrade_sdk.app.uvicorn.run") as mock_run:
        bapp.run()
    assert mock_run.call_args.kwargs["host"] == "127.0.0.1"


def test_run_host_can_be_overridden_by_platform(monkeypatch):
    from unittest.mock import patch
    monkeypatch.setenv("BEG_OS_HOST", "10.0.0.5")
    bapp = BelgradeApp("test_app")
    with patch("belgrade_sdk.app.uvicorn.run") as mock_run:
        bapp.run()
    assert mock_run.call_args.kwargs["host"] == "10.0.0.5"
