# Inference Controller Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a model-agnostic Inference Controller that reads `Task` messages from Redis Streams, drives a streaming tool loop through a pluggable `InferenceProvider` interface, and publishes `ThoughtEvent` protos back via Redis Pub/Sub. Supports Anthropic, Gemini, and Ollama — selected at runtime via `PROVIDER` env var.

**Architecture:** The worker holds no provider-specific logic. It iterates typed events (`TextChunk | StreamDone`) from the active `InferenceProvider`, publishing `RESPONSE_CHUNK` ThoughtEvents for each text delta and dispatching `ToolCall` protos when `StreamDone.stop_reason == "tool_use"`. The provider factory in `main.py` reads `Config.provider` and instantiates the correct implementation. All three providers normalise their SDK's streaming format into the shared event types.

**Tech Stack:** Python 3.11+, `anthropic>=0.39`, `google-generativeai>=0.8`, `openai>=1.0` (Ollama), `redis-py 5` (asyncio), `protobuf 4`, `pydantic-settings 2`, `pytest`, `pytest-asyncio`

---

## File Map

| Path | Action | Responsibility |
|------|--------|----------------|
| `inference/requirements.txt` | Modify | Add all provider SDKs + pydantic-settings |
| `inference/requirements-dev.txt` | Modify | Add pytest-asyncio |
| `inference/pytest.ini` | Create | `asyncio_mode = auto` |
| `inference/.env.example` | Create | Config template with all provider options |
| `inference/config.py` | Create | Pydantic settings, credential validator, `effective_consumer_id` |
| `inference/providers/__init__.py` | Create | Empty |
| `inference/providers/base.py` | Create | `InferenceProvider` ABC, `TextChunk`, `ToolUse`, `StreamDone` |
| `inference/providers/anthropic.py` | Create | `AnthropicProvider` wrapping `AsyncAnthropic` |
| `inference/providers/gemini.py` | Create | `GeminiProvider` wrapping `google.generativeai` |
| `inference/providers/ollama.py` | Create | `OllamaProvider` wrapping `openai.AsyncOpenAI` with custom base URL |
| `inference/redis_client.py` | Create | XREADGROUP, XACK, PUBLISH, XADD helpers |
| `inference/worker.py` | Create | `process_task(task, redis, provider)` — tool loop |
| `inference/main.py` | Create | Provider factory + consumer loop entry point |
| `inference/tests/test_config.py` | Create | Unit tests for config + credential validation |
| `inference/tests/test_providers.py` | Create | Unit tests for all three providers (mocked SDKs) |
| `inference/tests/test_redis_client.py` | Create | Integration tests (skip if Redis unavailable) |
| `inference/tests/test_worker.py` | Create | Unit tests with mock provider + mock Redis |
| `inference/tests/test_main.py` | Create | Unit tests for consumer loop + provider factory |

All commands run from `inference/` directory.

---

## Task 1: Dependencies, config, env template, and pytest setup

**Files:**
- Modify: `inference/requirements.txt`
- Modify: `inference/requirements-dev.txt`
- Create: `inference/pytest.ini`
- Create: `inference/.env.example`
- Create: `inference/config.py`
- Create: `inference/tests/test_config.py`

- [ ] **Step 1: Write failing tests**

Create `inference/tests/test_config.py`:

```python
import socket
import pytest
from config import Config


def test_defaults():
    c = Config(provider="anthropic", model="claude-opus-4-7", anthropic_api_key="k")
    assert c.redis_url == "redis://localhost:6379"
    assert c.max_tokens == 8192
    assert c.ollama_base_url == "http://localhost:11434"


def test_provider_required(monkeypatch):
    monkeypatch.delenv("PROVIDER", raising=False)
    with pytest.raises(Exception):
        Config(model="some-model")


def test_model_required(monkeypatch):
    monkeypatch.delenv("MODEL", raising=False)
    with pytest.raises(Exception):
        Config(provider="gemini")


def test_anthropic_requires_api_key():
    with pytest.raises(ValueError, match="ANTHROPIC_API_KEY"):
        Config(provider="anthropic", model="claude-opus-4-7")


def test_gemini_requires_api_key():
    with pytest.raises(ValueError, match="GOOGLE_API_KEY"):
        Config(provider="gemini", model="gemini-1.5-pro")


def test_ollama_needs_no_key():
    c = Config(provider="ollama", model="qwen2.5-coder")
    assert c.anthropic_api_key is None
    assert c.google_api_key is None


def test_effective_consumer_id_hostname():
    c = Config(provider="ollama", model="m", consumer_id="")
    assert c.effective_consumer_id == socket.gethostname()


def test_effective_consumer_id_explicit():
    c = Config(provider="ollama", model="m", consumer_id="worker-1")
    assert c.effective_consumer_id == "worker-1"


def test_from_env(monkeypatch):
    monkeypatch.setenv("PROVIDER", "gemini")
    monkeypatch.setenv("MODEL", "gemini-1.5-flash")
    monkeypatch.setenv("GOOGLE_API_KEY", "gkey")
    monkeypatch.setenv("MAX_TOKENS", "4096")
    c = Config()
    assert c.provider == "gemini"
    assert c.model == "gemini-1.5-flash"
    assert c.google_api_key == "gkey"
    assert c.max_tokens == 4096
```

