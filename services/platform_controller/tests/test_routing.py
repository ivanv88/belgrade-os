import asyncio
import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
from ephemeral_runner import EphemeralRunner, OutputValidationError
import main as ctrl_main


def _build_call_proto(task_id="t1", call_id="c1", tool_name="shopping:add_item",
                      input_json="{}", user_id="user1", tenant_id="tenant1"):
    from gen import belgrade_os_pb2
    call = belgrade_os_pb2.ToolCall()
    call.call_id = call_id
    call.task_id = task_id
    call.tool_name = tool_name
    call.input_json = input_json
    call.user_id = user_id
    call.tenant_id = tenant_id
    call.execution_mode = belgrade_os_pb2.ExecutionMode.Value("UNTRUSTED")
    return call.SerializeToString()


@pytest.mark.asyncio
async def test_process_untrusted_call_success():
    runner = AsyncMock(spec=EphemeralRunner)
    runner.run.return_value = {"output": "done"}
    rdb = AsyncMock()

    await ctrl_main.process_untrusted_call(_build_call_proto(), runner, rdb)

    runner.run.assert_called_once()
    rdb.xadd.assert_called_once()
    # Verify the published ToolResult has success=True
    call_args = rdb.xadd.call_args
    stream, fields = call_args[0]
    assert stream == "tasks:tool_results"
    from gen import belgrade_os_pb2
    result = belgrade_os_pb2.ToolResult()
    result.ParseFromString(fields[b"data"])
    assert result.success is True
    assert result.call_id == "c1"
    assert result.task_id == "t1"


@pytest.mark.asyncio
async def test_process_untrusted_call_runtime_error():
    runner = AsyncMock(spec=EphemeralRunner)
    runner.run.side_effect = RuntimeError("container crashed")
    rdb = AsyncMock()

    await ctrl_main.process_untrusted_call(_build_call_proto(), runner, rdb)

    rdb.xadd.assert_called_once()
    call_args = rdb.xadd.call_args
    stream, fields = call_args[0]
    from gen import belgrade_os_pb2
    result = belgrade_os_pb2.ToolResult()
    result.ParseFromString(fields[b"data"])
    assert result.success is False
    assert "container crashed" in result.error


@pytest.mark.asyncio
async def test_process_untrusted_call_timeout():
    runner = AsyncMock(spec=EphemeralRunner)
    runner.run.side_effect = asyncio.TimeoutError()
    rdb = AsyncMock()

    await ctrl_main.process_untrusted_call(_build_call_proto(), runner, rdb)

    rdb.xadd.assert_called_once()
    call_args = rdb.xadd.call_args
    stream, fields = call_args[0]
    from gen import belgrade_os_pb2
    result = belgrade_os_pb2.ToolResult()
    result.ParseFromString(fields[b"data"])
    assert result.success is False
    assert "timeout" in result.error.lower()
