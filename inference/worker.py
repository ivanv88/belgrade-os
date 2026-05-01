from __future__ import annotations

import json
import logging
import httpx
import os
from typing import Optional

from gen import belgrade_os_pb2
from providers.base import InferenceProvider, StreamDone, TextChunk, ToolUse
from redis_client import RedisClient

log = logging.getLogger(__name__)

CONSUMER_GROUP = "inference"
BRIDGE_URL = os.getenv("BEG_OS_BRIDGE_URL", "http://localhost:8081")

async def fetch_tools() -> list:
    """Fetches tools from the Capability Bridge and returns them in a format suitable for the provider."""
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(f"{BRIDGE_URL}/v1/tools")
            resp.raise_for_status()
            tools_data = resp.json()
            
            # Format: [{"name": "...", "description": "...", "input_schema": {...}}]
            return [
                {
                    "name": t["name"],
                    "description": t["description"],
                    "input_schema": json.loads(t["input_schema_json"])
                }
                for t in tools_data
            ]
    except Exception as e:
        log.warning("Failed to fetch tools from bridge: %s", e)
        return []

async def process_task(
    task: belgrade_os_pb2.Task,
    redis: RedisClient,
    provider: InferenceProvider,
    consumer_id: str,
) -> None:
    """Drive the full inference + tool loop for a single task."""
    messages: list = [{"role": "user", "content": task.prompt}]
    
    # Task currently doesn't carry tenant_id (missing in gateway/handler.go probably)
    # For now, let's derive it or assume it's passed.
    tenant_id = "household-vladisavljevic" # Placeholder or derived from registry

    tools = await fetch_tools()

    try:
        while True:
            async for event in provider.generate(messages, tools or None):
                if isinstance(event, TextChunk):
                    thought = belgrade_os_pb2.ThoughtEvent(
                        task_id=task.task_id,
                        user_id=task.user_id,
                        trace_id=task.trace_id,
                        type=belgrade_os_pb2.RESPONSE_CHUNK,
                        content=event.content,
                    )
                    await redis.publish_thought(task.task_id, thought.SerializeToString())

                elif isinstance(event, StreamDone):
                    if event.stop_reason == "end_turn":
                        done_signal = True
                        break

                    # stop_reason == "tool_use"
                    done_signal = False
                    tool_results_content: list = []

                    for tool_use in event.tool_calls:
                        # Push the tool call to the runner
                        tc = belgrade_os_pb2.ToolCall(
                            call_id=tool_use.call_id,
                            task_id=task.task_id,
                            tool_name=tool_use.name,
                            input_json=json.dumps(tool_use.input),
                            trace_id=task.trace_id,
                            user_id=task.user_id,
                            tenant_id=tenant_id,
                        )
                        await redis.push_tool_call(tc.SerializeToString())

                        # Wait for the result
                        result_tuple: Optional[tuple[str, bytes]] = await redis.read_tool_result(
                            CONSUMER_GROUP, consumer_id, task.task_id
                        )

                        if result_tuple is not None:
                            msg_id, result_bytes = result_tuple
                            tr = belgrade_os_pb2.ToolResult()
                            tr.ParseFromString(result_bytes)
                            await redis.ack_tool_result(CONSUMER_GROUP, msg_id)

                            if tr.success:
                                tool_results_content.append({
                                    "type": "tool_result",
                                    "tool_use_id": tool_use.call_id,
                                    "content": tr.output_json,
                                })
                            else:
                                tool_results_content.append({
                                    "type": "tool_result",
                                    "tool_use_id": tool_use.call_id,
                                    "content": tr.error or "[tool result unavailable]",
                                })
                        else:
                            tool_results_content.append({
                                "type": "tool_result",
                                "tool_use_id": tool_use.call_id,
                                "content": "[tool result unavailable]",
                            })

                    messages.append({"role": "assistant", "content": []})
                    messages.append({"role": "user", "content": tool_results_content})
                    # Continue outer loop
                    break

            else:
                # Inner for-loop exhausted without a StreamDone — treat as done
                done_signal = True

            if done_signal:
                break

    except Exception as exc:
        error_event = belgrade_os_pb2.ThoughtEvent(
            task_id=task.task_id,
            user_id=task.user_id,
            trace_id=task.trace_id,
            type=belgrade_os_pb2.ERROR,
            content=str(exc),
        )
        await redis.publish_thought(task.task_id, error_event.SerializeToString())
        return

    # Successful completion
    done_event = belgrade_os_pb2.ThoughtEvent(
        task_id=task.task_id,
        user_id=task.user_id,
        trace_id=task.trace_id,
        type=belgrade_os_pb2.DONE,
        content="",
    )
    await redis.publish_thought(task.task_id, done_event.SerializeToString())