- [ ] **Step 2: Run tests, confirm they fail**

```bash
cd /path/to/inference && python3 -m pytest tests/test_config.py -v
```

Expected: `ModuleNotFoundError: No module named 'config'`

- [ ] **Step 3: Update requirements files**

`inference/requirements.txt` — full replacement:

```
grpcio==1.64.1
protobuf==4.25.3
redis==5.0.4
anthropic>=0.39.0
google-generativeai>=0.8.0
openai>=1.0.0
pydantic-settings>=2.0
```

`inference/requirements-dev.txt` — full replacement:

```
-r requirements.txt
grpcio-tools==1.64.1
pytest==8.2.2
pytest-asyncio>=0.23
```

- [ ] **Step 4: Install deps**

```bash
pip3 install -r requirements-dev.txt
```

- [ ] **Step 5: Create `inference/pytest.ini`**

```ini
[pytest]
asyncio_mode = auto
```

- [ ] **Step 6: Create `inference/.env.example`**

```ini
# ── Required ────────────────────────────────────────────────────────────────
# Provider: anthropic | gemini | ollama
PROVIDER=

# Model name — must match the selected provider
#   anthropic : claude-opus-4-7 | claude-sonnet-4-6 | claude-haiku-4-5-20251001
#   gemini    : gemini-1.5-pro  | gemini-1.5-flash  | gemini-2.0-flash
#   ollama    : qwen2.5-coder   | llama3.2           | mistral
MODEL=

# ── Credentials (set the one matching PROVIDER) ─────────────────────────────
ANTHROPIC_API_KEY=
GOOGLE_API_KEY=
OLLAMA_BASE_URL=http://localhost:11434

# ── Redis ────────────────────────────────────────────────────────────────────
REDIS_URL=redis://localhost:6379

# ── Optional ─────────────────────────────────────────────────────────────────
MAX_TOKENS=8192
CONSUMER_ID=
```

- [ ] **Step 7: Create `inference/config.py`**

```python
import socket
from typing import Literal
from pydantic import model_validator
from pydantic_settings import BaseSettings


class Config(BaseSettings):
    redis_url: str = "redis://localhost:6379"
    provider: Literal["anthropic", "gemini", "ollama"]
    model: str
    max_tokens: int = 8192
    consumer_id: str = ""

    anthropic_api_key: str | None = None
    google_api_key: str | None = None
    ollama_base_url: str = "http://localhost:11434"

    model_config = {"env_file": ".env"}

    @model_validator(mode="after")
    def check_credentials(self) -> "Config":
        if self.provider == "anthropic" and not self.anthropic_api_key:
            raise ValueError("ANTHROPIC_API_KEY required when PROVIDER=anthropic")
        if self.provider == "gemini" and not self.google_api_key:
            raise ValueError("GOOGLE_API_KEY required when PROVIDER=gemini")
        return self

    @property
    def effective_consumer_id(self) -> str:
        return self.consumer_id or socket.gethostname()


def load_config() -> Config:
    return Config()
```

- [ ] **Step 8: Run tests, confirm they pass**

```bash
python3 -m pytest tests/test_config.py -v
```

Expected: 9 tests PASS.

Also confirm existing proto tests still pass:

```bash
python3 -m pytest tests/test_proto.py -v
```

- [ ] **Step 9: Commit**

```bash
git add inference/requirements.txt inference/requirements-dev.txt inference/pytest.ini inference/.env.example inference/config.py inference/tests/test_config.py
git commit -m "feat(inference): multi-provider config + deps"
```

---

## Task 2: Provider interface and all three provider implementations

**Files:**
- Create: `inference/providers/__init__.py`
- Create: `inference/providers/base.py`
- Create: `inference/providers/anthropic.py`
- Create: `inference/providers/gemini.py`
- Create: `inference/providers/ollama.py`
- Create: `inference/tests/test_providers.py`

- [ ] **Step 1: Write failing tests**

