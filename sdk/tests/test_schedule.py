from __future__ import annotations
import json
import pytest
from unittest.mock import AsyncMock
from belgrade_sdk.context import AppContext
from belgrade_sdk.gen import belgrade_os_pb2


def _make_ctx(redis_pool=None) -> AppContext:
    return AppContext(
        app_id="shopping",
        user_id="u1",
        tenant_id="t1",
        trace_id="tr-1",
        bridge_url="http://bridge:8081",
        redis_pool=redis_pool,
    )


@pytest.mark.asyncio
async def test_schedule_publishes_upsert_op():
    mock_pool = AsyncMock()
    ctx = _make_ctx(redis_pool=mock_pool)

    await ctx.schedule("daily-summary", "0 9 * * *", "shopping:summarize", params={"limit": 10})

    mock_pool.xadd.assert_called_once()
    stream, fields = mock_pool.xadd.call_args[0]
    assert stream == "tasks:schedule_ops"

    op = belgrade_os_pb2.ScheduleOp()
    op.ParseFromString(fields["data"])
    assert op.op == belgrade_os_pb2.ScheduleOp.UPSERT
    assert op.schedule_id == "shopping:u1:daily-summary"
    assert op.app_id == "shopping"
    assert op.user_id == "u1"
    assert op.tenant_id == "t1"
    assert op.cron == "0 9 * * *"
    assert op.tool_name == "shopping:summarize"
    assert json.loads(op.params_json) == {"limit": 10}
    assert op.trace_id == "tr-1"


@pytest.mark.asyncio
async def test_schedule_defaults_empty_params():
    mock_pool = AsyncMock()
    ctx = _make_ctx(redis_pool=mock_pool)

    await ctx.schedule("nightly", "0 2 * * *", "shopping:reindex")

    _, fields = mock_pool.xadd.call_args[0]
    op = belgrade_os_pb2.ScheduleOp()
    op.ParseFromString(fields["data"])
    assert json.loads(op.params_json) == {}


@pytest.mark.asyncio
async def test_schedule_raises_without_redis():
    ctx = _make_ctx(redis_pool=None)
    with pytest.raises(RuntimeError, match="Redis pool not initialized"):
        await ctx.schedule("daily-summary", "0 9 * * *", "shopping:summarize")


@pytest.mark.asyncio
async def test_unschedule_publishes_delete_op():
    mock_pool = AsyncMock()
    ctx = _make_ctx(redis_pool=mock_pool)

    await ctx.unschedule("daily-summary")

    mock_pool.xadd.assert_called_once()
    stream, fields = mock_pool.xadd.call_args[0]
    assert stream == "tasks:schedule_ops"

    op = belgrade_os_pb2.ScheduleOp()
    op.ParseFromString(fields["data"])
    assert op.op == belgrade_os_pb2.ScheduleOp.DELETE
    assert op.schedule_id == "shopping:u1:daily-summary"
    assert op.app_id == "shopping"
    assert op.user_id == "u1"


@pytest.mark.asyncio
async def test_unschedule_raises_without_redis():
    ctx = _make_ctx(redis_pool=None)
    with pytest.raises(RuntimeError, match="Redis pool not initialized"):
        await ctx.unschedule("daily-summary")
