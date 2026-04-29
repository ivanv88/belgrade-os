# Resource Runner Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the Resource Runner — a Python service that reads `ToolCall` protos from `tasks:tool_calls`, dispatches each to the Capability Bridge via HTTP, and writes `ToolResult` protos back to `tasks:tool_results`.

**Architecture:** The runner is a pure transport worker: read one stream, call one HTTP endpoint, write to another stream. It manages a Redis lease per in-flight call so the system can detect stuck workers. The bridge is the only authority on tool execution; the runner never interprets tool names or payloads.

**Tech Stack:** Python 3.9+, `redis-py 5` (asyncio), `httpx`, `pydantic-settings 2`, `protobuf 4`, `pytest`, `pytest-asyncio`

---

## Bridge HTTP API Contract

The runner depends on a single bridge endpoint. The bridge is a separate Rust service — document the contract here so both sides can be built to it.

### Extendability principle

All endpoints live under `/v1/` to allow future versioning. Each feature gets its own URL namespace so additions don't disturb existing clients:

| Endpoint | Consumer | Status | Purpose |
|---|---|---|---|
| `POST /v1/execute` | Runner | **this plan** | Execute a tool call |
| `GET /v1/tools` | Inference Controller | future | List registered tools |
| `GET /v1/notifications/provider` | Gateway | future | ntfy.sh / push config |
| `GET /v1/manifests` | (TBD) | future | App manifest metadata |
| WS `/v1/ui/events` | (TBD) | future | Real-time UI stream |

The bridge registers tools via `AppToolsRegistration` proto messages (see `proto/belgrade_os.proto`). The runner never calls a registration endpoint — that is the apps' responsibility at startup.

### `POST /v1/execute`

**Request:**
```json
{
  "call_id":    "550e8400-e29b-41d4-a716-446655440000",
  "task_id":    "550e8400-e29b-41d4-a716-446655440001",
  "tool_name":  "shopping:add_item",
  "input_json": "{\"item\": \"milk\"}",
  "trace_id":   "550e8400-e29b-41d4-a716-446655440002"
}
```

**Response 200 — success:**
```json
{
  "call_id":     "...",
  "task_id":     "...",
  "success":     true,
  "output_json": "{\"added\": true}",
  "error":       ""
}
```

**Response 200 — logical failure (unknown tool, app crash):**
```json
{
  "call_id":     "...",
  "task_id":     "...",
  "success":     false,
  "output_json": "",
  "error":       "tool not found: shopping:add_item"
}
```

**Non-200 / connection error:** Runner treats as `success=false, error="bridge error: <detail>"`. `duration_ms` is always measured by the runner (start-to-response wall clock), never included in the bridge response.

---

## File Map

```
runner/
├── config.py               — Config: redis_url, bridge_url, consumer_group, worker_id,
│                             lease_ttl_s, tool_timeout_s; effective_worker_id property
├── redis_client.py         — RedisClient: read_tool_call, ack_tool_call, write_tool_result,
│                             set_lease, delete_lease, ensure_consumer_group, close
├── bridge_client.py        — BridgeClient: persistent httpx client, execute(ToolCall) → ToolResult
├── worker.py               — process_tool_call(call, redis, bridge, worker_id, lease_ttl_s)
├── main.py                 — CONSUMER_GROUP constant, _consumer_loop, main() entry point
├── requirements.txt        — add httpx==0.27.2, pydantic-settings==2.11.0 to existing deps
├── requirements-dev.txt    — add pytest-asyncio>=0.23 to existing deps
├── pytest.ini              — asyncio_mode = auto, asyncio_default_fixture_loop_scope = function
└── tests/
    ├── test_config.py
    ├── test_redis_client.py
    ├── test_bridge_client.py
    ├── test_worker.py
    └── test_main.py
```

**Key invariant:** `write_tool_result` writes **two** fields to the Redis stream entry — `data` (ToolResult proto bytes) AND `task_id` (string). This is required because the Inference Controller's `read_tool_result` filters by `b"task_id"` in stream fields to match results to the calling task (see `inference/redis_client.py:81`).

