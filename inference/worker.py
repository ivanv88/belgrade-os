from __future__ import annotations

import json
from typing import Optional

from gen import belgrade_os_pb2
from providers.base import InferenceProvider, StreamDone, TextChunk, ToolUse
from redis_client import RedisClient

CONSUMER_GROUP = "inference"


async def process_task(
    task: belgrade_os_pb2.Task,
    redis: RedisClient,
    provider: InferenceProvider,
    consumer_id: str,
) -> None:
    """Drive the full inference + tool loop for a single task."""
    messages: list = [{"role": "user", "content": task.prompt}]
    tools: list = []

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
                        # Break out of the inner for-loop; the flag below
                        # will also break the outer while.
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
