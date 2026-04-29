from __future__ import annotations
import httpx
from gen import belgrade_os_pb2


class BridgeClient:
    def __init__(self, base_url: str, timeout_s: float = 30.0) -> None:
        self._client = httpx.AsyncClient(base_url=base_url, timeout=timeout_s)

    async def execute(self, call: belgrade_os_pb2.ToolCall) -> belgrade_os_pb2.ToolResult:
        try:
            response = await self._client.post("/v1/execute", json={
                "call_id":    call.call_id,
                "task_id":    call.task_id,
                "tool_name":  call.tool_name,
                "input_json": call.input_json,
                "trace_id":   call.trace_id,
            })
            response.raise_for_status()
            data = response.json()
            return belgrade_os_pb2.ToolResult(
                call_id=data["call_id"],
                task_id=data["task_id"],
                success=data["success"],
                output_json=data.get("output_json", ""),
                error=data.get("error", ""),
            )
        except Exception as exc:
            return belgrade_os_pb2.ToolResult(
                call_id=call.call_id,
                task_id=call.task_id,
                success=False,
                error=f"bridge error: {exc}",
            )

    async def close(self) -> None:
        await self._client.aclose()