---

## Task 1: Config, pytest setup, requirements

**Files:**
- Modify: `runner/requirements.txt`
- Modify: `runner/requirements-dev.txt`
- Create: `runner/pytest.ini`
- Create: `runner/config.py`
- Create: `runner/tests/test_config.py`

- [ ] **Step 1: Write failing tests**

Create `runner/tests/test_config.py`:

```python
from __future__ import annotations
import socket
import pytest
from config import Config


def test_defaults():
    c = Config(_env_file=None)
    assert c.redis_url == "redis://localhost:6379"
    assert c.bridge_url == "http://localhost:8081"
    assert c.consumer_group == "tool-runners"
    assert c.lease_ttl_s == 60
    assert c.tool_timeout_s == 30


def test_effective_worker_id_uses_hostname_when_empty():
    c = Config(worker_id="", _env_file=None)
    assert c.effective_worker_id == socket.gethostname()


def test_effective_worker_id_explicit():
    c = Config(worker_id="runner-1", _env_file=None)
    assert c.effective_worker_id == "runner-1"


def test_from_env(monkeypatch):
    monkeypatch.setenv("REDIS_URL", "redis://redis:6379")
    monkeypatch.setenv("BRIDGE_URL", "http://bridge:8081")
    monkeypatch.setenv("WORKER_ID", "runner-42")
    monkeypatch.setenv("TOOL_TIMEOUT_S", "10")
    c = Config(_env_file=None)
    assert c.redis_url == "redis://redis:6379"
    assert c.bridge_url == "http://bridge:8081"
    assert c.effective_worker_id == "runner-42"
    assert c.tool_timeout_s == 10
```

- [ ] **Step 2: Run tests, confirm they fail**

```bash
cd runner && python3 -m pytest tests/test_config.py -v
```

Expected: `ModuleNotFoundError: No module named 'config'`

- [ ] **Step 3: Update requirements files**

`runner/requirements.txt` — full replacement:
```
grpcio==1.64.1
protobuf==4.25.3
redis==5.0.4
httpx==0.27.2
pydantic-settings==2.11.0
```

`runner/requirements-dev.txt` — full replacement:
```
-r requirements.txt
grpcio-tools==1.64.1
pytest==8.2.2
pytest-asyncio>=0.23
```

- [ ] **Step 4: Install deps**

```bash
cd runner && pip3 install -r requirements-dev.txt
```

- [ ] **Step 5: Create `runner/pytest.ini`**

```ini
[pytest]
asyncio_mode = auto
asyncio_default_fixture_loop_scope = function
testpaths = tests
pythonpath = .
```

- [ ] **Step 6: Create `runner/config.py`**

```python
from __future__ import annotations
import socket
from pydantic_settings import BaseSettings


class Config(BaseSettings):
    redis_url: str = "redis://localhost:6379"
    bridge_url: str = "http://localhost:8081"
    consumer_group: str = "tool-runners"
    worker_id: str = ""
    lease_ttl_s: int = 60
    tool_timeout_s: int = 30
    model_config = {"env_file": ".env"}

    @property
    def effective_worker_id(self) -> str:
        return self.worker_id or socket.gethostname()


def load_config() -> Config:
    return Config()
```

- [ ] **Step 7: Run tests, confirm they pass**

```bash
cd runner && python3 -m pytest tests/test_config.py -v
```

Expected: 4 tests PASS.

- [ ] **Step 8: Confirm existing proto tests still pass**

```bash
cd runner && python3 -m pytest tests/test_proto.py -v
```

Expected: 6 tests PASS.

- [ ] **Step 9: Commit**

```bash
git add runner/requirements.txt runner/requirements-dev.txt runner/pytest.ini \
        runner/config.py runner/tests/test_config.py
git commit -m "feat(runner): config + pytest setup"
```

---

## Task 2: Redis client

**Files:**
- Create: `runner/redis_client.py`
- Create: `runner/tests/test_redis_client.py`