Create `inference/tests/test_providers.py`:

```python
import json
from unittest.mock import AsyncMock, MagicMock, patch, call
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
        mock_model.generate_content_async.return_value = fake_stream()

        provider = GeminiProvider(api_key="gkey", model="gemini-1.5-pro", max_tokens=1024)
        events = []
        async for evt in provider.generate([{"role": "user", "content": "hi"}]):
            events.append(evt)

    text_events = [e for e in events if isinstance(e, TextChunk)]
    assert [e.content for e in text_events] == ["Hello", " world"]
    done = events[-1]
    assert isinstance(done, StreamDone)
    assert done.stop_reason == "end_turn"


async def test_gemini_maps_function_call_to_tool_use():
    fc = MagicMock()
    fc.name = "search"
    fc.args = {"q": "test"}

    part = MagicMock()
    part.function_call = fc
    part.text = None

    candidate = MagicMock()
    candidate.finish_reason = MagicMock()
    candidate.finish_reason.name = "STOP"
    candidate.content.parts = [part]

    chunk = MagicMock()
    chunk.text = None
    chunk.candidates = [candidate]
    chunk.parts = [part]

    async def fake_stream(*_, **__):
        yield chunk

    with patch("providers.gemini.genai") as mock_genai:
        mock_model = MagicMock()
        mock_genai.GenerativeModel.return_value = mock_model
        mock_model.generate_content_async.return_value = fake_stream()

        provider = GeminiProvider(api_key="gkey", model="m", max_tokens=100)
        events = []
        async for evt in provider.generate([{"role": "user", "content": "hi"}]):
            events.append(evt)

    done = events[-1]
    assert isinstance(done, StreamDone)
    assert done.stop_reason == "tool_use"
    assert done.tool_calls[0].name == "search"


# ── OllamaProvider ───────────────────────────────────────────────────────────

async def test_ollama_yields_text_chunks():
    delta1 = MagicMock()
    delta1.content = "Hello"
    chunk1 = MagicMock()
    chunk1.choices = [MagicMock(delta=delta1, finish_reason=None)]

    delta2 = MagicMock()
    delta2.content = " world"
    chunk2 = MagicMock()
    chunk2.choices = [MagicMock(delta=delta2, finish_reason="stop")]

    async def fake_stream(*_, **__):
        for c in [chunk1, chunk2]:
            yield c

    with patch("providers.ollama.AsyncOpenAI") as Mock:
        mock_client = MagicMock()
        Mock.return_value = mock_client
        mock_client.chat.completions.create.return_value = fake_stream()

        provider = OllamaProvider(base_url="http://localhost:11434", model="qwen2.5-coder", max_tokens=512)
        events = []
        async for evt in provider.generate([{"role": "user", "content": "hi"}]):
            events.append(evt)

    text_events = [e for e in events if isinstance(e, TextChunk)]
    assert [e.content for e in text_events] == ["Hello", " world"]
    done = events[-1]
    assert isinstance(done, StreamDone)
    assert done.stop_reason == "end_turn"
```

- [ ] **Step 2: Run tests, confirm they fail**

```bash
python3 -m pytest tests/test_providers.py -v
```

Expected: `ModuleNotFoundError: No module named 'providers'`

- [ ] **Step 3: Create `inference/providers/__init__.py`**

Empty file.

- [ ] **Step 4: Create `inference/providers/base.py`**

```python
from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from collections.abc import AsyncIterator


@dataclass
class TextChunk:
    content: str


@dataclass
class ToolUse:
    call_id: str
    name: str
    input: dict


@dataclass
class StreamDone:
    stop_reason: str        # "end_turn" | "tool_use"
    tool_calls: list[ToolUse] = field(default_factory=list)


class InferenceProvider(ABC):
    @abstractmethod
    async def generate(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
    ) -> AsyncIterator[TextChunk | StreamDone]:
        """Yield TextChunk for each text delta, then a single StreamDone."""
```

- [ ] **Step 5: Create `inference/providers/anthropic.py`**

```python
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
        messages: list[dict],
        tools: list[dict] | None = None,
    ) -> AsyncIterator[TextChunk | StreamDone]:
        kwargs: dict = dict(
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
```

- [ ] **Step 6: Create `inference/providers/gemini.py`**

Gemini messages use `role: "user" | "model"` (not "assistant") and `parts` instead of `content`. This provider translates the standard `{"role": "user"/"assistant", "content": "..."}` format that the worker uses.

