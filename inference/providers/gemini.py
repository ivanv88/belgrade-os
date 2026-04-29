from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Optional, Union
import google.generativeai as genai
from .base import InferenceProvider, TextChunk, ToolUse, StreamDone


def _to_gemini_messages(messages: list) -> list:
    result = []
    for m in messages:
        role = "model" if m["role"] == "assistant" else m["role"]
        content = m["content"]
        if isinstance(content, str):
            result.append({"role": role, "parts": [content]})
        else:
            result.append({"role": role, "parts": content})
    return result


class GeminiProvider(InferenceProvider):
    def __init__(self, api_key: str, model: str, max_tokens: int) -> None:
        genai.configure(api_key=api_key)
        self._model = genai.GenerativeModel(model)
        self._max_tokens = max_tokens

    async def generate(
        self,
        messages: list,
        tools: Optional[list] = None,
    ) -> AsyncIterator[Union[TextChunk, StreamDone]]:
        gemini_msgs = _to_gemini_messages(messages)
        kwargs = dict(stream=True)
        if tools:
            kwargs["tools"] = tools

        tool_calls = []
        async for chunk in await self._model.generate_content_async(gemini_msgs, **kwargs):
            if chunk.text:
                yield TextChunk(content=chunk.text)
            for candidate in getattr(chunk, "candidates", []):
                for part in getattr(candidate.content, "parts", []):
                    fc = getattr(part, "function_call", None)
                    if fc and getattr(fc, "name", None):
                        tool_calls.append(
                            ToolUse(
                                call_id=str(uuid.uuid4()),
                                name=fc.name,
                                input=dict(fc.args),
                            )
                        )

        yield StreamDone(
            stop_reason="tool_use" if tool_calls else "end_turn",
            tool_calls=tool_calls,
        )