- [ ] **Step 1: Write failing tests**

Create `runner/tests/test_redis_client.py`:

```python
from __future__ import annotations
from unittest.mock import AsyncMock, MagicMock, patch
import pytest
import redis.exceptions
from redis_client import RedisClient, TOOL_CALLS_STREAM, TOOL_RESULTS_STREAM, LEASE_KEY_PREFIX


def _make_client():
    with patch("redis_client.aioredis.from_url") as mock_from_url:
        mock_redis = MagicMock()
        mock_from_url.return_value = mock_redis
        client = RedisClient("redis://localhost:6379")
    return client, mock_redis


async def test_read_tool_call_returns_message():
    client, mock_redis = _make_client()
    mock_redis.xreadgroup = AsyncMock(return_value=[
        (b"tasks:tool_calls", [(b"1-0", {b"data": b"proto-bytes"})])
    ])
    result = await client.read_tool_call("g", "w1")
    assert result == ("1-0", b"proto-bytes")


async def test_read_tool_call_returns_none_on_empty():
    client, mock_redis = _make_client()
    mock_redis.xreadgroup = AsyncMock(return_value=[])
    result = await client.read_tool_call("g", "w1")
    assert result is None


async def test_ack_tool_call_calls_xack():
    client, mock_redis = _make_client()
    mock_redis.xack = AsyncMock()
    await client.ack_tool_call("g", "1-0")
    mock_redis.xack.assert_awaited_once_with(TOOL_CALLS_STREAM, "g", "1-0")


async def test_write_tool_result_includes_task_id_field():
    client, mock_redis = _make_client()
    mock_redis.xadd = AsyncMock()
    await client.write_tool_result("task-1", b"result-bytes")
    mock_redis.xadd.assert_awaited_once_with(
        TOOL_RESULTS_STREAM,
        {"data": b"result-bytes", "task_id": "task-1"},
    )


async def test_set_lease_calls_set_with_ex():
    client, mock_redis = _make_client()
    mock_redis.set = AsyncMock()
    await client.set_lease("worker-1", b"lease-bytes", ttl_s=60)
    mock_redis.set.assert_awaited_once_with(
        f"{LEASE_KEY_PREFIX}:worker-1", b"lease-bytes", ex=60
    )


async def test_delete_lease_calls_delete():
    client, mock_redis = _make_client()
    mock_redis.delete = AsyncMock()
    await client.delete_lease("worker-1")
    mock_redis.delete.assert_awaited_once_with(f"{LEASE_KEY_PREFIX}:worker-1")


async def test_ensure_consumer_group_creates_group():
    client, mock_redis = _make_client()
    mock_redis.xgroup_create = AsyncMock()
    await client.ensure_consumer_group()
    mock_redis.xgroup_create.assert_awaited_once_with(
        TOOL_CALLS_STREAM, "tool-runners", id="0", mkstream=True
    )


async def test_ensure_consumer_group_ignores_busygroup():
    client, mock_redis = _make_client()
    mock_redis.xgroup_create = AsyncMock(
        side_effect=redis.exceptions.ResponseError("BUSYGROUP Consumer Group name already exists")
    )
    await client.ensure_consumer_group()  # must not raise
```

- [ ] **Step 2: Run tests, confirm they fail**

```bash
cd runner && python3 -m pytest tests/test_redis_client.py -v
```

Expected: `ModuleNotFoundError: No module named 'redis_client'`

- [ ] **Step 3: Create `runner/redis_client.py`**