```python
from __future__ import annotations
from collections.abc import AsyncIterator
import google.generativeai as genai
from .base import InferenceProvider, TextChunk, ToolUse, StreamDone


def _to_gemini_messages(messages: list[dict]) -> list[dict]:
    result = []
    for m in messages:
        role = "model" if m["role"] == "assistant" else m["role"]
        content = m["content"]
        if isinstance(content, str):
            result.append({"role": role, "parts": [content]})
        elif isinstance(content, list):
            # tool_result turn from worker
            result.append({"role": role, "parts": content})
        else:
            result.append({"role": role, "parts": [content]})
    return result


class GeminiProvider(InferenceProvider):
    def __init__(self, api_key: str, model: str, max_tokens: int) -> None:
        genai.configure(api_key=api_key)
        self._model = genai.GenerativeModel(model)
        self._max_tokens = max_tokens

    async def generate(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
    ) -> AsyncIterator[TextChunk | StreamDone]:
        gemini_msgs = _to_gemini_messages(messages)
        kwargs: dict = dict(stream=True)
        if tools:
            kwargs["tools"] = tools

        tool_calls: list[ToolUse] = []
        async for chunk in await self._model.generate_content_async(gemini_msgs, **kwargs):
            if chunk.text:
                yield TextChunk(content=chunk.text)
            for candidate in getattr(chunk, "candidates", []):
                for part in getattr(candidate.content, "parts", []):
                    fc = getattr(part, "function_call", None)
                    if fc and fc.name:
                        import uuid
                        tool_calls.append(
                            ToolUse(
                                call_id=str(uuid.uuid4()),
                                name=fc.name,
                                input=dict(fc.args),
                            )
                        )

        stop_reason = "tool_use" if tool_calls else "end_turn"
        yield StreamDone(stop_reason=stop_reason, tool_calls=tool_calls)
```

- [ ] **Step 7: Create `inference/providers/ollama.py`**

Ollama exposes an OpenAI-compatible API. We use `openai.AsyncOpenAI` pointed at the Ollama base URL.

```python
from __future__ import annotations
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
        messages: list[dict],
        tools: list[dict] | None = None,
    ) -> AsyncIterator[TextChunk | StreamDone]:
        kwargs: dict = dict(
            model=self._model,
            messages=messages,
            max_tokens=self._max_tokens,
            stream=True,
        )
        if tools:
            kwargs["tools"] = tools

        tool_calls: list[ToolUse] = []
        async for chunk in await self._client.chat.completions.create(**kwargs):
            choice = chunk.choices[0]
            delta = choice.delta
            if delta.content:
                yield TextChunk(content=delta.content)
            if hasattr(delta, "tool_calls") and delta.tool_calls:
                for tc in delta.tool_calls:
                    import json
                    tool_calls.append(
                        ToolUse(
                            call_id=tc.id or "",
                            name=tc.function.name,
                            input=json.loads(tc.function.arguments or "{}"),
                        )
                    )

        stop_reason = "tool_use" if tool_calls else "end_turn"
        yield StreamDone(stop_reason=stop_reason, tool_calls=tool_calls)
```

- [ ] **Step 8: Run tests, confirm they pass**

```bash
python3 -m pytest tests/test_providers.py -v
```

Expected: 7 tests PASS.

- [ ] **Step 9: Commit**

```bash
git add inference/providers/ inference/tests/test_providers.py
git commit -m "feat(inference): provider interface + anthropic/gemini/ollama implementations"
```

---

## Task 3: Redis client

**Files:**
- Create: `inference/redis_client.py`
- Create: `inference/tests/test_redis_client.py`

- [ ] **Step 1: Write failing tests**

Create `inference/tests/test_redis_client.py`:

