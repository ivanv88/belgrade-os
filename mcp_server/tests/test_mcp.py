from __future__ import annotations
import os
import pytest
from unittest.mock import patch
from fastapi.testclient import TestClient


_TEST_ENV = {
    "BRIDGE_URL": "http://localhost:8081",
    "MCP_JWT_SECRET": "test-secret",
    "MCP_DEFAULT_USER_ID": "ivan",
    "MCP_DEFAULT_TENANT_ID": "default",
    "CF_TEAM_DOMAIN": "beg-os",
    "CF_MCP_AUDIENCE": "test-audience",
}


def _make_client():
    # Keep the env patch active for the lifetime of the returned client so that
    # functions reading env at call time (e.g. _get_jwt_secret) see test values.
    env_patcher = patch.dict(os.environ, _TEST_ENV)
    env_patcher.start()
    import sys
    for mod in list(sys.modules.keys()):
        if mod.startswith("main") or mod.startswith("oauth") or mod.startswith("registry"):
            del sys.modules[mod]
    import main as m
    return TestClient(m.app), m


def test_token_missing_cf_jwt_returns_401():
    client, _ = _make_client()
    resp = client.post(
        "/oauth/token",
        data={"grant_type": "client_credentials"},
    )
    assert resp.status_code == 401


def test_token_wrong_grant_type_returns_400():
    client, _ = _make_client()
    resp = client.post(
        "/oauth/token",
        data={"grant_type": "password"},
        headers={"CF-Access-Jwt-Assertion": "fake-jwt"},
    )
    assert resp.status_code == 400


def test_token_invalid_cf_jwt_returns_401():
    client, _ = _make_client()
    with patch("oauth.validate_cf_jwt", side_effect=Exception("invalid")):
        resp = client.post(
            "/oauth/token",
            data={"grant_type": "client_credentials"},
            headers={"CF-Access-Jwt-Assertion": "bad-token"},
        )
    assert resp.status_code == 401


def test_token_valid_cf_jwt_returns_bearer_token():
    client, _ = _make_client()
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


def test_mcp_unauthenticated_returns_401():
    client, _ = _make_client()
    resp = client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "method": "initialize", "id": 1},
    )
    assert resp.status_code == 401
