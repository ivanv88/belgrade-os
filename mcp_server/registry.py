from __future__ import annotations
import json
import os
import uuid
import httpx

_BRIDGE_URL = os.getenv("BRIDGE_URL", "http://localhost:8081")


async def list_mcp_tools() -> list:
    """Fetch MCP-exposed tools from Bridge."""
    async with httpx.AsyncClient() as client:
        resp = await client.get(f"{_BRIDGE_URL}/v1/tools?mcp=true")
        resp.raise_for_status()
        return resp.json()


async def call_tool(
    tool_name: str,
    arguments: dict,
    user_id: str,
    tenant_id: str,
) -> dict:
    """Execute a tool via Bridge /v1/execute."""
    payload = {
        "call_id": str(uuid.uuid4()),
        "task_id": str(uuid.uuid4()),
        "trace_id": str(uuid.uuid4()),
        "tool_name": tool_name,
        "input_json": json.dumps(arguments),
        "user_id": user_id,
        "tenant_id": tenant_id,
    }
    async with httpx.AsyncClient() as client:
        resp = await client.post(f"{_BRIDGE_URL}/v1/execute", json=payload)
        resp.raise_for_status()
        return resp.json()
