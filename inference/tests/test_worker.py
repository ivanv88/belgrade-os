from __future__ import annotations

import json
from typing import Optional
from unittest.mock import AsyncMock, MagicMock

import pytest

from gen import belgrade_os_pb2
from worker import CONSUMER_GROUP, process_task


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_task(
    task_id: str = "t1",
    user_id: str = "u1",
    prompt: str = "hello",
    trace_id: str = "trace-1",
) -> belgrade_os_pb2.Task:
    return belgrade_os_pb2.Task(
        task_id=task_id,
        user_id=user_id,
        prompt=prompt,
        trace_id=trace_id,
    )


def _make_redis() -> AsyncMock:
    mock = AsyncMock()
    return mock


def _parse_published_events(mock_redis: AsyncMock) -> list[belgrade_os_pb2.ThoughtEvent]:
    published_bytes = [call.args[1] for call in mock_redis.publish_thought.await_args_list]
    events = [belgrade_os_pb2.ThoughtEvent() for _ in published_bytes]
    for e, b in zip(events, published_bytes):
        e.ParseFromString(b)
    return events


async def _provider_from_events(event_lists: list) -> MagicMock:
    """Build a mock provider where each generate() call yields from successive event_lists."""

    async def _gen_iter(events):
        for e in events:
            yield e

    provider = MagicMock()
    # generate is an async generator — we use side_effect with a list of async iterables
    call_count = [0]

    async def _generate(messages, tools=None):
        idx = call_count[0]
        call_count[0] += 1
        events = event_lists[idx]
        async for ev in _gen_iter(events):
            yield ev

    provider.generate = _generate
    return provider


# ---------------------------------------------------------------------------
# Test 1: response chunks + done
# ---------------------------------------------------------------------------


async def test_publishes_response_chunks_and_done():
    from providers.base import TextChunk, StreamDone

    task = _make_task()
    mock_redis = _make_redis()
    provider = await _provider_from_events([
        [TextChunk("Hello"), TextChunk(" world"), StreamDone("end_turn")],
    ])

    await process_task(task, mock_redis, provider, "worker-1")

    assert mock_redis.publish_thought.await_count == 3

    events = _parse_published_events(mock_redis)
    chunk_events = [e for e in events if e.type == belgrade_os_pb2.RESPONSE_CHUNK]
    done_events = [e for e in events if e.type == belgrade_os_pb2.DONE]

    assert len(chunk_events) == 2
    assert chunk_events[0].content == "Hello"
    assert chunk_events[1].content == " world"

    assert len(done_events) == 1
    assert done_events[0].task_id == "t1"
    assert done_events[0].trace_id == "trace-1"


# ---------------------------------------------------------------------------
# Test 2: all events carry task metadata
# ---------------------------------------------------------------------------


async def test_all_events_carry_task_metadata():
    from providers.base import TextChunk, StreamDone

    task = _make_task(task_id="task-42", user_id="user-7", trace_id="trace-99")
    mock_redis = _make_redis()
    provider = await _provider_from_events([
        [TextChunk("hi"), StreamDone("end_turn")],
    ])

    await process_task(task, mock_redis, provider, "worker-1")

    events = _parse_published_events(mock_redis)
    assert len(events) >= 1
    for e in events:
        assert e.task_id == "task-42"
        assert e.user_id == "user-7"
        assert e.trace_id == "trace-99"


# ---------------------------------------------------------------------------
# Test 3: tool use dispatches and continues
# ---------------------------------------------------------------------------


async def test_tool_use_dispatches_and_continues():
    from providers.base import TextChunk, StreamDone, ToolUse

    task = _make_task(task_id="t1")
    mock_redis = _make_redis()

    # Build serialized ToolResult for the mock to return
    tool_result = belgrade_os_pb2.ToolResult(
        call_id="call-abc",
        task_id="t1",
        success=True,
        output_json='["meeting"]',
    )
    tool_result_bytes = tool_result.SerializeToString()
    mock_redis.read_tool_result = AsyncMock(return_value=("msg-1", tool_result_bytes))

    provider = await _provider_from_events([
        # Round 1: tool_use
        [StreamDone("tool_use", [ToolUse("call-abc", "calendar:list_events", {"date": "2026-04-28"})])],
        # Round 2: text + end_turn
        [TextChunk("Done."), StreamDone("end_turn")],
    ])

    await process_task(task, mock_redis, provider, "worker-1")

    # push_tool_call called once
    assert mock_redis.push_tool_call.await_count == 1
    tc_bytes = mock_redis.push_tool_call.await_args.args[0]
    tc = belgrade_os_pb2.ToolCall()
    tc.ParseFromString(tc_bytes)
    assert tc.call_id == "call-abc"
    assert tc.tool_name == "calendar:list_events"
    assert json.loads(tc.input_json) == {"date": "2026-04-28"}

    # read_tool_result called with correct args
    mock_redis.read_tool_result.assert_awaited_once_with(
        CONSUMER_GROUP, "worker-1", "t1"
    )

    # ack_tool_result called with correct args
    mock_redis.ack_tool_result.assert_awaited_once_with(CONSUMER_GROUP, "msg-1")

    # At least one DONE event published
    events = _parse_published_events(mock_redis)
    done_events = [e for e in events if e.type == belgrade_os_pb2.DONE]
    assert len(done_events) == 1


# ---------------------------------------------------------------------------
# Test 4: exception publishes ERROR event
# ---------------------------------------------------------------------------


async def test_exception_publishes_error_event():
    task = _make_task()
    mock_redis = _make_redis()

    provider = MagicMock()
    provider.generate = MagicMock(side_effect=Exception("API timeout"))

    await process_task(task, mock_redis, provider, "worker-1")

    events = _parse_published_events(mock_redis)
    error_events = [e for e in events if e.type == belgrade_os_pb2.ERROR]
    assert len(error_events) == 1
    assert "API timeout" in error_events[0].content
