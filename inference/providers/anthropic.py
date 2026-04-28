from __future__ import annotations

from collections.abc import AsyncIterator
from anthropic import AsyncAnthropic
from .base import InferenceProvider, TextChunk, ToolUse, StreamDone


class AnthropicProvider(InferenceProvider):
    def __init__(self, api_key: str, model: str, max_tokens: int) -> None:
        self._client = AsyncAnthropic(api_key=api_key)
        self._model = model
        self._max_tokens = max_tokens

    async def generate(
        self,
        messages: list,
        tools: list = None,
    ) -> AsyncIterator:
        kwargs = dict(
            model=self._model,
            max_tokens=self._max_tokens,
            messages=messages,
        )
        if tools:
            kwargs["tools"] = tools

        async with self._client.messages.stream(**kwargs) as stream:
            async for text in stream.text_stream:
                yield TextChunk(content=text)
            final = await stream.get_final_message()

        tool_calls = [
            ToolUse(call_id=b.id, name=b.name, input=b.input)
            for b in final.content
            if b.type == "tool_use"
        ]
        yield StreamDone(
            stop_reason="tool_use" if tool_calls else final.stop_reason,
            tool_calls=tool_calls,
        )
