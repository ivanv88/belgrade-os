from __future__ import annotations
import os
import pytest
from unittest.mock import AsyncMock, patch
from belgrade_sdk.app import BelgradeApp
from belgrade_sdk.models import RegisterRequest, ToolDefinition


def test_mcp_flag_false_by_default():
    app = BelgradeApp(app_id="shopping", bridge_url="http://bridge:8081")
    assert app._mcp_enabled is False


def test_mcp_flag_true_when_env_set():
    with patch.dict(os.environ, {"BEG_OS_MCP_ENABLED": "true"}):
        app = BelgradeApp(app_id="shopping", bridge_url="http://bridge:8081")
    assert app._mcp_enabled is True


def test_mcp_flag_in_register_request():
    with patch.dict(os.environ, {"BEG_OS_MCP_ENABLED": "true"}):
        app = BelgradeApp(app_id="shopping", bridge_url="http://bridge:8081")

    @app.tool("add-item", description="Add item")
    def add_item(ctx, item: str):
        pass

    req = RegisterRequest(
        app_id=app.app_id,
        callback_url="http://localhost:9001",
        tools=app.tool_definitions,
        mcp=app._mcp_enabled,
    )
    assert req.mcp is True


def test_mcp_hint_in_tool_definition():
    app = BelgradeApp(app_id="shopping", bridge_url="http://bridge:8081")

    @app.tool("add-item", description="Add item", mcp_hint="Use when buying groceries")
    def add_item(ctx, item: str):
        pass

    defn = app.tool_definitions[0]
    assert defn.mcp_hint == "Use when buying groceries"


def test_no_mcp_hint_is_none():
    app = BelgradeApp(app_id="shopping", bridge_url="http://bridge:8081")

    @app.tool("add-item", description="Add item")
    def add_item(ctx, item: str):
        pass

    defn = app.tool_definitions[0]
    assert defn.mcp_hint is None
