from __future__ import annotations
from unittest.mock import AsyncMock
import pytest
from gen import belgrade_os_pb2
from worker import process_tool_call


def _make_call(call_id="c1", task_id="t1") -> belgrade_os_pb2.ToolCall:
    c = belgrade_os_pb2.ToolCall()
    c.call_id = call_id
    c.task_id = task_id
    c.tool_name = "shop:add"
    c.input_json = "{}"
    c.trace_id = "tr1"
    return c


def _make_bridge_result(call_id="c1", task_id="t1", success=True) -> belgrade_os_pb2.ToolResult:
    r = belgrade_os_pb2.ToolResult()
    r.call_id = call_id
    r.task_id = task_id
    r.success = success
    r.output_json = '{"ok":true}'
    return r


async def test_sets_lease_with_correct_fields():
    mock_redis = AsyncMock()
    mock_bridge = AsyncMock()
    mock_bridge.execute.return_value = _make_bridge_result()

    await process_tool_call(_make_call(), mock_redis, mock_bridge, worker_id="w1", lease_ttl_s=60)

    mock_redis.set_lease.assert_awaited_once()
    key_arg = mock_redis.set_lease.await_args.args[0]
    lease_bytes = mock_redis.set_lease.await_args.args[1]
    ttl = mock_redis.set_lease.await_args.args[2]
    lease = belgrade_os_pb2.WorkerLease()
    lease.ParseFromString(lease_bytes)
    assert key_arg == "w1"
    assert lease.worker_id == "w1"
    assert lease.task_id == "t1"
    assert lease.call_id == "c1"
    assert lease.expires_at_ms > lease.leased_at_ms
    assert ttl == 60


async def test_writes_result_with_task_id_and_duration():
    mock_redis = AsyncMock()
    mock_bridge = AsyncMock()
    mock_bridge.execute.return_value = _make_bridge_result(task_id="t1")

    await process_tool_call(_make_call(task_id="t1"), mock_redis, mock_bridge, worker_id="w1", lease_ttl_s=60)

    mock_redis.write_tool_result.assert_awaited_once()
    task_id_arg = mock_redis.write_tool_result.await_args.args[0]
    result_bytes = mock_redis.write_tool_result.await_args.args[1]
    assert task_id_arg == "t1"
    result = belgrade_os_pb2.ToolResult()
    result.ParseFromString(result_bytes)
    assert result.success is True
    assert result.duration_ms >= 0


async def test_deletes_lease_after_success():
    mock_redis = AsyncMock()
    mock_bridge = AsyncMock()
    mock_bridge.execute.return_value = _make_bridge_result()

    await process_tool_call(_make_call(), mock_redis, mock_bridge, worker_id="w1", lease_ttl_s=60)

    mock_redis.delete_lease.assert_awaited_once_with("w1")


async def test_deletes_lease_even_on_bridge_error():
    mock_redis = AsyncMock()
    mock_bridge = AsyncMock()
    error_result = belgrade_os_pb2.ToolResult()
    error_result.call_id = "c1"
    error_result.task_id = "t1"
    error_result.success = False
    error_result.error = "bridge error: timeout"
    mock_bridge.execute.return_value = error_result

    await process_tool_call(_make_call(), mock_redis, mock_bridge, worker_id="w1", lease_ttl_s=60)

    mock_redis.delete_lease.assert_awaited_once_with("w1")
    mock_redis.write_tool_result.assert_awaited_once()


async def test_result_written_before_lease_deleted():
    """write_tool_result must precede delete_lease so the inference controller
    can never read an ACK with no result in the stream."""
    call_order = []
    mock_redis = AsyncMock()
    mock_redis.set_lease = AsyncMock(side_effect=lambda *a, **kw: call_order.append("set"))
    mock_redis.write_tool_result = AsyncMock(side_effect=lambda *a, **kw: call_order.append("write"))
    mock_redis.delete_lease = AsyncMock(side_effect=lambda *a, **kw: call_order.append("delete"))
    mock_bridge = AsyncMock()
    mock_bridge.execute.return_value = _make_bridge_result()

    await process_tool_call(_make_call(), mock_redis, mock_bridge, worker_id="w1", lease_ttl_s=60)

    assert call_order == ["set", "write", "delete"]


async def test_deletes_lease_on_bridge_exception():
    mock_redis = AsyncMock()
    mock_bridge = AsyncMock()
    mock_bridge.execute.side_effect = RuntimeError("unexpected")

    with pytest.raises(RuntimeError):
        await process_tool_call(_make_call(), mock_redis, mock_bridge, worker_id="w1", lease_ttl_s=60)

    mock_redis.delete_lease.assert_awaited_once_with("w1")
    mock_redis.write_tool_result.assert_not_awaited()
