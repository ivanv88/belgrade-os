from __future__ import annotations
import json
import os
import sys
import pytest
from unittest.mock import AsyncMock, patch
from fastapi.testclient import TestClient


_TEST_ENV = {
    "BRIDGE_URL": "http://localhost:8081",
    "MCP_JWT_SECRET": "test-secret",
    "MCP_DEFAULT_USER_ID": "ivan",
    "MCP_DEFAULT_TENANT_ID": "default",
    "CF_TEAM_DOMAIN": "beg-os",
    "CF_MCP_AUDIENCE": "test-audience",
}


@pytest.fixture()
def mcp_client():
    env_patcher = patch.dict(os.environ, _TEST_ENV)
    env_patcher.start()
    for mod in list(sys.modules.keys()):
        if mod.startswith("main") or mod.startswith("oauth") or mod.startswith("registry"):
            del sys.modules[mod]
    import main as m
    client = TestClient(m.app)
    yield client, m
    env_patcher.stop()


def _make_token(client, m) -> str:
    with patch("oauth.validate_cf_jwt", return_value={"sub": "svc"}):
        resp = client.post(
            "/oauth/token",
            data={"grant_type": "client_credentials"},
            headers={"CF-Access-Jwt-Assertion": "valid"},
        )
    return resp.json()["access_token"]


def test_token_missing_cf_jwt_returns_401(mcp_client):
    client, _ = mcp_client
    resp = client.post(
        "/oauth/token",
        data={"grant_type": "client_credentials"},
    )
    assert resp.status_code == 401


def test_token_wrong_grant_type_returns_400(mcp_client):
    client, _ = mcp_client
    resp = client.post(
        "/oauth/token",
        data={"grant_type": "password"},
        headers={"CF-Access-Jwt-Assertion": "fake-jwt"},
    )
    assert resp.status_code == 400


def test_token_invalid_cf_jwt_returns_401(mcp_client):
    client, _ = mcp_client
    with patch("oauth.validate_cf_jwt", side_effect=Exception("invalid")):
        resp = client.post(
            "/oauth/token",
            data={"grant_type": "client_credentials"},
            headers={"CF-Access-Jwt-Assertion": "bad-token"},
        )
    assert resp.status_code == 401


def test_token_valid_cf_jwt_returns_bearer_token(mcp_client):
    client, _ = mcp_client
    with patch("oauth.validate_cf_jwt", return_value={"sub": "service-token-abc"}):
        resp = client.post(
            "/oauth/token",
            data={"grant_type": "client_credentials"},
            headers={"CF-Access-Jwt-Assertion": "valid-jwt"},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["token_type"] == "bearer"
    assert "access_token" in body
    assert body["expires_in"] == 3600


def test_token_encodes_cf_sub_as_user_id(mcp_client):
    """Issued Belgrade token must carry the CF JWT sub, not the default user."""
    import jwt as _jwt
    client, _ = mcp_client
    with patch("oauth.validate_cf_jwt", return_value={"sub": "alice@example.com"}):
        resp = client.post(
            "/oauth/token",
            data={"grant_type": "client_credentials"},
            headers={"CF-Access-Jwt-Assertion": "valid-jwt"},
        )
    assert resp.status_code == 200
    claims = _jwt.decode(
        resp.json()["access_token"],
        _TEST_ENV["MCP_JWT_SECRET"],
        algorithms=["HS256"],
    )
    assert claims["user_id"] == "alice@example.com"


def test_token_falls_back_to_default_when_sub_absent(mcp_client):
    """Falls back to MCP_DEFAULT_USER_ID when CF JWT has no sub claim."""
    import jwt as _jwt
    client, _ = mcp_client
    with patch("oauth.validate_cf_jwt", return_value={}):
        resp = client.post(
            "/oauth/token",
            data={"grant_type": "client_credentials"},
            headers={"CF-Access-Jwt-Assertion": "valid-jwt"},
        )
    assert resp.status_code == 200
    claims = _jwt.decode(
        resp.json()["access_token"],
        _TEST_ENV["MCP_JWT_SECRET"],
        algorithms=["HS256"],
    )
    assert claims["user_id"] == _TEST_ENV["MCP_DEFAULT_USER_ID"]


def test_mcp_unauthenticated_returns_401(mcp_client):
    client, _ = mcp_client
    resp = client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "method": "initialize", "id": 1},
    )
    assert resp.status_code == 401


def test_mcp_initialize_returns_capabilities(mcp_client):
    client, m = mcp_client
    token = _make_token(client, m)
    resp = client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "method": "initialize", "params": {}, "id": 1},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["result"]["protocolVersion"] == "2024-11-05"
    assert "tools" in body["result"]["capabilities"]


def test_mcp_tools_list_returns_bridge_tools(mcp_client):
    client, m = mcp_client
    token = _make_token(client, m)

    bridge_tools = [
        {
            "name": "shopping:add-item",
            "description": "Add item to list",
            "input_schema_json": json.dumps({"type": "object", "properties": {"item": {"type": "string"}}}),
            "app_id": "shopping",
            "mcp_hint": "Use when buying groceries",
        }
    ]

    with patch("registry.list_mcp_tools", new_callable=AsyncMock, return_value=bridge_tools):
        resp = client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "method": "tools/list", "params": {}, "id": 2},
            headers={"Authorization": f"Bearer {token}"},
        )
    assert resp.status_code == 200
    tools = resp.json()["result"]["tools"]
    assert len(tools) == 1
    assert tools[0]["name"] == "shopping:add-item"
    assert tools[0]["description"] == "Add item to list. Use when buying groceries."
    assert tools[0]["inputSchema"] == {"type": "object", "properties": {"item": {"type": "string"}}}


def test_mcp_tools_call_routes_to_bridge(mcp_client):
    client, m = mcp_client
    token = _make_token(client, m)

    bridge_tools = [
        {
            "name": "shopping:add-item",
            "description": "Add item",
            "input_schema_json": "{}",
            "app_id": "shopping",
            "mcp_hint": None,
        }
    ]
    bridge_result = {"success": True, "output_json": json.dumps({"added": "milk"}), "error": ""}

    with patch("registry.list_mcp_tools", new_callable=AsyncMock, return_value=bridge_tools), \
         patch("registry.call_tool", new_callable=AsyncMock, return_value=bridge_result) as mock_call:
        resp = client.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "method": "tools/call",
                "params": {"name": "shopping:add-item", "arguments": {"item": "milk"}},
                "id": 3,
            },
            headers={"Authorization": f"Bearer {token}"},
        )

    assert resp.status_code == 200
    content = resp.json()["result"]["content"]
    assert content[0]["type"] == "text"
    assert "milk" in content[0]["text"]

    mock_call.assert_called_once_with(
        "shopping:add-item",
        {"item": "milk"},
        "svc",   # sub from CF JWT used by _make_token
        "default",
    )


def test_mcp_tools_call_unknown_tool_returns_error(mcp_client):
    client, m = mcp_client
    token = _make_token(client, m)

    with patch("registry.list_mcp_tools", new_callable=AsyncMock, return_value=[]):
        resp = client.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "method": "tools/call",
                "params": {"name": "shopping:unknown", "arguments": {}},
                "id": 4,
            },
            headers={"Authorization": f"Bearer {token}"},
        )

    assert resp.status_code == 200
    body = resp.json()
    assert "error" in body
    assert body["error"]["code"] == -32602


def test_mcp_unknown_method_returns_error(mcp_client):
    client, m = mcp_client
    token = _make_token(client, m)
    resp = client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "method": "unknown/method", "id": 5},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert "error" in body
    assert body["error"]["code"] == -32601