```python
import pytest
import redis.asyncio as aioredis
from gen import belgrade_os_pb2
from redis_client import RedisClient, GROUP, STREAM_TASKS_INBOUND, STREAM_TOOL_CALLS


@pytest.fixture
async def rclient():
    c = RedisClient("redis://localhost:6379")
    try:
        await c._r.ping()
    except Exception as e:
        pytest.skip(f"Redis unavailable: {e}")
    await c.ensure_consumer_groups()
    yield c
    await c._r.aclose()


async def test_ensure_consumer_groups_idempotent(rclient):
    await rclient.ensure_consumer_groups()


async def test_publish_thought_and_subscribe(rclient):
    task_id = "test-pub-thought"
    pubsub = rclient._r.pubsub()
    await pubsub.subscribe(f"sse:{task_id}")
    await pubsub.get_message(timeout=0.1)

    evt = belgrade_os_pb2.ThoughtEvent(
        task_id=task_id,
        user_id="user-1",
        type=belgrade_os_pb2.RESPONSE_CHUNK,
        content="hello inference",
        trace_id="trace-1",
    )
    await rclient.publish_thought(task_id, evt)

    msg = await pubsub.get_message(timeout=1.0)
    assert msg is not None and msg["type"] == "message"
    got = belgrade_os_pb2.ThoughtEvent()
    got.ParseFromString(msg["data"])
    assert got.content == "hello inference"
    await pubsub.aclose()


async def test_add_tool_call_xadd(rclient):
    call = belgrade_os_pb2.ToolCall(
        call_id="call-001",
        task_id="task-001",
        tool_name="calendar:list_events",
        input_json='{"date":"2026-04-28"}',
        trace_id="trace-1",
    )
    await rclient.add_tool_call(call)

    msgs = await rclient._r.xrevrange(STREAM_TOOL_CALLS, "+", "-", count=1)
    assert msgs
    got = belgrade_os_pb2.ToolCall()
    got.ParseFromString(msgs[0][1][b"data"])
    assert got.call_id == "call-001"
    assert got.tool_name == "calendar:list_events"
```

- [ ] **Step 2: Run tests, confirm they fail**

```bash
python3 -m pytest tests/test_redis_client.py -v
```

Expected: `ModuleNotFoundError: No module named 'redis_client'`

- [ ] **Step 3: Create `inference/redis_client.py`**

```python
import redis.asyncio as aioredis
from gen import belgrade_os_pb2

STREAM_TASKS_INBOUND = "tasks:inbound"
STREAM_TOOL_RESULTS = "tasks:tool_results"
STREAM_TOOL_CALLS = "tasks:tool_calls"
GROUP = "inference-controllers"


class RedisClient:
    def __init__(self, url: str) -> None:
        self._r = aioredis.from_url(url, decode_responses=False)

    async def ensure_consumer_groups(self) -> None:
        for stream in (STREAM_TASKS_INBOUND, STREAM_TOOL_RESULTS):
            try:
                await self._r.xgroup_create(stream, GROUP, id="$", mkstream=True)
            except aioredis.ResponseError as exc:
                if "BUSYGROUP" not in str(exc):
                    raise

    async def read_task(self, consumer_id: str) -> tuple[str, belgrade_os_pb2.Task]:
        while True:
            result = await self._r.xreadgroup(
                groupname=GROUP,
                consumername=consumer_id,
                streams={STREAM_TASKS_INBOUND: ">"},
                count=1,
                block=5_000,
            )
            if not result:
                continue
            _, entries = result[0]
            msg_id, fields = entries[0]
            task = belgrade_os_pb2.Task()
            task.ParseFromString(fields[b"data"])
            return msg_id.decode(), task

    async def ack_task(self, msg_id: str) -> None:
        await self._r.xack(STREAM_TASKS_INBOUND, GROUP, msg_id)

    async def publish_thought(
        self, task_id: str, event: belgrade_os_pb2.ThoughtEvent
    ) -> None:
        await self._r.publish(f"sse:{task_id}", event.SerializeToString())

    async def add_tool_call(self, call: belgrade_os_pb2.ToolCall) -> None:
        await self._r.xadd(STREAM_TOOL_CALLS, {"data": call.SerializeToString()})

    async def read_tool_result(
        self, task_id: str, call_id: str, consumer_id: str
    ) -> belgrade_os_pb2.ToolResult:
        while True:
            result = await self._r.xreadgroup(
                groupname=GROUP,
                consumername=consumer_id,
                streams={STREAM_TOOL_RESULTS: ">"},
                count=1,
                block=30_000,
            )
            if not result:
                continue
            _, entries = result[0]
            msg_id, fields = entries[0]
            tr = belgrade_os_pb2.ToolResult()
            tr.ParseFromString(fields[b"data"])
            await self._r.xack(STREAM_TOOL_RESULTS, GROUP, msg_id.decode())
            if tr.task_id == task_id and tr.call_id == call_id:
                return tr
```

- [ ] **Step 4: Run tests, confirm they pass**

```bash
python3 -m pytest tests/test_redis_client.py -v
```

Expected: 3 tests PASS (or SKIP if Redis unavailable).

- [ ] **Step 5: Commit**

```bash
git add inference/redis_client.py inference/tests/test_redis_client.py
git commit -m "feat(inference): redis client"
```

---

## Task 4: Worker — process_task tool loop

**Files:**
- Create: `inference/worker.py`
- Create: `inference/tests/test_worker.py`

- [ ] **Step 1: Write failing tests**

Create `inference/tests/test_worker.py`:

