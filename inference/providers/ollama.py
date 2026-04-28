from __future__ import annotations

import inspect
import json
from collections.abc import AsyncIterator
from openai import AsyncOpenAI
from .base import InferenceProvider, TextChunk, ToolUse, StreamDone


class OllamaProvider(InferenceProvider):
    def __init__(self, base_url: str, model: str, max_tokens: int) -> None:
        self._client = AsyncOpenAI(base_url=f"{base_url}/v1", api_key="ollama")
        self._model = model
        self._max_tokens = max_tokens

    async def generate(
        self,
        messages: list,
        tools: list = None,
    ) -> AsyncIterator:
        kwargs = dict(
            model=self._model,
            messages=messages,
            max_tokens=self._max_tokens,
            stream=True,
        )
        if tools:
            kwargs["tools"] = tools

        tool_calls = []
        result = self._client.chat.completions.create(**kwargs)
        if inspect.iscoroutine(result):
            result = await result
        async for chunk in result:
            choice = chunk.choices[0]
            delta = choice.delta
            if delta.content:
                yield TextChunk(content=delta.content)
            tcs = getattr(delta, "tool_calls", None)
            if tcs:
                for tc in tcs:
                    tool_calls.append(
                        ToolUse(
                            call_id=tc.id or "",
                            name=tc.function.name,
                            input=json.loads(tc.function.arguments or "{}"),
                        )
                    )

        yield StreamDone(
            stop_reason="tool_use" if tool_calls else "end_turn",
            tool_calls=tool_calls,
        )