```python
from __future__ import annotations
from typing import Optional
import redis.asyncio as aioredis
import redis.exceptions

TOOL_CALLS_STREAM   = "tasks:tool_calls"
TOOL_RESULTS_STREAM = "tasks:tool_results"
LEASE_KEY_PREFIX    = "lease"
_DEFAULT_GROUP      = "tool-runners"


class RedisClient:
    def __init__(self, url: str) -> None:
        self._redis: aioredis.Redis = aioredis.from_url(url, decode_responses=False)

    async def read_tool_call(
        self, consumer_group: str, consumer_id: str
    ) -> Optional[tuple[str, bytes]]:
        """XREADGROUP from tasks:tool_calls. Returns (message_id, proto_bytes) or None."""
        results = await self._redis.xreadgroup(
            groupname=consumer_group,
            consumername=consumer_id,
            streams={TOOL_CALLS_STREAM: ">"},
            count=1,
            block=2000,
        )
        if not results:
            return None
        _stream, messages = results[0]
        if not messages:
            return None
        msg_id, fields = messages[0]
        message_id = msg_id.decode() if isinstance(msg_id, bytes) else msg_id
        return message_id, fields[b"data"]

    async def ack_tool_call(self, consumer_group: str, message_id: str) -> None:
        """XACK tasks:tool_calls."""
        await self._redis.xack(TOOL_CALLS_STREAM, consumer_group, message_id)

    async def write_tool_result(self, task_id: str, proto_bytes: bytes) -> None:
        """XADD tasks:tool_results with both data and task_id fields.

        The task_id field is required by the Inference Controller's read_tool_result,
        which filters stream entries by b"task_id" to match results to their task.
        """
        await self._redis.xadd(
            TOOL_RESULTS_STREAM,
            {"data": proto_bytes, "task_id": task_id},
        )

    async def set_lease(self, worker_id: str, proto_bytes: bytes, ttl_s: int) -> None:
        """SET lease:{worker_id} to serialised WorkerLease proto with TTL."""
        await self._redis.set(f"{LEASE_KEY_PREFIX}:{worker_id}", proto_bytes, ex=ttl_s)

    async def delete_lease(self, worker_id: str) -> None:
        """DEL lease:{worker_id}."""
        await self._redis.delete(f"{LEASE_KEY_PREFIX}:{worker_id}")

    async def ensure_consumer_group(self) -> None:
        """XGROUP CREATE tasks:tool_calls tool-runners MKSTREAM. Ignores BUSYGROUP."""
        try:
            await self._redis.xgroup_create(
                TOOL_CALLS_STREAM, _DEFAULT_GROUP, id="0", mkstream=True
            )
        except redis.exceptions.ResponseError as exc:
            if "BUSYGROUP" not in str(exc):
                raise

    async def close(self) -> None:
        await self._redis.aclose()
```

- [ ] **Step 4: Run tests, confirm they pass**

```bash
cd runner && python3 -m pytest tests/test_redis_client.py -v
```

Expected: 8 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add runner/redis_client.py runner/tests/test_redis_client.py
git commit -m "feat(runner): Redis client — tool_calls consumer, tool_results producer, lease"
```

---

## Task 3: Bridge client

**Files:**
- Create: `runner/bridge_client.py`
- Create: `runner/tests/test_bridge_client.py`

The client holds a single persistent `httpx.AsyncClient`. All HTTP and connection errors are caught and returned as a failed `ToolResult` — the caller never has to handle exceptions.

- [ ] **Step 1: Write failing tests**

Create `runner/tests/test_bridge_client.py`:

```python
from __future__ import annotations
import pytest
import httpx
from unittest.mock import AsyncMock, MagicMock, patch
from gen import belgrade_os_pb2
from bridge_client import BridgeClient


def _make_call() -> belgrade_os_pb2.ToolCall:
    c = belgrade_os_pb2.ToolCall()
    c.call_id = "c1"; c.task_id = "t1"
    c.tool_name = "shopping:add_item"
    c.input_json = '{"item":"milk"}'
    c.trace_id = "tr1"
    return c


def _make_client_with_mock():
    with patch("bridge_client.httpx.AsyncClient") as MockClient:
        mock_http = AsyncMock()
        MockClient.return_value = mock_http
        client = BridgeClient(base_url="http://bridge:8081", timeout_s=30)
    return client, mock_http