```python
import json
from unittest.mock import AsyncMock, MagicMock
import pytest
from gen import belgrade_os_pb2
from providers.base import TextChunk, ToolUse, StreamDone
from worker import process_task


def _make_task(task_id="t1", user_id="u1", prompt="hello", trace_id="tr1"):
    t = belgrade_os_pb2.Task()
    t.task_id = task_id
    t.user_id = user_id
    t.prompt = prompt
    t.trace_id = trace_id
    return t


async def _events(*items):
    for item in items:
        yield item


async def test_publishes_response_chunks_and_done():
    task = _make_task()
    mock_redis = AsyncMock()
    mock_provider = MagicMock()
    mock_provider.generate.return_value = _events(
        TextChunk("Hello"), TextChunk(" world"), StreamDone("end_turn")
    )

    await process_task(task, mock_redis, mock_provider)

    published = [call.args for call in mock_redis.publish_thought.await_args_list]
    chunks = [e for _, e in published if e.type == belgrade_os_pb2.RESPONSE_CHUNK]
    dones  = [e for _, e in published if e.type == belgrade_os_pb2.DONE]

    assert [c.content for c in chunks] == ["Hello", " world"]
    assert len(dones) == 1
    assert dones[0].task_id == "t1"
    assert dones[0].trace_id == "tr1"


async def test_all_events_carry_task_metadata():
    task = _make_task(task_id="t2", user_id="u2", trace_id="tr2")
    mock_redis = AsyncMock()
    mock_provider = MagicMock()
    mock_provider.generate.return_value = _events(
        TextChunk("hi"), StreamDone("end_turn")
    )

    await process_task(task, mock_redis, mock_provider)

    for c in mock_redis.publish_thought.await_args_list:
        _, evt = c.args
        assert evt.task_id == "t2"
        assert evt.user_id == "u2"
        assert evt.trace_id == "tr2"


async def test_tool_use_dispatches_and_continues():
    task = _make_task()
    mock_redis = AsyncMock()

    tool_result = belgrade_os_pb2.ToolResult()
    tool_result.call_id = "call-abc"
    tool_result.task_id = "t1"
    tool_result.success = True
    tool_result.output_json = '["meeting at 9am"]'
    mock_redis.read_tool_result = AsyncMock(return_value=tool_result)

    call_count = 0

    async def fake_generate(messages, tools=None):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            yield StreamDone("tool_use", [ToolUse("call-abc", "calendar:list_events", {"date": "2026-04-28"})])
        else:
            yield TextChunk("Done.")
            yield StreamDone("end_turn")

    mock_provider = MagicMock()
    mock_provider.generate.side_effect = fake_generate

    await process_task(task, mock_redis, mock_provider)

    mock_redis.add_tool_call.assert_awaited_once()
    dispatched: belgrade_os_pb2.ToolCall = mock_redis.add_tool_call.await_args.args[0]
    assert dispatched.call_id == "call-abc"
    assert dispatched.tool_name == "calendar:list_events"
    assert json.loads(dispatched.input_json) == {"date": "2026-04-28"}

    mock_redis.read_tool_result.assert_awaited_once_with("t1", "call-abc")

    published = [c.args for c in mock_redis.publish_thought.await_args_list]
    dones = [e for _, e in published if e.type == belgrade_os_pb2.DONE]
    assert len(dones) == 1


async def test_exception_publishes_error_event():
    task = _make_task()
    mock_redis = AsyncMock()
    mock_provider = MagicMock()
    mock_provider.generate.side_effect = Exception("API timeout")

    await process_task(task, mock_redis, mock_provider)

    published = [c.args for c in mock_redis.publish_thought.await_args_list]
    errors = [e for _, e in published if e.type == belgrade_os_pb2.ERROR]
    assert len(errors) == 1
    assert "API timeout" in errors[0].content
```

- [ ] **Step 2: Run tests, confirm they fail**

```bash
python3 -m pytest tests/test_worker.py -v
```

Expected: `ModuleNotFoundError: No module named 'worker'`

- [ ] **Step 3: Create `inference/worker.py`**

