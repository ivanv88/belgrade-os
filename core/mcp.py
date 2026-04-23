from __future__ import annotations
import logging
from typing import Any, Callable, Optional
from fastapi import FastAPI, HTTPException, Request
from core.models.manifest import AppManifest

logger = logging.getLogger(__name__)


def build_mcp_tool_name(manifest: AppManifest) -> str:
    return f"run_{manifest.app_id.replace('-', '_')}"


def register_mcp_tools(
    app: FastAPI,
    manifests: list[AppManifest],
    ctx_factories: dict[str, Callable[[], Any]],
    event_bus: Any,
) -> None:
    try:
        from fastmcp import FastMCP
    except ImportError:
        logger.warning("fastmcp not installed — MCP endpoint skipped")
        return

    mcp = FastMCP("Belgrade AI OS")

    for manifest in manifests:
        if not manifest.mcp_enabled:
            continue

        tool_name = build_mcp_tool_name(manifest)
        description = manifest.description
        app_id = manifest.app_id
        ctx_factory = ctx_factories.get(app_id)

        @mcp.tool(name=tool_name, description=description)
        async def run_app(
            request: Request,
            _app_id: str = app_id,
            _factory: Any = ctx_factory,
            _bus: Any = event_bus,
        ) -> dict[str, str]:
            from core.auth import verify_request_identity
            from core.executor import safe_execute
            verify_request_identity(dict(request.headers))
            if _factory is None:
                raise HTTPException(status_code=500, detail=f"No factory for {_app_id}")
            ctx = await _factory()
            await safe_execute(_app_id, ctx, _bus)
            return {"status": "ok", "app_id": _app_id}

        logger.info("MCP tool registered: %s", tool_name)

    app.mount("/mcp", mcp.http_app())