async def test_execute_success():
    client, mock_http = _make_client_with_mock()
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {
        "call_id": "c1", "task_id": "t1",
        "success": True, "output_json": '{"added":true}', "error": ""
    }
    mock_http.post = AsyncMock(return_value=mock_resp)

    result = await client.execute(_make_call())

    assert result.call_id == "c1"
    assert result.task_id == "t1"
    assert result.success is True
    assert result.output_json == '{"added":true}'
    assert result.error == ""


async def test_execute_logical_failure():
    client, mock_http = _make_client_with_mock()
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {
        "call_id": "c1", "task_id": "t1",
        "success": False, "output_json": "",
        "error": "tool not found: shopping:add_item"
    }
    mock_http.post = AsyncMock(return_value=mock_resp)

    result = await client.execute(_make_call())

    assert result.success is False
    assert "shopping:add_item" in result.error


async def test_execute_connection_error_returns_failed_result():
    client, mock_http = _make_client_with_mock()
    mock_http.post = AsyncMock(side_effect=httpx.ConnectError("refused"))

    result = await client.execute(_make_call())

    assert result.call_id == "c1"
    assert result.task_id == "t1"
    assert result.success is False
    assert "bridge error" in result.error


async def test_execute_sends_correct_payload():
    client, mock_http = _make_client_with_mock()
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {
        "call_id": "c1", "task_id": "t1",
        "success": True, "output_json": "[]", "error": ""
    }
    mock_http.post = AsyncMock(return_value=mock_resp)

    await client.execute(_make_call())

    post_kwargs = mock_http.post.call_args
    assert post_kwargs.args[0] == "/v1/execute"
    sent = post_kwargs.kwargs["json"]
    assert sent["call_id"] == "c1"
    assert sent["tool_name"] == "shopping:add_item"
    assert sent["input_json"] == '{"item":"milk"}'
    assert sent["trace_id"] == "tr1"
```

- [ ] **Step 2: Run tests, confirm they fail**

```bash
cd runner && python3 -m pytest tests/test_bridge_client.py -v
```

Expected: `ModuleNotFoundError: No module named 'bridge_client'`

- [ ] **Step 3: Create `runner/bridge_client.py`**

```python
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
```

- [ ] **Step 4: Run tests, confirm they pass**

```bash
cd runner && python3 -m pytest tests/test_bridge_client.py -v
```

Expected: 4 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add runner/bridge_client.py runner/tests/test_bridge_client.py
git commit -m "feat(runner): bridge HTTP client for POST /v1/execute"
```

---

## Task 4: Worker

**Files:**
- Create: `runner/worker.py`
- Create: `runner/tests/test_worker.py`

The worker sets a lease before calling the bridge and deletes it in a `finally` block. It always writes a result — even when the bridge returns an error — so the Inference Controller is never stuck waiting.

- [ ] **Step 1: Write failing tests**

Create `runner/tests/test_worker.py`:

```python
from __future__ import annotations
from unittest.mock import AsyncMock
import pytest
from gen import belgrade_os_pb2
from worker import process_tool_call


def _make_call(call_id="c1", task_id="t1") -> belgrade_os_pb2.ToolCall:
    c = belgrade_os_pb2.ToolCall()
    c.call_id = call_id; c.task_id = task_id
    c.tool_name = "shop:add"; c.input_json = "{}"
    c.trace_id = "tr1"
    return c


def _make_bridge_result(call_id="c1", task_id="t1", success=True) -> belgrade_os_pb2.ToolResult:
    r = belgrade_os_pb2.ToolResult()
    r.call_id = call_id; r.task_id = task_id
    r.success = success; r.output_json = '{"ok":true}'
    return r


async def test_sets_lease_with_correct_fields():
    mock_redis = AsyncMock()
    mock_bridge = AsyncMock()
    mock_bridge.execute.return_value = _make_bridge_result()

    await process_tool_call(_make_call(), mock_redis, mock_bridge, worker_id="w1", lease_ttl_s=60)

    mock_redis.set_lease.assert_awaited_once()
    lease_bytes = mock_redis.set_lease.await_args.args[1]
    ttl = mock_redis.set_lease.await_args.args[2]
    lease = belgrade_os_pb2.WorkerLease()
    lease.ParseFromString(lease_bytes)
    assert lease.worker_id == "w1"
    assert lease.task_id == "t1"
    assert lease.call_id == "c1"
    assert lease.expires_at_ms > lease.leased_at_ms
    assert ttl == 60


async def test_writes_result_with_task_id_and_duration():
    mock_redis = AsyncMock()
    mock_bridge = AsyncMock()
    mock_bridge.execute.return_value = _make_bridge_result(task_id="t1")

    await process_tool_call(_make_call(task_id="t1"), mock_redis, mock_bridge, worker_id="w1", lease_ttl_s=60)

    mock_redis.write_tool_result.assert_awaited_once()
    task_id_arg = mock_redis.write_tool_result.await_args.args[0]
    result_bytes = mock_redis.write_tool_result.await_args.args[1]
    assert task_id_arg == "t1"
    result = belgrade_os_pb2.ToolResult()
    result.ParseFromString(result_bytes)
    assert result.success is True
    assert result.duration_ms >= 0


async def test_deletes_lease_after_success():
    mock_redis = AsyncMock()
    mock_bridge = AsyncMock()
    mock_bridge.execute.return_value = _make_bridge_result()

    await process_tool_call(_make_call(), mock_redis, mock_bridge, worker_id="w1", lease_ttl_s=60)

    mock_redis.delete_lease.assert_awaited_once_with("w1")


async def test_deletes_lease_even_on_bridge_error():
    mock_redis = AsyncMock()
    mock_bridge = AsyncMock()
    error_result = belgrade_os_pb2.ToolResult()
    error_result.call_id = "c1"; error_result.task_id = "t1"
    error_result.success = False; error_result.error = "bridge error: timeout"
    mock_bridge.execute.return_value = error_result

    await process_tool_call(_make_call(), mock_redis, mock_bridge, worker_id="w1", lease_ttl_s=60)

    mock_redis.delete_lease.assert_awaited_once_with("w1")
    mock_redis.write_tool_result.assert_awaited_once()


async def test_result_written_before_lease_deleted():
    """write_tool_result must precede delete_lease so the inference controller
    can never read an ACK with no result in the stream."""
    call_order = []
    mock_redis = AsyncMock()
    mock_redis.set_lease = AsyncMock(side_effect=lambda *a, **kw: call_order.append("set"))
    mock_redis.write_tool_result = AsyncMock(side_effect=lambda *a, **kw: call_order.append("write"))
    mock_redis.delete_lease = AsyncMock(side_effect=lambda *a, **kw: call_order.append("delete"))
    mock_bridge = AsyncMock()
    mock_bridge.execute.return_value = _make_bridge_result()

    await process_tool_call(_make_call(), mock_redis, mock_bridge, worker_id="w1", lease_ttl_s=60)

    assert call_order == ["set", "write", "delete"]
```

- [ ] **Step 2: Run tests, confirm they fail**

```bash
cd runner && python3 -m pytest tests/test_worker.py -v
```

Expected: `ModuleNotFoundError: No module named 'worker'`

- [ ] **Step 3: Create `runner/worker.py`**

```python
from __future__ import annotations
import time
import logging
from gen import belgrade_os_pb2
from redis_client import RedisClient
from bridge_client import BridgeClient

log = logging.getLogger(__name__)


async def process_tool_call(
    call: belgrade_os_pb2.ToolCall,
    redis: RedisClient,
    bridge: BridgeClient,
    worker_id: str,
    lease_ttl_s: int,
) -> None:
    now_ms = int(time.time() * 1000)
    lease = belgrade_os_pb2.WorkerLease(
        worker_id=worker_id,
        task_id=call.task_id,
        call_id=call.call_id,
        leased_at_ms=now_ms,
        expires_at_ms=now_ms + lease_ttl_s * 1000,
    )
    await redis.set_lease(worker_id, lease.SerializeToString(), lease_ttl_s)
    try:
        start_ms = int(time.time() * 1000)
        result = await bridge.execute(call)
        result.duration_ms = int(time.time() * 1000) - start_ms
        await redis.write_tool_result(call.task_id, result.SerializeToString())
    finally:
        await redis.delete_lease(worker_id)
```