```python
import json
import logging
from gen import belgrade_os_pb2
from providers.base import InferenceProvider, TextChunk, StreamDone
from redis_client import RedisClient

log = logging.getLogger(__name__)


async def process_task(
    task: belgrade_os_pb2.Task,
    redis: RedisClient,
    provider: InferenceProvider,
) -> None:
    messages: list[dict] = [{"role": "user", "content": task.prompt}]
    tools: list[dict] = []  # populated by Capability Bridge in a future milestone

    try:
        while True:
            done: StreamDone | None = None

            async for event in provider.generate(messages, tools or None):
                if isinstance(event, TextChunk):
                    await redis.publish_thought(
                        task.task_id,
                        belgrade_os_pb2.ThoughtEvent(
                            task_id=task.task_id,
                            user_id=task.user_id,
                            trace_id=task.trace_id,
                            type=belgrade_os_pb2.RESPONSE_CHUNK,
                            content=event.content,
                        ),
                    )
                elif isinstance(event, StreamDone):
                    done = event

            if done is None or done.stop_reason == "end_turn":
                break

            if done.stop_reason == "tool_use":
                tool_results_content: list[dict] = []
                for tu in done.tool_calls:
                    call = belgrade_os_pb2.ToolCall(
                        call_id=tu.call_id,
                        task_id=task.task_id,
                        tool_name=tu.name,
                        input_json=json.dumps(tu.input),
                        trace_id=task.trace_id,
                    )
                    await redis.add_tool_call(call)
                    result = await redis.read_tool_result(task.task_id, tu.call_id)
                    tool_results_content.append({
                        "type": "tool_result",
                        "tool_use_id": tu.call_id,
                        "content": result.output_json if result.success else result.error,
                    })

                messages.append({"role": "assistant", "content": []})
                messages.append({"role": "user", "content": tool_results_content})

    except Exception as exc:
        log.exception("process_task failed for %s", task.task_id)
        await redis.publish_thought(
            task.task_id,
            belgrade_os_pb2.ThoughtEvent(
                task_id=task.task_id,
                user_id=task.user_id,
                trace_id=task.trace_id,
                type=belgrade_os_pb2.ERROR,
                content=str(exc),
            ),
        )
        return

    await redis.publish_thought(
        task.task_id,
        belgrade_os_pb2.ThoughtEvent(
            task_id=task.task_id,
            user_id=task.user_id,
            trace_id=task.trace_id,
            type=belgrade_os_pb2.DONE,
            content="",
        ),
    )
```

- [ ] **Step 4: Run tests, confirm they pass**

```bash
python3 -m pytest tests/test_worker.py -v
```

Expected: 4 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add inference/worker.py inference/tests/test_worker.py
git commit -m "feat(inference): worker — provider-agnostic tool loop"
```

---

## Task 5: Main — provider factory + consumer loop

**Files:**
- Create: `inference/main.py`
- Create: `inference/tests/test_main.py`

- [ ] **Step 1: Write failing tests**

Create `inference/tests/test_main.py`:

```python
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
import pytest
from gen import belgrade_os_pb2


async def test_consumer_loop_calls_process_task_then_acks():
    task = belgrade_os_pb2.Task(task_id="t1", user_id="u1", prompt="test", trace_id="tr1")
    mock_redis = AsyncMock()
    mock_provider = MagicMock()
    read_count = 0

    async def fake_read(consumer_id):
        nonlocal read_count
        read_count += 1
        if read_count == 1:
            return "msg-001", task
        await asyncio.sleep(9999)

    mock_redis.read_task.side_effect = fake_read

    from main import _consumer_loop
    with patch("main.process_task", new_callable=AsyncMock) as mock_process:
        try:
            await asyncio.wait_for(
                _consumer_loop(mock_redis, mock_provider, consumer_id="w1"),
                timeout=1.0,
            )
        except asyncio.TimeoutError:
            pass

    mock_process.assert_awaited_once()
    assert mock_process.await_args.args[0].task_id == "t1"
    mock_redis.ack_task.assert_awaited_once_with("msg-001")


async def test_consumer_loop_acks_even_after_error():
    task = belgrade_os_pb2.Task(task_id="t2", user_id="u1", prompt="crash", trace_id="tr2")
    mock_redis = AsyncMock()
    mock_provider = MagicMock()
    read_count = 0

    async def fake_read(consumer_id):
        nonlocal read_count
        read_count += 1
        if read_count == 1:
            return "msg-002", task
        await asyncio.sleep(9999)

    mock_redis.read_task.side_effect = fake_read

    from main import _consumer_loop
    with patch("main.process_task", new_callable=AsyncMock, side_effect=Exception("boom")):
        try:
            await asyncio.wait_for(
                _consumer_loop(mock_redis, mock_provider, consumer_id="w1"),
                timeout=1.0,
            )
        except asyncio.TimeoutError:
            pass

    mock_redis.ack_task.assert_awaited_once_with("msg-002")


def test_build_provider_anthropic():
    from main import _build_provider
    from providers.anthropic import AnthropicProvider
    from config import Config
    cfg = Config(provider="anthropic", model="claude-opus-4-7", anthropic_api_key="k")
    assert isinstance(_build_provider(cfg), AnthropicProvider)


