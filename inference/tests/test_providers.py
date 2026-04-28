from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock
import pytest
from providers.base import InferenceProvider, TextChunk, ToolUse, StreamDone
from providers.anthropic import AnthropicProvider
from providers.gemini import GeminiProvider
from providers.ollama import OllamaProvider


# ── AnthropicProvider ────────────────────────────────────────────────────────

class _FakeAnthropicStream:
    def __init__(self, texts, stop_reason="end_turn", tool_blocks=None):
        self._texts = texts
        self._stop_reason = stop_reason
        self._tool_blocks = tool_blocks or []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        pass

    @property
    def text_stream(self):
        return self._iter()

    async def _iter(self):
        for t in self._texts:
            yield t

    async def get_final_message(self):
        msg = MagicMock()
        msg.stop_reason = self._stop_reason
        msg.content = self._tool_blocks
        return msg


async def test_anthropic_yields_text_chunks():
    from unittest.mock import patch
    with patch("providers.anthropic.AsyncAnthropic") as Mock:
        mock_client = MagicMock()
        Mock.return_value = mock_client
        mock_client.messages.stream.return_value = _FakeAnthropicStream(["Hello", " world"])

        provider = AnthropicProvider(api_key="k", model="claude-opus-4-7", max_tokens=1024)
        events = []
        async for evt in provider.generate([{"role": "user", "content": "hi"}]):
            events.append(evt)

    text_events = [e for e in events if isinstance(e, TextChunk)]
    done_events = [e for e in events if isinstance(e, StreamDone)]
    assert [e.content for e in text_events] == ["Hello", " world"]
    assert len(done_events) == 1
    assert done_events[0].stop_reason == "end_turn"


async def test_anthropic_yields_tool_use():
    from unittest.mock import patch
    tool_block = MagicMock()
    tool_block.type = "tool_use"
    tool_block.id = "call-1"
    tool_block.name = "search"
    tool_block.input = {"q": "test"}

    with patch("providers.anthropic.AsyncAnthropic") as Mock:
        mock_client = MagicMock()
        Mock.return_value = mock_client
        mock_client.messages.stream.return_value = _FakeAnthropicStream(
            [], stop_reason="tool_use", tool_blocks=[tool_block]
        )

        provider = AnthropicProvider(api_key="k", model="m", max_tokens=100)
        events = []
        async for evt in provider.generate([{"role": "user", "content": "hi"}]):
            events.append(evt)

    done = events[-1]
    assert isinstance(done, StreamDone)
    assert done.stop_reason == "tool_use"
    assert len(done.tool_calls) == 1
    assert done.tool_calls[0].call_id == "call-1"
    assert done.tool_calls[0].name == "search"
    assert done.tool_calls[0].input == {"q": "test"}


async def test_anthropic_passes_tools_when_provided():
    from unittest.mock import patch
    with patch("providers.anthropic.AsyncAnthropic") as Mock:
        mock_client = MagicMock()
        Mock.return_value = mock_client
        mock_client.messages.stream.return_value = _FakeAnthropicStream([])

        tools = [{"name": "calc", "description": "math", "input_schema": {"type": "object"}}]
        provider = AnthropicProvider(api_key="k", model="m", max_tokens=100)
        async for _ in provider.generate([], tools=tools):
            pass

        call_kwargs = mock_client.messages.stream.call_args.kwargs
        assert call_kwargs["tools"] == tools


# ── GeminiProvider ───────────────────────────────────────────────────────────

async def test_gemini_yields_text_chunks():
    from unittest.mock import patch

    chunk1 = MagicMock()
    chunk1.text = "Hello"
    chunk1.candidates = []
    chunk2 = MagicMock()
    chunk2.text = " world"
    chunk2.candidates = []

    async def fake_stream(*_, **__):
        for c in [chunk1, chunk2]:
            yield c

    with patch("providers.gemini.genai") as mock_genai:
        mock_model = MagicMock()
        mock_genai.GenerativeModel.return_value = mock_model
        mock_model.generate_content_async = AsyncMock(return_value=fake_stream())

        provider = GeminiProvider(api_key="gkey", model="gemini-1.5-pro", max_tokens=1024)
        events = []
        async for evt in provider.generate([{"role": "user", "content": "hi"}]):
            events.append(evt)

    text_events = [e for e in events if isinstance(e, TextChunk)]
    assert [e.content for e in text_events] == ["Hello", " world"]
    assert events
    done = events[-1]
    assert isinstance(done, StreamDone)
    assert done.stop_reason == "end_turn"


async def test_gemini_maps_function_call_to_tool_use():
    from unittest.mock import patch

    fc = MagicMock()
    fc.name = "search"
    fc.args = {"q": "test"}

    part = MagicMock()
    part.function_call = fc
    part.text = None

    candidate = MagicMock()
    candidate.content = MagicMock()
    candidate.content.parts = [part]

    chunk = MagicMock()
    chunk.text = None
    chunk.candidates = [candidate]

    async def fake_stream(*_, **__):
        yield chunk

    with patch("providers.gemini.genai") as mock_genai:
        mock_model = MagicMock()
        mock_genai.GenerativeModel.return_value = mock_model
        mock_model.generate_content_async = AsyncMock(return_value=fake_stream())

        provider = GeminiProvider(api_key="gkey", model="m", max_tokens=100)
        events = []
        async for evt in provider.generate([{"role": "user", "content": "hi"}]):
            events.append(evt)

    assert events
    done = events[-1]
    assert isinstance(done, StreamDone)
    assert done.stop_reason == "tool_use"
    assert done.tool_calls[0].name == "search"


# ── OllamaProvider ───────────────────────────────────────────────────────────

async def test_ollama_yields_text_chunks():
    from unittest.mock import patch

    delta1 = MagicMock()
    delta1.content = "Hello"
    delta1.tool_calls = None
    chunk1 = MagicMock()
    chunk1.choices = [MagicMock(delta=delta1, finish_reason=None)]

    delta2 = MagicMock()
    delta2.content = " world"
    delta2.tool_calls = None
    chunk2 = MagicMock()
    chunk2.choices = [MagicMock(delta=delta2, finish_reason="stop")]

    async def fake_stream(*_, **__):
        for c in [chunk1, chunk2]:
            yield c

    with patch("providers.ollama.AsyncOpenAI") as Mock:
        mock_client = MagicMock()
        Mock.return_value = mock_client
        mock_client.chat.completions.create = AsyncMock(return_value=fake_stream())

        provider = OllamaProvider(base_url="http://localhost:11434", model="qwen2.5-coder", max_tokens=512)
        events = []
        async for evt in provider.generate([{"role": "user", "content": "hi"}]):
            events.append(evt)

    text_events = [e for e in events if isinstance(e, TextChunk)]
    assert [e.content for e in text_events] == ["Hello", " world"]
    assert events
    done = events[-1]
    assert isinstance(done, StreamDone)
    assert done.stop_reason == "end_turn"
