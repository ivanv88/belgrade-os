from __future__ import annotations
from gen import belgrade_os_pb2


def test_task_message() -> None:
    task = belgrade_os_pb2.Task()
    task.task_id = "task-001"
    task.user_id = "user-1"
    task.prompt = "What's for dinner?"
    task.created_at_ms = 1_700_000_000_000
    task.trace_id = "trace-abc"
    assert task.task_id == "task-001"
    assert task.trace_id == "trace-abc"


def test_tool_call_message() -> None:
    call = belgrade_os_pb2.ToolCall()
    call.call_id = "call-001"
    call.task_id = "task-001"
    call.tool_name = "shopping:add_item"
    call.input_json = '{"item": "milk"}'
    call.trace_id = "trace-abc"
    assert call.tool_name == "shopping:add_item"
    assert call.trace_id == "trace-abc"


def test_tool_result_failure() -> None:
    result = belgrade_os_pb2.ToolResult()
    result.call_id = "call-001"
    result.task_id = "task-001"
    result.success = False
    result.error = "app crashed"
    result.duration_ms = 42
    assert not result.success
    assert result.error == "app crashed"
    assert result.duration_ms == 42


def test_thought_event_type() -> None:
    ev = belgrade_os_pb2.ThoughtEvent()
    ev.task_id = "task-001"
    ev.user_id = "user-1"
    ev.type = belgrade_os_pb2.RESPONSE_CHUNK
    ev.content = "pasta is great"
    ev.trace_id = "trace-abc"
    assert ev.type == belgrade_os_pb2.RESPONSE_CHUNK
    assert ev.trace_id == "trace-abc"


def test_thought_event_unspecified_default() -> None:
    ev = belgrade_os_pb2.ThoughtEvent()
    assert ev.type == belgrade_os_pb2.THOUGHT_EVENT_TYPE_UNSPECIFIED


def test_worker_lease_fields() -> None:
    lease = belgrade_os_pb2.WorkerLease()
    lease.worker_id = "worker-1"
    lease.task_id = "task-001"
    lease.call_id = "call-001"
    lease.leased_at_ms = 1_700_000_000_000
    lease.expires_at_ms = 1_700_000_060_000
    assert lease.expires_at_ms > lease.leased_at_ms
