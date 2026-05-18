from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Optional, Union
from google import genai
from google.genai import types
from .base import InferenceProvider, TextChunk, ToolUse, StreamDone


def _to_gemini_contents(messages: list) -> list[types.Content]:
    result = []
    for m in messages:
        role = "model" if m["role"] == "assistant" else m["role"]
        content = m["content"]
        if isinstance(content, str):
            result.append(types.Content(role=role, parts=[types.Part.from_text(text=content)]))
        elif isinstance(content, list):
            parts = []
            for item in content:
                if isinstance(item, dict) and item.get("type") == "tool_result":
                    parts.append(types.Part.from_function_response(
                        name=item.get("tool_use_id", ""),
                        response={"result": item.get("content", "")},
                    ))
                elif isinstance(item, str):
                    parts.append(types.Part.from_text(text=item))
            result.append(types.Content(role=role, parts=parts or [types.Part.from_text(text="")]))
        else:
            result.append(types.Content(role=role, parts=[types.Part.from_text(text=str(content))]))
    return result


class GeminiProvider(InferenceProvider):
    def __init__(self, api_key: str, model: str, max_tokens: int) -> None:
        self._client = genai.Client(api_key=api_key)
        self._model = model
        self._max_tokens = max_tokens

    async def generate(
        self,
        messages: list,
        tools: Optional[list] = None,
    ) -> AsyncIterator[Union[TextChunk, StreamDone]]:
        contents = _to_gemini_contents(messages)
        config_kwargs: dict = {"max_output_tokens": self._max_tokens}
        if tools:
            config_kwargs["tools"] = tools
        config = types.GenerateContentConfig(**config_kwargs)

        tool_calls: list[ToolUse] = []
        async for chunk in await self._client.aio.models.generate_content_stream(
            model=self._model,
            contents=contents,
            config=config,
        ):
            if chunk.text:
                yield TextChunk(content=chunk.text)
            for fc in (chunk.function_calls or []):
                tool_calls.append(ToolUse(
                    call_id=fc.id or str(uuid.uuid4()),
                    name=fc.name,
                    input=dict(fc.args),
                ))

        yield StreamDone(
            stop_reason="tool_use" if tool_calls else "end_turn",
            tool_calls=tool_calls,
        )
