from __future__ import annotations
import asyncio
import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch, call
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
import main as ctrl_main
from scheduler import ScheduleEntry


def _build_schedule_op(
    op_type="UPSERT",
    schedule_id="shopping:u1:daily-summary",
    app_id="shopping",
    user_id="u1",
    tenant_id="t1",
    cron="0 9 * * *",
    tool_name="shopping:summarize",
    params_json='{"limit": 10}',
):
    from gen import belgrade_os_pb2
    op = belgrade_os_pb2.ScheduleOp()
    op.op = getattr(belgrade_os_pb2.ScheduleOp, op_type)
    op.schedule_id = schedule_id
    op.app_id = app_id
    op.user_id = user_id
    op.tenant_id = tenant_id
    op.cron = cron
    op.tool_name = tool_name
    op.params_json = params_json
    op.trace_id = "tr-1"
    return op.SerializeToString()


@pytest.mark.asyncio
async def test_process_schedule_op_upsert_calls_add_schedule():
    mock_scheduler = AsyncMock()
    mock_session_cm = AsyncMock()
    mock_session = AsyncMock()
    mock_session_cm.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session_cm.__aexit__ = AsyncMock(return_value=False)

    with patch.object(ctrl_main, "scheduler_manager", mock_scheduler), \
         patch.object(ctrl_main, "SessionLocal", return_value=mock_session_cm):
        await ctrl_main._process_schedule_op(_build_schedule_op("UPSERT"))

    mock_scheduler.add_schedule.assert_called_once()
    entry: ScheduleEntry = mock_scheduler.add_schedule.call_args[0][0]
    assert entry.id == "shopping:u1:daily-summary"
    assert entry.app_id == "shopping"
    assert entry.user_id == "u1"
    assert entry.cron == "0 9 * * *"
    assert entry.tool_name == "shopping:summarize"
    assert entry.params == {"limit": 10}


@pytest.mark.asyncio
async def test_process_schedule_op_delete_calls_remove_schedule():
    mock_scheduler = MagicMock()
    mock_session_cm = AsyncMock()
    mock_session = AsyncMock()
    mock_session_cm.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session_cm.__aexit__ = AsyncMock(return_value=False)

    with patch.object(ctrl_main, "scheduler_manager", mock_scheduler), \
         patch.object(ctrl_main, "SessionLocal", return_value=mock_session_cm):
        await ctrl_main._process_schedule_op(_build_schedule_op("DELETE"))

    mock_scheduler.remove_schedule.assert_called_once_with("shopping:u1:daily-summary")


@pytest.mark.asyncio
async def test_process_schedule_op_invalid_app_id_discards():
    mock_scheduler = MagicMock()

    with patch.object(ctrl_main, "scheduler_manager", mock_scheduler):
        await ctrl_main._process_schedule_op(
            _build_schedule_op("UPSERT", app_id="../../evil")
        )

    mock_scheduler.add_schedule.assert_not_called()


@pytest.mark.asyncio
async def test_process_schedule_op_malformed_proto_raises():
    with pytest.raises(Exception):
        await ctrl_main._process_schedule_op(b"not a proto")
