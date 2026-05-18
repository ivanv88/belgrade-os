from __future__ import annotations
import pytest
import httpx
from unittest.mock import AsyncMock, MagicMock, patch
from gen import belgrade_os_pb2
from bridge_client import BridgeClient


def _make_call() -> belgrade_os_pb2.ToolCall:
    c = belgrade_os_pb2.ToolCall()
    c.call_id = "c1"
    c.task_id = "t1"
    c.tool_name = "shopping:add_item"
    c.input_json = '{"item":"milk"}'
    c.trace_id = "tr1"
    return c


def _make_client_with_mock():
    with patch("bridge_client.httpx.AsyncClient") as MockClient:
        mock_http = AsyncMock()
        MockClient.return_value = mock_http
        client = BridgeClient(base_url="http://bridge:8081", timeout_s=30)
    return client, mock_http


async def test_execute_success():
    client, mock_http = _make_client_with_mock()
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {
        "call_id": "c1", "task_id": "t1",
        "success": True, "output_json": '{"added":true}', "error": ""
    }
    mock_http.post = AsyncMock(return_value=mock_resp)

    result = await client.execute(_make_call())

    assert result.call_id == "c1"
    assert result.task_id == "t1"
    assert result.success is True
    assert result.output_json == '{"added":true}'
    assert result.error == ""


async def test_execute_logical_failure():
    client, mock_http = _make_client_with_mock()
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {
        "call_id": "c1", "task_id": "t1",
        "success": False, "output_json": "",
        "error": "tool not found: shopping:add_item"
    }
    mock_http.post = AsyncMock(return_value=mock_resp)

    result = await client.execute(_make_call())

    assert result.success is False
    assert "shopping:add_item" in result.error


async def test_execute_connection_error_returns_failed_result():
    client, mock_http = _make_client_with_mock()
    mock_http.post = AsyncMock(side_effect=httpx.ConnectError("refused"))

    result = await client.execute(_make_call())

    assert result.call_id == "c1"
    assert result.task_id == "t1"
    assert result.success is False
    assert "bridge error" in result.error


async def test_execute_sends_correct_payload():
    client, mock_http = _make_client_with_mock()
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {
        "call_id": "c1", "task_id": "t1",
        "success": True, "output_json": "[]", "error": ""
    }
    mock_http.post = AsyncMock(return_value=mock_resp)

    await client.execute(_make_call())

    post_kwargs = mock_http.post.call_args
    assert post_kwargs.args[0] == "/v1/execute"
    sent = post_kwargs.kwargs["json"]
    assert sent["call_id"] == "c1"
    assert sent["task_id"] == "t1"
    assert sent["tool_name"] == "shopping:add_item"
    assert sent["input_json"] == '{"item":"milk"}'
    assert sent["trace_id"] == "tr1"