def test_build_provider_gemini():
    from main import _build_provider
    from providers.gemini import GeminiProvider
    from config import Config
    with patch("providers.gemini.genai"):
        cfg = Config(provider="gemini", model="gemini-1.5-pro", google_api_key="gk")
        assert isinstance(_build_provider(cfg), GeminiProvider)


def test_build_provider_ollama():
    from main import _build_provider
    from providers.ollama import OllamaProvider
    from config import Config
    cfg = Config(provider="ollama", model="qwen2.5-coder")
    assert isinstance(_build_provider(cfg), OllamaProvider)
```

- [ ] **Step 2: Run tests, confirm they fail**

```bash
python3 -m pytest tests/test_main.py -v
```

Expected: `ModuleNotFoundError: No module named 'main'`

- [ ] **Step 3: Create `inference/main.py`**

```python
import asyncio
import logging
from config import Config, load_config
from redis_client import RedisClient
from providers.base import InferenceProvider
from providers.anthropic import AnthropicProvider
from providers.gemini import GeminiProvider
from providers.ollama import OllamaProvider
from worker import process_task

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)


def _build_provider(cfg: Config) -> InferenceProvider:
    if cfg.provider == "anthropic":
        return AnthropicProvider(
            api_key=cfg.anthropic_api_key,
            model=cfg.model,
            max_tokens=cfg.max_tokens,
        )
    if cfg.provider == "gemini":
        return GeminiProvider(
            api_key=cfg.google_api_key,
            model=cfg.model,
            max_tokens=cfg.max_tokens,
        )
    if cfg.provider == "ollama":
        return OllamaProvider(
            base_url=cfg.ollama_base_url,
            model=cfg.model,
            max_tokens=cfg.max_tokens,
        )
    raise ValueError(f"Unknown provider: {cfg.provider}")


async def _consumer_loop(
    redis: RedisClient,
    provider: InferenceProvider,
    consumer_id: str,
) -> None:
    while True:
        msg_id, task = await redis.read_task(consumer_id)
        log.info("processing task %s (msg %s)", task.task_id, msg_id)
        try:
            await process_task(task, redis, provider)
        except Exception:
            log.exception("unhandled error for task %s", task.task_id)
        finally:
            await redis.ack_task(msg_id)


async def main() -> None:
    cfg = load_config()
    redis = RedisClient(cfg.redis_url)
    provider = _build_provider(cfg)

    await redis.ensure_consumer_groups()
    consumer_id = cfg.effective_consumer_id
    log.info("inference controller starting provider=%s model=%s consumer=%s",
             cfg.provider, cfg.model, consumer_id)

    await _consumer_loop(redis, provider, consumer_id)


if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 4: Run tests, confirm they pass**

```bash
python3 -m pytest tests/test_main.py -v
```

Expected: 5 tests PASS.

- [ ] **Step 5: Run full test suite**

```bash
python3 -m pytest -v
```

Expected: all tests PASS (Redis tests SKIP if Redis not running).

- [ ] **Step 6: Commit**

```bash
git add inference/main.py inference/tests/test_main.py
git commit -m "feat(inference): provider factory + consumer loop"
```

---

## Self-Review Checklist

1. **Spec coverage:**
   - [x] `InferenceProvider` ABC with `generate()` → `TextChunk | StreamDone` — Task 2
   - [x] `AnthropicProvider` — Task 2
   - [x] `GeminiProvider` with message format translation — Task 2
   - [x] `OllamaProvider` via OpenAI-compatible API — Task 2
   - [x] Config with `provider` required (no default), credential validator — Task 1
   - [x] `.env.example` template — Task 1
   - [x] Redis XREADGROUP/XACK/PUBLISH/XADD — Task 3
   - [x] Worker iterates `TextChunk | StreamDone`, no provider-specific logic — Task 4
   - [x] Tool loop: dispatches `ToolCall`, waits for `ToolResult`, continues — Task 4
   - [x] Error handling: publishes `ERROR` ThoughtEvent — Task 4
   - [x] Provider factory `_build_provider(cfg)` — Task 5
   - [x] Consumer loop ACKs even on exception — Task 5

2. **Placeholder scan:** None.

3. **Type consistency:**
   - `process_task(task, redis, provider)` — defined Task 4, called Task 5 ✓
   - `redis.read_tool_result(task_id, call_id)` — 2 args (no consumer_id) ✓
   - `StreamDone.tool_calls: list[ToolUse]` — used in worker Task 4 ✓
   - `_build_provider(cfg) -> InferenceProvider` — defined + tested Task 5 ✓