- [ ] **Step 4: Run tests, confirm they pass**

```bash
cd runner && python3 -m pytest tests/test_worker.py -v
```

Expected: 5 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add runner/worker.py runner/tests/test_worker.py
git commit -m "feat(runner): worker — lease + bridge dispatch + duration measurement"
```

---

## Task 5: Main — consumer loop and entry point

**Files:**
- Create: `runner/main.py`
- Create: `runner/tests/test_main.py`

- [ ] **Step 1: Write failing tests**

Create `runner/tests/test_main.py`:

```python
from __future__ import annotations
import asyncio
from unittest.mock import AsyncMock, patch
import pytest
from gen import belgrade_os_pb2


def _make_call_bytes() -> bytes:
    c = belgrade_os_pb2.ToolCall()
    c.call_id = "c1"; c.task_id = "t1"
    c.tool_name = "shop:add"; c.input_json = "{}"
    c.trace_id = "tr1"
    return c.SerializeToString()


async def test_consumer_loop_processes_and_acks():
    from main import _consumer_loop, CONSUMER_GROUP
    mock_redis = AsyncMock()
    mock_bridge = AsyncMock()
    read_count = [0]

    async def fake_read(group, worker_id):
        read_count[0] += 1
        if read_count[0] == 1:
            return "msg-1", _make_call_bytes()
        await asyncio.sleep(9999)

    mock_redis.read_tool_call.side_effect = fake_read

    with patch("main.process_tool_call", new_callable=AsyncMock) as mock_process:
        try:
            await asyncio.wait_for(
                _consumer_loop(mock_redis, mock_bridge, worker_id="w1", lease_ttl_s=60),
                timeout=1.0,
            )
        except asyncio.TimeoutError:
            pass

    mock_process.assert_awaited_once()
    call_arg = mock_process.await_args.args[0]
    assert call_arg.task_id == "t1"
    mock_redis.ack_tool_call.assert_awaited_once_with(CONSUMER_GROUP, "msg-1")


async def test_consumer_loop_acks_even_after_exception():
    from main import _consumer_loop, CONSUMER_GROUP
    mock_redis = AsyncMock()
    mock_bridge = AsyncMock()
    read_count = [0]

    async def fake_read(group, worker_id):
        read_count[0] += 1
        if read_count[0] == 1:
            return "msg-2", _make_call_bytes()
        await asyncio.sleep(9999)

    mock_redis.read_tool_call.side_effect = fake_read

    with patch("main.process_tool_call", new_callable=AsyncMock, side_effect=Exception("boom")):
        try:
            await asyncio.wait_for(
                _consumer_loop(mock_redis, mock_bridge, worker_id="w1", lease_ttl_s=60),
                timeout=1.0,
            )
        except asyncio.TimeoutError:
            pass

    mock_redis.ack_tool_call.assert_awaited_once_with(CONSUMER_GROUP, "msg-2")


async def test_consumer_loop_skips_none_without_acking():
    from main import _consumer_loop
    mock_redis = AsyncMock()
    mock_bridge = AsyncMock()
    read_count = [0]

    async def fake_read(group, worker_id):
        read_count[0] += 1
        if read_count[0] == 1:
            return None
        await asyncio.sleep(9999)

    mock_redis.read_tool_call.side_effect = fake_read

    with patch("main.process_tool_call", new_callable=AsyncMock):
        try:
            await asyncio.wait_for(
                _consumer_loop(mock_redis, mock_bridge, worker_id="w1", lease_ttl_s=60),
                timeout=1.0,
            )
        except asyncio.TimeoutError:
            pass

    mock_redis.ack_tool_call.assert_not_awaited()
