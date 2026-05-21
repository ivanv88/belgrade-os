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
    assert entry.tenant_id == "t1"


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
async def test_process_schedule_op_empty_app_id_discards():
    mock_scheduler = MagicMock()

    with patch.object(ctrl_main, "scheduler_manager", mock_scheduler):
        await ctrl_main._process_schedule_op(
            _build_schedule_op("UPSERT", app_id="")
        )

    mock_scheduler.add_schedule.assert_not_called()


@pytest.mark.asyncio
async def test_process_schedule_op_malformed_proto_raises():
    from google.protobuf.message import DecodeError
    with pytest.raises(DecodeError):
        await ctrl_main._process_schedule_op(b"not a proto")


@pytest.mark.asyncio
async def test_process_schedule_op_invalid_cron_discards():
    mock_scheduler = MagicMock()

    with patch.object(ctrl_main, "scheduler_manager", mock_scheduler):
        await ctrl_main._process_schedule_op(
            _build_schedule_op("UPSERT", cron="not a cron")
        )

    mock_scheduler.add_schedule.assert_not_called()


from fastapi.testclient import TestClient


def _make_test_client():
    import os
    os.environ.setdefault("CONTROLLER_API_TOKEN", "test-token")
    return TestClient(ctrl_main.app)


@pytest.mark.asyncio
async def test_list_schedules_filter_by_app_id():
    mock_session_cm = AsyncMock()
    mock_session = AsyncMock()
    mock_session_cm.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session_cm.__aexit__ = AsyncMock(return_value=False)

    mock_row = MagicMock()
    mock_row._mapping = {
        "id": "shopping:u1:daily-summary",
        "app_id": "shopping",
        "user_id": "u1",
        "tenant_id": "t1",
        "cron": "0 9 * * *",
        "tool_name": "shopping:summarize",
        "params": {"limit": 10},
    }
    mock_result = MagicMock()
    mock_result.all.return_value = [mock_row]
    mock_session.execute = AsyncMock(return_value=mock_result)

    with patch.object(ctrl_main, "SessionLocal", return_value=mock_session_cm):
        client = _make_test_client()
        resp = client.get("/schedules?app_id=shopping", headers={"Authorization": "Bearer test-token"})

    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["app_id"] == "shopping"


@pytest.mark.asyncio
async def test_list_schedules_filter_by_user_id():
    mock_session_cm = AsyncMock()
    mock_session = AsyncMock()
    mock_session_cm.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session_cm.__aexit__ = AsyncMock(return_value=False)

    mock_row = MagicMock()
    mock_row._mapping = {
        "id": "shopping:u1:daily-summary",
        "app_id": "shopping",
        "user_id": "u1",
        "tenant_id": "t1",
        "cron": "0 9 * * *",
        "tool_name": "shopping:summarize",
        "params": {},
    }
    mock_result = MagicMock()
    mock_result.all.return_value = [mock_row]
    mock_session.execute = AsyncMock(return_value=mock_result)

    with patch.object(ctrl_main, "SessionLocal", return_value=mock_session_cm):
        client = _make_test_client()
        resp = client.get("/schedules?user_id=u1", headers={"Authorization": "Bearer test-token"})

    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["user_id"] == "u1"


@pytest.mark.asyncio
async def test_list_schedules_filter_by_app_id_and_user_id():
    mock_session_cm = AsyncMock()
    mock_session = AsyncMock()
    mock_session_cm.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session_cm.__aexit__ = AsyncMock(return_value=False)

    mock_row = MagicMock()
    mock_row._mapping = {
        "id": "shopping:u1:daily-summary",
        "app_id": "shopping",
        "user_id": "u1",
        "tenant_id": "t1",
        "cron": "0 9 * * *",
        "tool_name": "shopping:summarize",
        "params": {},
    }
    mock_result = MagicMock()
    mock_result.all.return_value = [mock_row]
    mock_session.execute = AsyncMock(return_value=mock_result)

    with patch.object(ctrl_main, "SessionLocal", return_value=mock_session_cm):
        client = _make_test_client()
        resp = client.get("/schedules?app_id=shopping&user_id=u1", headers={"Authorization": "Bearer test-token"})

    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["app_id"] == "shopping"
    assert data[0]["user_id"] == "u1"


@pytest.mark.asyncio
async def test_delete_app_schedules_removes_all():
    mock_scheduler = MagicMock()
    mock_session_cm = AsyncMock()
    mock_session = AsyncMock()
    mock_session_cm.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session_cm.__aexit__ = AsyncMock(return_value=False)

    mock_result = MagicMock()
    mock_result.all.return_value = [("shopping:u1:daily-summary",), ("shopping:u2:nightly",)]
    mock_session.execute = AsyncMock(return_value=mock_result)

    with patch.object(ctrl_main, "scheduler_manager", mock_scheduler), \
         patch.object(ctrl_main, "SessionLocal", return_value=mock_session_cm):
        client = _make_test_client()
        resp = client.delete(
            "/apps/shopping/schedules",
            headers={"Authorization": "Bearer test-token"},
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["cancelled"] == 2
    assert body["app_id"] == "shopping"
    assert mock_scheduler.remove_schedule.call_count == 2


@pytest.mark.asyncio
async def test_delete_app_schedules_returns_zero_when_none():
    mock_scheduler = MagicMock()
    mock_session_cm = AsyncMock()
    mock_session = AsyncMock()
    mock_session_cm.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session_cm.__aexit__ = AsyncMock(return_value=False)

    mock_result = MagicMock()
    mock_result.all.return_value = []
    mock_session.execute = AsyncMock(return_value=mock_result)

    with patch.object(ctrl_main, "scheduler_manager", mock_scheduler), \
         patch.object(ctrl_main, "SessionLocal", return_value=mock_session_cm):
        client = _make_test_client()
        resp = client.delete(
            "/apps/shopping/schedules",
            headers={"Authorization": "Bearer test-token"},
        )

    assert resp.status_code == 200
    assert resp.json()["cancelled"] == 0


@pytest.mark.asyncio
async def test_delete_app_schedules_rejects_invalid_app_id():
    client = _make_test_client()
    # colon is not in [a-zA-Z0-9_-] — reaches the handler and fails the regex
    resp = client.delete(
        "/apps/foo:bar/schedules",
        headers={"Authorization": "Bearer test-token"},
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_delete_app_schedules_requires_token():
    client = _make_test_client()
    resp = client.delete("/apps/shopping/schedules")
    assert resp.status_code == 403
