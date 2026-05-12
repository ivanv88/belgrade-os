from __future__ import annotations
import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock
from belgrade_sdk.context import AppContext
from belgrade_sdk.gen import belgrade_os_pb2
from belgrade_sdk.exceptions import InferenceError, InferenceTimeoutError


def _make_ctx(redis_pool=None) -> AppContext:
    return AppContext(
        app_id="test_app",
        user_id="u1",
        tenant_id="t1",
        trace_id="tr-original",
        bridge_url="http://bridge:8081",
        redis_pool=redis_pool,
    )


async def test_inference_request_publishes_task_to_stream():
    mock_pool = AsyncMock()
    mock_pool.xadd = AsyncMock(return_value="1234-0")

    ctx = _make_ctx(redis_pool=mock_pool)
    result = await ctx.inference.request("plan my meals")

    assert "task_id" in result
    assert result["task_id"] != ""
    assert result["trace_id"] == "tr-original"

    mock_pool.xadd.assert_called_once()
    stream_name = mock_pool.xadd.call_args[0][0]
    payload = mock_pool.xadd.call_args[0][1]
    assert stream_name == "tasks:inbound"
    assert "data" in payload

    task = belgrade_os_pb2.Task()
    task.ParseFromString(payload["data"])
    assert task.task_id == result["task_id"]
    assert task.prompt == "plan my meals"
    assert task.app_id == "test_app"
    assert task.tenant_id == "t1"
    assert task.user_id == "u1"
    assert task.trace_id == "tr-original"
    assert task.execution_mode == belgrade_os_pb2.ExecutionMode.Value("UNTRUSTED")


async def test_inference_request_raises_without_redis():
    ctx = _make_ctx(redis_pool=None)
    with pytest.raises(RuntimeError, match="Redis pool not initialized"):
        await ctx.inference.request("test prompt")


async def test_inference_request_inherits_trace_id():
    mock_pool = AsyncMock()
    mock_pool.xadd = AsyncMock(return_value="1234-0")

    ctx = _make_ctx(redis_pool=mock_pool)
    ctx.trace_id = "my-trace-abc"
    result = await ctx.inference.request("hello")
    assert result["trace_id"] == "my-trace-abc"


async def test_inference_request_returns_new_task_id_each_call():
    mock_pool = AsyncMock()
    mock_pool.xadd = AsyncMock(return_value="1234-0")

    ctx = _make_ctx(redis_pool=mock_pool)
    r1 = await ctx.inference.request("first")
    r2 = await ctx.inference.request("second")
    assert r1["task_id"] != r2["task_id"]


async def test_inference_request_empty_tenant_when_none():
    mock_pool = AsyncMock()
    mock_pool.xadd = AsyncMock(return_value="1234-0")

    ctx = AppContext(
        app_id="myapp",
        user_id="u1",
        tenant_id=None,
        trace_id="tr1",
        bridge_url="http://bridge:8081",
        redis_pool=mock_pool,
    )
    await ctx.inference.request("test")

    payload = mock_pool.xadd.call_args[0][1]
    task = belgrade_os_pb2.Task()
    task.ParseFromString(payload["data"])
    assert task.tenant_id == ""


async def test_cancel_sets_redis_key_with_ttl():
    mock_pool = AsyncMock()
    mock_pool.set = AsyncMock()
    ctx = _make_ctx(redis_pool=mock_pool)
    await ctx.inference.cancel("task-abc")
    mock_pool.set.assert_awaited_once_with("tasks:cancel:task-abc", "1", ex=3600)


async def test_cancel_raises_without_redis():
    ctx = _make_ctx(redis_pool=None)
    with pytest.raises(RuntimeError, match="Redis pool not initialized"):
        await ctx.inference.cancel("task-abc")


def _make_pubsub_mock(messages: list) -> AsyncMock:
    """Build a mock pubsub that returns messages in order then None forever."""
    mock_pubsub = AsyncMock()
    mock_pubsub.subscribe = AsyncMock()
    mock_pubsub.unsubscribe = AsyncMock()
    side_effects = messages + [None] * 20
    mock_pubsub.get_message = AsyncMock(side_effect=side_effects)
    return mock_pubsub


async def test_stream_yields_events_until_done():
    from belgrade_sdk.gen import belgrade_os_pb2

    chunk = belgrade_os_pb2.ThoughtEvent(
        task_id="t1", type=belgrade_os_pb2.RESPONSE_CHUNK, content="hello"
    )
    done_evt = belgrade_os_pb2.ThoughtEvent(
        task_id="t1", type=belgrade_os_pb2.DONE
    )
    mock_pubsub = _make_pubsub_mock([
        {"type": "message", "data": chunk.SerializeToString()},
        {"type": "message", "data": done_evt.SerializeToString()},
    ])
    mock_pool = AsyncMock()
    mock_pool.pubsub = MagicMock(return_value=mock_pubsub)

    ctx = _make_ctx(redis_pool=mock_pool)
    events = []
    async for event in ctx.inference.stream("t1"):
        events.append(event)

    assert len(events) == 2
    assert events[0].type == belgrade_os_pb2.RESPONSE_CHUNK
    assert events[0].content == "hello"
    assert events[1].type == belgrade_os_pb2.DONE
    mock_pubsub.subscribe.assert_awaited_once_with("sse:t1")
    mock_pubsub.unsubscribe.assert_awaited_once_with("sse:t1")


async def test_stream_yields_error_event_and_terminates():
    from belgrade_sdk.gen import belgrade_os_pb2

    error_evt = belgrade_os_pb2.ThoughtEvent(
        task_id="t1", type=belgrade_os_pb2.ERROR, content="API failure"
    )
    mock_pubsub = _make_pubsub_mock([
        {"type": "message", "data": error_evt.SerializeToString()},
    ])
    mock_pool = AsyncMock()
    mock_pool.pubsub = MagicMock(return_value=mock_pubsub)

    ctx = _make_ctx(redis_pool=mock_pool)
    events = []
    async for event in ctx.inference.stream("t1"):
        events.append(event)

    assert len(events) == 1
    assert events[0].type == belgrade_os_pb2.ERROR
    assert events[0].content == "API failure"
    mock_pubsub.unsubscribe.assert_awaited_once_with("sse:t1")


async def test_stream_raises_timeout_when_no_terminal_event():
    mock_pubsub = _make_pubsub_mock([])   # always returns None
    mock_pool = AsyncMock()
    mock_pool.pubsub = MagicMock(return_value=mock_pubsub)

    ctx = _make_ctx(redis_pool=mock_pool)
    with pytest.raises(InferenceTimeoutError):
        async for _ in ctx.inference.stream("t1", timeout=0.001):
            pass
    mock_pubsub.unsubscribe.assert_awaited_once_with("sse:t1")


async def test_stream_raises_without_redis():
    ctx = _make_ctx(redis_pool=None)
    with pytest.raises(RuntimeError, match="Redis pool not initialized"):
        async for _ in ctx.inference.stream("t1"):
            pass