```

- [ ] **Step 2: Run tests, confirm they fail**

```bash
cd runner && python3 -m pytest tests/test_main.py -v
```

Expected: `ModuleNotFoundError: No module named 'main'`

- [ ] **Step 3: Create `runner/main.py`**

```python
from __future__ import annotations
import asyncio
import logging
from gen import belgrade_os_pb2
from config import Config, load_config
from redis_client import RedisClient
from bridge_client import BridgeClient
from worker import process_tool_call

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

CONSUMER_GROUP = "tool-runners"


async def _consumer_loop(
    redis: RedisClient,
    bridge: BridgeClient,
    worker_id: str,
    lease_ttl_s: int,
) -> None:
    while True:
        result = await redis.read_tool_call(CONSUMER_GROUP, worker_id)
        if result is None:
            continue
        msg_id, call_bytes = result
        call = belgrade_os_pb2.ToolCall()
        call.ParseFromString(call_bytes)
        try:
            await process_tool_call(call, redis, bridge, worker_id, lease_ttl_s)
        except Exception:
            log.exception("unhandled error for call %s task %s", call.call_id, call.task_id)
        finally:
            await redis.ack_tool_call(CONSUMER_GROUP, msg_id)


async def main() -> None:
    cfg = load_config()
    redis = RedisClient(cfg.redis_url)
    bridge = BridgeClient(base_url=cfg.bridge_url, timeout_s=cfg.tool_timeout_s)

    await redis.ensure_consumer_group()
    worker_id = cfg.effective_worker_id
    log.info("resource runner starting worker_id=%s bridge=%s", worker_id, cfg.bridge_url)

    try:
        await _consumer_loop(redis, bridge, worker_id, cfg.lease_ttl_s)
    finally:
        await bridge.close()
        await redis.close()


if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 4: Run tests, confirm they pass**

```bash
cd runner && python3 -m pytest tests/test_main.py -v
```

Expected: 3 tests PASS.

- [ ] **Step 5: Run full suite**

```bash
cd runner && python3 -m pytest tests/ -v
```

Expected: all tests PASS (proto + config + redis_client + bridge_client + worker + main).

- [ ] **Step 6: Commit**

```bash
git add runner/main.py runner/tests/test_main.py
git commit -m "feat(runner): consumer loop + entry point"
```

---

## Self-Review

**1. Spec coverage:**
- [x] Config: redis_url, bridge_url, consumer_group, worker_id, lease_ttl_s, tool_timeout_s, effective_worker_id
- [x] Redis consumer — XREADGROUP tasks:tool_calls, 2000ms block
- [x] write_tool_result includes **both** `data` and `task_id` fields (inference controller requirement)
- [x] Worker lease — WorkerLease proto, SET with TTL, DEL in finally
- [x] Bridge client — POST /v1/execute, persistent httpx client, all errors → failed ToolResult
- [x] duration_ms measured by runner (start-to-response wall clock)
- [x] ACK in finally block (never lost on exception)
- [x] Lease deleted in finally block (never orphaned)
- [x] None read → no ack, continue
- [x] Bridge errors → failed ToolResult written (inference controller never stuck)
- [x] CONSUMER_GROUP = "tool-runners" matches _DEFAULT_GROUP in redis_client
- [x] bridge.close() + redis.close() in main() finally
- [x] Bridge API contract documented with extendability table

**2. Placeholder scan:** None.

**3. Type consistency:**
- `process_tool_call(call, redis, bridge, worker_id, lease_ttl_s)` — Task 4 definition, Task 5 call ✓
- `_consumer_loop(redis, bridge, worker_id, lease_ttl_s)` — Task 5 definition and tests ✓
- `RedisClient.write_tool_result(task_id, proto_bytes)` — Task 2 definition, Task 4 call ✓
- `RedisClient.set_lease(worker_id, proto_bytes, ttl_s)` — Task 2 definition, Task 4 call ✓
- `BridgeClient.execute(ToolCall) → ToolResult` — Task 3 definition, Task 4 call ✓
- `CONSUMER_GROUP` imported in test_main.py from `main` — matches constant in main.py ✓
