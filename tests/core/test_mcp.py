import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from core.mcp import build_mcp_tool_name, register_mcp_tools
from core.models.manifest import AppManifest, AppStorage, AppConfig


def make_mcp_manifest(app_id: str) -> AppManifest:
    return AppManifest(
        app_id=app_id,
        name="Test",
        description="Does something useful",
        version="1.0.0",
        pattern="worker",
        mcp_enabled=True,
        triggers=[],
        storage=AppStorage(scope=app_id),
        config=AppConfig(),
    )


def test_tool_name_format() -> None:
    manifest = make_mcp_manifest("nutrition")
    assert build_mcp_tool_name(manifest) == "run_nutrition"


def test_tool_name_replaces_hyphens() -> None:
    manifest = make_mcp_manifest("wiki-compiler")
    assert build_mcp_tool_name(manifest) == "run_wiki_compiler"


def test_register_mcp_tools_signature_accepts_event_bus() -> None:
    import inspect
    sig = inspect.signature(register_mcp_tools)
    assert "event_bus" in sig.parameters
