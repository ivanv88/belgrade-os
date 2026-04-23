from __future__ import annotations
import logging
from typing import Any, Callable, Optional
from fastapi import FastAPI, HTTPException
from core.models.manifest import AppManifest

logger = logging.getLogger(__name__)


def build_mcp_tool_name(manifest: AppManifest) -> str:
    return f"run_{manifest.app_id.replace('-', '_')}"


def validate_cloudflare_header(value: Optional[str]) -> bool:
    if not value:
        raise ValueError("Cloudflare Access Identity header is required")
    return True


def register_mcp_tools(
    app: FastAPI,
    manifests: list[AppManifest],
    ctx_factories: dict[str, Callable[[], Any]],
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
            cf_access: Optional[str] = None,
            _app_id: str = app_id,
            _factory: Any = ctx_factory,
        ) -> dict[str, str]:
            try:
                validate_cloudflare_header(cf_access)
            except ValueError as e:
                raise HTTPException(status_code=401, detail=str(e))
            return {"status": "triggered", "app_id": _app_id}

        logger.info("MCP tool registered: %s", tool_name)

    app.mount("/mcp", mcp.http_app())
