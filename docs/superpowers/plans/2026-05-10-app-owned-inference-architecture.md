# App-Owned Inference Architecture Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Correct the architecture so Gateway is an auth edge and app action router — not the public inference API — and give apps a first-class SDK path to enqueue inference tasks.

**Architecture:** Gateway marks `POST /v1/tasks` as legacy/internal. Apps call `ctx.inference.request(prompt)` which writes a `Task` proto directly to `tasks:inbound` via Redis, carrying `app_id` and `tenant_id`. Execution mode is always `UNTRUSTED` for app-owned tasks — trust is a platform-stamped property, not self-declared. Inference propagates new task fields into each ToolCall. A subscribe-only `GET /v1/tasks/{task_id}/stream` endpoint lets clients receive inference output for app-owned tasks. Gateway routes `/api/{app_id}/...` directly to app callback URLs (via Bridge lookup + URL scheme validation) after RBAC — no inference stream touched.

**Tech Stack:** proto3 (protoc/prost), Python 3.12 (pytest-asyncio), Go 1.26 (net/http stdlib), Rust (axum), Redis ACL, Redis streams.

---

## File Map

| Action | Path | Purpose |
|--------|------|---------|
| Modify | `docs/main.spec.md` | Add Architecture Principles section (inc. trust authority rule) |
| Modify | `gateway/handler.go` | Legacy comment on `CreateTask`; add `StreamTask` subscribe endpoint |
| Modify | `gateway/main.go` | Legacy comment on route; register `GET /v1/tasks/{task_id}/stream`; register `/api/` routes |
| Modify | `gateway/handler_test.go` | Tests for `StreamTask` |
| Modify | `proto/belgrade_os.proto` | Add `app_id` (field 7) + `tenant_id` (field 8) to `Task` |
| Regenerated | `gateway/gen/belgrade_os.pb.go` | via `make proto` |
| Regenerated | `runner/gen/belgrade_os_pb2.py` | via `make proto` |
| Regenerated | `inference/gen/belgrade_os_pb2.py` | via `make proto` |
| Regenerated | `notification/gen/belgrade_os_pb2.py` | via `make proto` |
| Regenerated | `sdk/belgrade_sdk/gen/belgrade_os_pb2.py` | via `make proto` |
| Regenerated | `vault_service/gen/belgrade_os_pb2.py` | via `make proto` |
| Regenerated | `platform_controller/gen/belgrade_os_pb2.py` | via `make proto` |
| Modify | `config/redis.acl.template` | Add `~tasks:inbound` to `app` user |
| Modify | `setup.sh` | Update generated app ACL line to include `~tasks:inbound` |
| Modify | `sdk/belgrade_sdk/context.py` | Add `InferenceAdapter` (always UNTRUSTED) + `inference` property |
| Create | `sdk/requirements-dev.txt` | Add pytest + pytest-asyncio for SDK tests |
| Create | `sdk/pytest.ini` | Configure asyncio_mode = auto |
| Create | `sdk/tests/test_inference.py` | Unit tests for InferenceAdapter |
| Modify | `inference/worker.py` | Propagate `task.tenant_id`, `task.execution_mode`; route UNTRUSTED calls to `tasks:untrusted_calls` |
| Modify | `inference/tests/test_worker.py` | Tests for propagation and stream routing |
| Modify | `inference/redis_client.py` | Add `UNTRUSTED_CALLS_STREAM` constant and `push_untrusted_tool_call` method |
| Modify | `bridge/src/registry.rs` | Add `get_callback(app_id) -> Option<String>` method |
| Modify | `bridge/src/router.rs` | Add URL-parse validation in `handle_register`; add `AppInfoResponse`, `handle_app_info`, `GET /v1/apps/:app_id` |
| Modify | `bridge/Cargo.toml` | Add `url = "2"` dependency for URL-parse validation |
| Modify | `gateway/config.go` | Add `BridgeURL` field |
| Modify | `gateway/config_test.go` | Test `BridgeURL` env var loading |
| Create | `gateway/appproxy/handler.go` | `AppProxyHandler` — RBAC + bridge lookup + scheme validation + header sanitization + reverse proxy |
| Create | `gateway/appproxy/handler_test.go` | Unit + integration tests including cookie stripping |
| Modify | `scripts/seed_permissions.py` | Add `api` bundle alongside `web` bundle grants |

---

## Task 1: Architecture Documentation + Legacy Markers

**Files:**
- Modify: `docs/main.spec.md`
- Modify: `gateway/handler.go`
- Modify: `gateway/main.go`

- [ ] **Step 1: Add Architecture Principles section to docs/main.spec.md**

  Insert the following after the `## 2. Technical Stack` table, before `## 3. Implementation Roadmap`:

  ```markdown
  ## 3. Architecture Principles

  These rules govern how the system is designed. Violations indicate planning drift.

  ### Gateway is an edge, not an inference API

  The Gateway (`gateway/`) authenticates requests, enforces RBAC, serves static UI assets, and
  routes direct app actions. It is **not** the product inference API. Inference is an internal
  platform capability, not a public endpoint.

  ### Inference is internal — apps own their workflows

  The Inference Controller (`inference/`) is a background worker that reads from `tasks:inbound`.
  Apps decide when AI is needed. When an app needs inference it enqueues a `Task` proto directly
  to Redis via `ctx.inference.request(prompt)` from the Belgrade SDK — no HTTP call to Gateway.

  ### App-owned inference is always UNTRUSTED

  Apps may not self-assert trusted execution. `ExecutionMode` on app-owned tasks is always stamped
  `UNTRUSTED` by the SDK. The Gateway is the only system component that stamps `TRUSTED`, derived
  from the `TRUSTED_USER_IDS` env var checked against the validated JWT identity. An internal
  platform trusted path for apps is a future concern.

  **Defense-in-depth:** Inference overrides `execution_mode` to `UNTRUSTED` for every task where
  `task.app_id != ""`. Redis ACLs cannot validate proto field values — an app with the `app` Redis
  credential could manually serialize `execution_mode=TRUSTED`. The override in Inference is the
  enforcement boundary, not the SDK default.

  ### App actions are deterministic by default

  `POST /api/{app_id}/...` routes reach the app's own HTTP callback server directly (via Bridge
  lookup). They do **not** enqueue inference tasks. An app may internally call
  `ctx.inference.request()` if its domain logic requires AI, but this is the exception.

  ### `/v1/tasks` is legacy/internal

  `POST /v1/tasks` on the Gateway remains functional for backward compatibility, developer tooling,
  and future Admin App use. It is **not** the recommended integration path for apps or clients.
  New code must use `ctx.inference.request()` from the SDK instead.

  ### No direct HTTP from Gateway to Inference Controller

  Gateway never calls Inference over HTTP. All Gateway→Inference communication is indirect:
  Gateway (or SDK) XADDs to `tasks:inbound`; Inference consumes from that stream.

  ### Bridge callback URLs are an internal trust boundary

  App callback URLs are registered at startup by apps running under Platform Controller supervision.
  The Bridge is not exposed through Cloudflare/Gateway — only internal services can register.
  Registered callback URLs must use `http://` or `https://` schemes; other schemes are rejected
  at registration time.

  ```

  Renumber the old sections 3→4, 4→5, 5→6 in `docs/main.spec.md`.

- [ ] **Step 2: Mark CreateTask as legacy in gateway/handler.go**

  Add a doc comment above `func (h *Handler) CreateTask(...)`:

  ```go
  // CreateTask is a legacy/internal endpoint for direct inference submission.
  // It is NOT the recommended integration path. Apps should use
  // ctx.inference.request() from the Belgrade SDK instead.
  // Retained for backward compatibility, developer tooling, and future Admin App use.
  func (h *Handler) CreateTask(w http.ResponseWriter, r *http.Request) {
  ```

- [ ] **Step 3: Mark the route as internal in gateway/main.go**

  Update the route registration comment:

  ```go
  // Legacy/internal: direct inference submission. Not the recommended client path.
  // Apps use ctx.inference.request() via the SDK. Retained for dev tooling + Admin App.
  mux.HandleFunc("POST /v1/tasks", h.CreateTask)
  ```

- [ ] **Step 4: Verify existing tests still pass**

  ```bash
  cd gateway && go test ./... -v
  ```

  Expected: all tests PASS (no code logic changed).

- [ ] **Step 5: Commit**

  ```bash
  git add docs/main.spec.md gateway/handler.go gateway/main.go
  git commit -m "docs: architecture principles — trust authority, Gateway is edge not inference API, /v1/tasks is legacy"
  ```

---

## Task 2: Proto — Add `app_id` and `tenant_id` to `Task`

**Files:**
- Modify: `proto/belgrade_os.proto`
- Regenerated: all `*/gen/belgrade_os_pb2.py` and `gateway/gen/belgrade_os.pb.go`

- [ ] **Step 1: Verify current proto builds cleanly (baseline)**

  ```bash
  make proto
  ```

  Expected output contains: `proto codegen complete`

- [ ] **Step 2: Add fields to Task in proto/belgrade_os.proto**

  Find the `message Task` block:
  ```proto
  message Task {
    string        task_id        = 1;
    string        user_id        = 2;
    string        prompt         = 3;
    int64         created_at_ms  = 4;
    string        trace_id       = 5;
    ExecutionMode execution_mode = 6;
  }
  ```

  Replace with:
  ```proto
  message Task {
    string        task_id        = 1;  // UUID v4
    string        user_id        = 2;  // resolved from JWT sub claim
    string        prompt         = 3;
    int64         created_at_ms  = 4;  // Unix epoch milliseconds
    string        trace_id       = 5;  // propagated across all services for distributed tracing
    ExecutionMode execution_mode = 6;
    string        app_id         = 7;  // originating app (empty for legacy /v1/tasks calls)
    string        tenant_id      = 8;  // tenant context (empty for legacy /v1/tasks calls)
  }
  ```

- [ ] **Step 3: Regenerate all proto artifacts**

  ```bash
  make proto
  ```

  Expected: `proto codegen complete` — all 7 `gen/` files updated (gateway, runner, inference, notification, sdk, vault_service, platform_controller).

- [ ] **Step 4: Build all services**

  ```bash
  make build
  ```

  Expected: Go gateway and Rust bridge build successfully.

- [ ] **Step 5: Run gateway tests**

  ```bash
  cd gateway && go test ./... -v
  ```

  Expected: all tests PASS. New fields are optional in proto3.

- [ ] **Step 6: Commit**

  ```bash
  git add proto/belgrade_os.proto gateway/gen/belgrade_os.pb.go \
    runner/gen/belgrade_os_pb2.py inference/gen/belgrade_os_pb2.py \
    notification/gen/belgrade_os_pb2.py sdk/belgrade_sdk/gen/belgrade_os_pb2.py \
    vault_service/gen/belgrade_os_pb2.py platform_controller/gen/belgrade_os_pb2.py
  git commit -m "proto: add app_id (field 7) and tenant_id (field 8) to Task"
  ```

---

## Task 3: Redis ACL — Grant `app` User Access to `tasks:inbound`

**Files:**
- Modify: `config/redis.acl.template`
- Modify: `setup.sh`

The `app` Redis user in the ACL template currently has `~tasks:vault_ops ~tasks:notifications +xadd`
only. `ctx.inference.request()` writes to `tasks:inbound`, which is outside that key pattern.

- [ ] **Step 1: Verify the current app user ACL (baseline)**

  ```bash
  grep "^user app" config/redis.acl.template
  ```

  Expected: `user app on >APP_REDIS_PASSWORD ~tasks:vault_ops ~tasks:notifications +ping +xadd`

- [ ] **Step 2: Update the app user line in config/redis.acl.template**

  Find:
  ```
  user app on >APP_REDIS_PASSWORD ~tasks:vault_ops ~tasks:notifications +ping +xadd
  ```

  Replace with:
  ```
  user app on >APP_REDIS_PASSWORD ~tasks:inbound ~tasks:vault_ops ~tasks:notifications +ping +xadd
  ```

- [ ] **Step 3: Verify config/redis.acl.template change is correct**

  ```bash
  grep "^user app" config/redis.acl.template
  ```

  Expected: `user app on >APP_REDIS_PASSWORD ~tasks:inbound ~tasks:vault_ops ~tasks:notifications +ping +xadd`

- [ ] **Step 4: Update the same line in setup.sh**

  `setup.sh` generates `config/redis.acl` from environment variables. If not updated alongside the
  template, running `./setup.sh` again would regenerate the old wrong line and silently undo this fix.

  Find in `setup.sh` (around line 112):
  ```bash
  user app on >${APP_PASS} ~tasks:vault_ops ~tasks:notifications +ping +xadd
  ```

  Replace with:
  ```bash
  user app on >${APP_PASS} ~tasks:inbound ~tasks:vault_ops ~tasks:notifications +ping +xadd
  ```

- [ ] **Step 5: Verify setup.sh change is correct**

  ```bash
  grep "user app on" setup.sh
  ```

  Expected: `user app on >\${APP_PASS} ~tasks:inbound ~tasks:vault_ops ~tasks:notifications +ping +xadd`

- [ ] **Step 6: Commit**

  ```bash
  git add config/redis.acl.template setup.sh
  git commit -m "fix(redis-acl): grant app user xadd on tasks:inbound for ctx.inference.request()"
  ```

  > **Operator note:** Run `./setup.sh` on the host to regenerate `config/redis.acl` with the live
  > passwords (the app ACL line is now correct in setup.sh), then reload: `redis-cli ACL LOAD`.

---

## Task 4: SDK `InferenceAdapter` — `ctx.inference.request(prompt)`

**Files:**
- Modify: `sdk/belgrade_sdk/context.py`
- Create: `sdk/requirements-dev.txt`
- Create: `sdk/pytest.ini`
- Create: `sdk/tests/test_inference.py`

`execution_mode` is NOT a parameter on `request()`. App-owned inference is always `UNTRUSTED`.
The trust authority is the Gateway (derived from `TRUSTED_USER_IDS` + JWT identity), not the app.

- [ ] **Step 1: Create sdk/requirements-dev.txt**

  ```
  -r requirements.txt
  pytest==8.2.2
  pytest-asyncio==1.2.0
  anyio[asyncio]
  ```

- [ ] **Step 2: Create sdk/pytest.ini**

  ```ini
  [pytest]
  asyncio_mode = auto
  asyncio_default_fixture_loop_scope = function
  testpaths = tests
  pythonpath = .
  ```

- [ ] **Step 3: Install dev deps**

  ```bash
  cd sdk && pip install -r requirements-dev.txt
  ```

- [ ] **Step 4: Write failing tests in sdk/tests/test_inference.py**

  ```python
  from __future__ import annotations
  import pytest
  from unittest.mock import AsyncMock
  from belgrade_sdk.context import AppContext
  from belgrade_sdk.gen import belgrade_os_pb2


  def _make_ctx(redis_pool=None) -> AppContext:
      return AppContext(
          app_id="test_app",
          user_id="u1",
          tenant_id="t1",
          trace_id="tr-original",
          bridge_url="http://bridge:8081",
          redis_pool=redis_pool,
      )


  async def test_inference_request_publishes_task_to_stream():
      mock_pool = AsyncMock()
      mock_pool.xadd = AsyncMock(return_value="1234-0")

      ctx = _make_ctx(redis_pool=mock_pool)
      result = await ctx.inference.request("plan my meals")

      assert "task_id" in result
      assert result["task_id"] != ""
      assert result["trace_id"] == "tr-original"

      mock_pool.xadd.assert_called_once()
      stream_name = mock_pool.xadd.call_args[0][0]
      payload = mock_pool.xadd.call_args[0][1]
      assert stream_name == "tasks:inbound"
      assert "data" in payload

      task = belgrade_os_pb2.Task()
      task.ParseFromString(payload["data"])
      assert task.task_id == result["task_id"]
      assert task.prompt == "plan my meals"
      assert task.app_id == "test_app"
      assert task.tenant_id == "t1"
      assert task.user_id == "u1"
      assert task.trace_id == "tr-original"
      # App-owned inference is always UNTRUSTED — trust is stamped by Gateway, not apps.
      assert task.execution_mode == belgrade_os_pb2.ExecutionMode.Value("UNTRUSTED")


  async def test_inference_request_raises_without_redis():
      ctx = _make_ctx(redis_pool=None)
      with pytest.raises(RuntimeError, match="Redis pool not initialized"):
          await ctx.inference.request("test prompt")


  async def test_inference_request_inherits_trace_id():
      mock_pool = AsyncMock()
      mock_pool.xadd = AsyncMock(return_value="1234-0")

      ctx = _make_ctx(redis_pool=mock_pool)
      ctx.trace_id = "my-trace-abc"
      result = await ctx.inference.request("hello")
      assert result["trace_id"] == "my-trace-abc"


  async def test_inference_request_returns_new_task_id_each_call():
      mock_pool = AsyncMock()
      mock_pool.xadd = AsyncMock(return_value="1234-0")

      ctx = _make_ctx(redis_pool=mock_pool)
      r1 = await ctx.inference.request("first")
      r2 = await ctx.inference.request("second")
      assert r1["task_id"] != r2["task_id"]


  async def test_inference_request_empty_tenant_when_none():
      mock_pool = AsyncMock()
      mock_pool.xadd = AsyncMock(return_value="1234-0")

      ctx = AppContext(
          app_id="myapp",
          user_id="u1",
          tenant_id=None,
          trace_id="tr1",
          bridge_url="http://bridge:8081",
          redis_pool=mock_pool,
      )
      await ctx.inference.request("test")

      payload = mock_pool.xadd.call_args[0][1]
      task = belgrade_os_pb2.Task()
      task.ParseFromString(payload["data"])
      assert task.tenant_id == ""
  ```

- [ ] **Step 5: Run tests to confirm they fail**

  ```bash
  cd sdk && python -m pytest tests/test_inference.py -v
  ```

  Expected: `AttributeError: 'AppContext' object has no attribute 'inference'`

- [ ] **Step 6: Implement InferenceAdapter in sdk/belgrade_sdk/context.py**

  Add imports at the top (after existing imports):
  ```python
  import time
  import uuid as _uuid
  ```

  Add `InferenceAdapter` class after `VaultAdapter` (before `AppContext`):
  ```python
  class InferenceAdapter:
      def __init__(self, ctx: "AppContext"):
          self.ctx = ctx

      async def request(self, prompt: str) -> dict:
          """Enqueue an inference task to tasks:inbound via Redis.

          Execution mode is always UNTRUSTED. Trust is a platform-stamped property
          derived from the user's JWT identity at the Gateway — apps must not self-assert it.
          """
          from .gen import belgrade_os_pb2

          if not self.ctx._redis_pool:
              raise RuntimeError("Redis pool not initialized in AppContext")

          task_id = str(_uuid.uuid4())
          trace_id = self.ctx.trace_id or str(_uuid.uuid4())

          task = belgrade_os_pb2.Task()
          task.task_id = task_id
          task.user_id = self.ctx.user_id or ""
          task.prompt = prompt
          task.created_at_ms = int(time.time() * 1000)
          task.trace_id = trace_id
          task.execution_mode = belgrade_os_pb2.ExecutionMode.Value("UNTRUSTED")
          task.app_id = self.ctx.app_id
          task.tenant_id = self.ctx.tenant_id or ""

          try:
              await self.ctx._redis_pool.xadd(
                  defaults.STREAM_TASKS_INBOUND,
                  {"data": task.SerializeToString()},
                  maxlen=1000,
                  approximate=True,
              )
          except Exception as e:
              logger.error("Failed to publish inference task: %s", e)
              raise

          return {"task_id": task_id, "trace_id": trace_id}
  ```

  Add `inference` property to `AppContext` after the `vault` property:
  ```python
  @property
  def inference(self) -> InferenceAdapter:
      return InferenceAdapter(self)
  ```

- [ ] **Step 7: Run tests to confirm they pass**

  ```bash
  cd sdk && python -m pytest tests/test_inference.py -v
  ```

  Expected: all 5 tests PASS.

- [ ] **Step 8: Run full SDK test suite**

  ```bash
  cd sdk && python -m pytest tests/ -v
  ```

  Expected: all tests PASS.

- [ ] **Step 9: Commit**

  ```bash
  git add sdk/belgrade_sdk/context.py sdk/requirements-dev.txt sdk/pytest.ini sdk/tests/test_inference.py
  git commit -m "feat(sdk): InferenceAdapter — ctx.inference.request() enqueues UNTRUSTED Task to tasks:inbound"
  ```

---

## Task 5: Inference Worker — Propagate `app_id`, `tenant_id`, `execution_mode`; Route by Trust Level

**Files:**
- Modify: `inference/worker.py`
- Modify: `inference/tests/test_worker.py`
- Modify: `inference/redis_client.py`

`worker.py` currently hardcodes `tenant_id = "household-vladisavljevic"`, does not set
`execution_mode` on ToolCall, and always writes to `tasks:tool_calls` (the bare-metal runner stream).
The containerized untrusted runner listens on `tasks:untrusted_calls`. Tool calls must be routed to
the correct stream based on `effective_execution_mode`.

- [ ] **Step 1: Add push_untrusted_tool_call to inference/redis_client.py**

  Add the constant and method below the existing `TOOL_CALLS_STREAM` constant and `push_tool_call`:

  ```python
  UNTRUSTED_CALLS_STREAM = "tasks:untrusted_calls"

  async def push_untrusted_tool_call(self, proto_bytes: bytes) -> None:
      """XADD tasks:untrusted_calls * data proto_bytes."""
      await self._redis.xadd(UNTRUSTED_CALLS_STREAM, {"data": proto_bytes})
  ```

- [ ] **Step 2: Write failing tests in inference/tests/test_worker.py**

  Add these three tests to the bottom of `inference/tests/test_worker.py`:

  ```python
  # ---------------------------------------------------------------------------
  # Test 5: execution_mode forced UNTRUSTED for app-owned tasks (defense-in-depth)
  # ---------------------------------------------------------------------------


  async def test_execution_mode_forced_untrusted_for_app_tasks():
      """Inference must override execution_mode=UNTRUSTED when task.app_id is set,
      even if the producer wrote TRUSTED into the proto. Redis ACLs cannot validate
      proto field values, so this is the enforcement boundary."""
      from providers.base import StreamDone, ToolUse

      task = belgrade_os_pb2.Task(
          task_id="t-forge",
          user_id="u1",
          prompt="hello",
          trace_id="tr1",
          app_id="shopping",  # app-owned
          execution_mode=belgrade_os_pb2.ExecutionMode.Value("TRUSTED"),  # attempted forge
      )
      mock_redis = _make_redis()
      tool_result = belgrade_os_pb2.ToolResult(
          call_id="c-forge", task_id="t-forge", success=True, output_json="{}"
      )
      mock_redis.read_tool_result = AsyncMock(
          return_value=("msg-forge", tool_result.SerializeToString())
      )
      provider = await _provider_from_events([
          [StreamDone("tool_use", [ToolUse("c-forge", "shopping:add", {"item": "milk"})])],
          [StreamDone("end_turn")],
      ])

      await process_task(task, mock_redis, provider, "worker-1")

      # App task must land on the untrusted stream — forged TRUSTED is ignored.
      mock_redis.push_untrusted_tool_call.assert_awaited_once()
      mock_redis.push_tool_call.assert_not_awaited()
      tc_bytes = mock_redis.push_untrusted_tool_call.await_args.args[0]
      tc = belgrade_os_pb2.ToolCall()
      tc.ParseFromString(tc_bytes)
      # Despite the task claiming TRUSTED, app tasks (app_id != "") must be UNTRUSTED.
      assert tc.execution_mode == belgrade_os_pb2.ExecutionMode.Value("UNTRUSTED")


  # ---------------------------------------------------------------------------
  # Test 6: tenant_id propagated from task into ToolCall
  # ---------------------------------------------------------------------------


  async def test_tool_call_carries_tenant_id_from_task():
      from providers.base import StreamDone, ToolUse

      task = belgrade_os_pb2.Task(
          task_id="t-tenant",
          user_id="u1",
          prompt="hello",
          trace_id="tr1",
          tenant_id="household-test",
          app_id="nutrition",
      )
      mock_redis = _make_redis()
      tool_result = belgrade_os_pb2.ToolResult(
          call_id="c1", task_id="t-tenant", success=True, output_json="{}"
      )
      mock_redis.read_tool_result = AsyncMock(
          return_value=("msg-1", tool_result.SerializeToString())
      )
      provider = await _provider_from_events([
          [StreamDone("tool_use", [ToolUse("c1", "nutrition:log", {"calories": "500"})])],
          [StreamDone("end_turn")],
      ])

      await process_task(task, mock_redis, provider, "worker-1")

      # task.app_id is set → routes to untrusted stream
      tc_bytes = mock_redis.push_untrusted_tool_call.await_args.args[0]
      tc = belgrade_os_pb2.ToolCall()
      tc.ParseFromString(tc_bytes)
      assert tc.tenant_id == "household-test"


  # ---------------------------------------------------------------------------
  # Test 7: execution_mode preserved when app_id is empty (gateway/legacy path)
  # ---------------------------------------------------------------------------


  async def test_tool_call_carries_execution_mode_when_no_app_id():
      """When task.app_id is empty (legacy /v1/tasks gateway path), execution_mode is
      preserved from the task — the Gateway stamped it from TRUSTED_USER_IDS."""
      from providers.base import StreamDone, ToolUse

      task = belgrade_os_pb2.Task(
          task_id="t-mode",
          user_id="u1",
          prompt="hello",
          trace_id="tr1",
          app_id="",  # gateway path — no app_id
          execution_mode=belgrade_os_pb2.ExecutionMode.Value("TRUSTED"),
      )
      mock_redis = _make_redis()
      tool_result = belgrade_os_pb2.ToolResult(
          call_id="c2", task_id="t-mode", success=True, output_json="{}"
      )
      mock_redis.read_tool_result = AsyncMock(
          return_value=("msg-2", tool_result.SerializeToString())
      )
      provider = await _provider_from_events([
          [StreamDone("tool_use", [ToolUse("c2", "shopping:add_item", {"item": "milk"})])],
          [StreamDone("end_turn")],
      ])

      await process_task(task, mock_redis, provider, "worker-1")

      # app_id is empty → gateway/legacy path → routes to trusted stream
      mock_redis.push_tool_call.assert_awaited_once()
      mock_redis.push_untrusted_tool_call.assert_not_awaited()
      tc_bytes = mock_redis.push_tool_call.await_args.args[0]
      tc = belgrade_os_pb2.ToolCall()
      tc.ParseFromString(tc_bytes)
      # app_id is empty → gateway path → execution_mode from task is preserved
      assert tc.execution_mode == belgrade_os_pb2.ExecutionMode.Value("TRUSTED")
  ```

- [ ] **Step 3: Run new tests to confirm they fail**

  ```bash
  cd inference && python -m pytest \
    tests/test_worker.py::test_execution_mode_forced_untrusted_for_app_tasks \
    tests/test_worker.py::test_tool_call_carries_tenant_id_from_task \
    tests/test_worker.py::test_tool_call_carries_execution_mode_when_no_app_id -v
  ```

  Expected: all three FAIL — routing not yet implemented, so `push_untrusted_tool_call` is never
  called. Example: `AssertionError: Expected 'push_untrusted_tool_call' to have been awaited once.
  Awaited 0 times.`

- [ ] **Step 4: Update inference/worker.py**

  In `process_task`, replace the hardcoded `tenant_id` line and update the ToolCall construction and routing.

  Find:
  ```python
  # Task currently doesn't carry tenant_id (missing in gateway/handler.go probably)
  # For now, let's derive it or assume it's passed.
  tenant_id = "household-vladisavljevic" # Placeholder or derived from registry
  ```

  Replace with:
  ```python
  # Use tenant_id from task if present; fall back to platform default for legacy tasks
  # (legacy /v1/tasks calls leave tenant_id empty).
  tenant_id = task.tenant_id or "household-vladisavljevic"

  # Defense-in-depth: if task.app_id is set, the task came from an app via the SDK.
  # Force UNTRUSTED regardless of what the proto field says — Redis ACLs cannot
  # validate field values, so this is the enforcement boundary.
  effective_execution_mode = (
      belgrade_os_pb2.ExecutionMode.Value("UNTRUSTED")
      if task.app_id
      else task.execution_mode
  )
  ```

  Find the ToolCall construction and dispatch block:
  ```python
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
  ```

  Replace with:
  ```python
  tc = belgrade_os_pb2.ToolCall(
      call_id=tool_use.call_id,
      task_id=task.task_id,
      tool_name=tool_use.name,
      input_json=json.dumps(tool_use.input),
      trace_id=task.trace_id,
      user_id=task.user_id,
      tenant_id=tenant_id,
      execution_mode=effective_execution_mode,
  )
  log.debug("tool_call app_id=%s tool=%s mode=%s", task.app_id, tool_use.name, effective_execution_mode)
  serialised = tc.SerializeToString()
  if effective_execution_mode == belgrade_os_pb2.ExecutionMode.Value("UNTRUSTED"):
      await redis.push_untrusted_tool_call(serialised)
  else:
      await redis.push_tool_call(serialised)
  ```

- [ ] **Step 5: Run tests to confirm they pass**

  ```bash
  cd inference && python -m pytest tests/test_worker.py -v
  ```

  Expected: all 7 tests PASS (4 existing + 3 new). Existing Test 3
  (`test_tool_use_dispatches_and_continues`) still calls `push_tool_call` because
  `task.execution_mode = 0` (UNSPECIFIED, not set) routes to the trusted stream.

- [ ] **Step 6: Commit**

  ```bash
  git add inference/worker.py inference/tests/test_worker.py inference/redis_client.py
  git commit -m "fix(inference): route UNTRUSTED tool calls to tasks:untrusted_calls; propagate tenant_id, execution_mode"
  ```

---

## Task 6: Gateway — Subscribe-Only SSE Endpoint for App-Owned Tasks

**Files:**
- Modify: `gateway/handler.go`
- Modify: `gateway/handler_test.go`
- Modify: `gateway/main.go`

Without this, `ctx.inference.request()` can start work but there is no documented way for a
client/browser to receive the streaming result. This endpoint subscribes to `sse:{task_id}` without
creating a new task — it provides the result channel for app-owned inference.

The task_id is a random UUID generated by the SDK. It acts as a capability token: knowing it is
sufficient for subscription. Any authenticated user who holds the task_id may subscribe.

> **Capability-token caveat:** task IDs in URLs can appear in browser history, reverse proxy access
> logs, and server access logs. For a personal home system this is acceptable. Future improvement:
> move to `GET /api/{app_id}/tasks/{task_id}/stream` with app-scoped RBAC, so the URL is scoped to
> the app and the user's role is enforced rather than relying on task_id opacity.

> **`r.PathValue` requires ServeMux:** `r.PathValue("task_id")` is only populated when the request
> is routed through an `http.ServeMux` that matched the `{task_id}` wildcard. Tests that call
> `h.StreamTask` directly (bypassing the mux) will see an empty string regardless of the URL.
> All meaningful tests for this handler must go through `mux.ServeHTTP`.

- [ ] **Step 1: Write failing tests in gateway/handler_test.go**

  Add these tests to `gateway/handler_test.go`:

  ```go
  func TestStreamTaskReturns401WithoutAuth(t *testing.T) {
      // Direct call is valid here: auth check (401) happens before path value extraction.
      cache := auth.NewTestCache(t, "http://localhost:0")
      h := NewHandler(cache, nil, "aud", auth.TrustedSet{})

      req := httptest.NewRequest(http.MethodGet, "/v1/tasks/some-task-id/stream", nil)
      w := httptest.NewRecorder()
      h.StreamTask(w, req)

      if w.Code != http.StatusUnauthorized {
          t.Fatalf("expected 401, got %d", w.Code)
      }
  }

  func TestStreamTaskPathValuePopulatedByMux(t *testing.T) {
      // r.PathValue("task_id") only works through ServeMux. This test verifies the mux
      // populates the path value: auth passes, task_id is non-empty, next step is
      // SubscribeSSE which fails (nil redis) → 500, NOT 400 (which would mean empty task_id).
      key := auth.GenerateTestKey(t)
      kid := "stream-mux-kid"
      srv := auth.ServeJWKS(t, &key.PublicKey, kid)
      defer srv.Close()

      cache := auth.NewTestCache(t, srv.URL)
      h := NewHandler(cache, nil, "test-aud", auth.TrustedSet{}) // nil redis intentional
      tokenStr := auth.SignToken(t, key, kid, "user-mux", "test-aud", time.Now().Add(time.Hour))

      mux := http.NewServeMux()
      mux.HandleFunc("GET /v1/tasks/{task_id}/stream", h.StreamTask)

      req := httptest.NewRequest(http.MethodGet, "/v1/tasks/my-task-uuid-123/stream", nil)
      req.Header.Set("Cf-Access-Jwt-Assertion", tokenStr)
      w := httptest.NewRecorder()
      mux.ServeHTTP(w, req)

      // 400 would mean task_id was empty (path value not populated by mux) — that's the bug.
      // nil redis → SubscribeSSE fails → 500 means path value WAS populated correctly.
      if w.Code == http.StatusBadRequest {
          t.Fatal("r.PathValue('task_id') not populated by mux — got 400 (empty task_id)")
      }
      if w.Code != http.StatusInternalServerError {
          t.Fatalf("expected 500 (nil redis → subscribe fails), got %d", w.Code)
      }
  }
  ```

- [ ] **Step 2: Run new tests to confirm they fail**

  ```bash
  cd gateway && go test ./... -run TestStreamTask -v 2>&1 | head -20
  ```

  Expected: compile error — `h.StreamTask` undefined.

- [ ] **Step 3: Add StreamTask to gateway/handler.go**

  Add this method to `handler.go` after the `CreateTask` method:

  ```go
  // StreamTask is the subscribe-only SSE endpoint for app-owned inference tasks.
  // The client passes a task_id previously returned by ctx.inference.request().
  // Any authenticated user who holds the task_id UUID may subscribe — the UUID itself
  // acts as a capability token (128-bit entropy, not guessable).
  func (h *Handler) StreamTask(w http.ResponseWriter, r *http.Request) {
      tokenStr := r.Header.Get("Cf-Access-Jwt-Assertion")
      if tokenStr == "" {
          if cookie, err := r.Cookie("CF_Authorization"); err == nil {
              tokenStr = cookie.Value
          }
      }
      if tokenStr == "" {
          http.Error(w, "missing authentication", http.StatusUnauthorized)
          return
      }
      if _, err := auth.ValidateToken(tokenStr, h.auth, h.audience); err != nil {
          http.Error(w, "unauthorized", http.StatusUnauthorized)
          return
      }

      taskID := r.PathValue("task_id")
      if taskID == "" {
          http.Error(w, "missing task_id", http.StatusBadRequest)
          return
      }

      evtCh, err := h.redis.SubscribeSSE(r.Context(), taskID)
      if err != nil {
          http.Error(w, "failed to set up stream", http.StatusInternalServerError)
          return
      }
      streamSSE(w, r, evtCh)
  }
  ```

- [ ] **Step 4: Register the route in gateway/main.go**

  After `mux.HandleFunc("POST /v1/tasks", h.CreateTask)`, add:

  ```go
  // Subscribe-only SSE for app-owned inference tasks. task_id returned by ctx.inference.request().
  mux.HandleFunc("GET /v1/tasks/{task_id}/stream", h.StreamTask)
  ```

- [ ] **Step 5: Run all gateway tests**

  ```bash
  cd gateway && go test ./... -v
  ```

  Expected: all tests PASS including both new `TestStreamTask*` tests.

- [ ] **Step 6: Commit**

  ```bash
  git add gateway/handler.go gateway/handler_test.go gateway/main.go
  git commit -m "feat(gateway): GET /v1/tasks/{task_id}/stream — subscribe-only SSE for app-owned inference"
  ```

---

## Task 7: Bridge — App Info Endpoint + URL-Parse Validation

**Files:**
- Modify: `bridge/src/registry.rs`
- Modify: `bridge/src/router.rs`
- Modify: `bridge/Cargo.toml`

Registration validation prevents misconfigured or malicious callback URLs (e.g. `file://`, `ftp://`,
`http:///path` with empty host) from being stored and later proxied through Gateway.

- [ ] **Step 1: Write failing registry test**

  Add to `bridge/src/registry.rs` `#[cfg(test)]` mod:

  ```rust
  #[test]
  fn test_get_callback_returns_url_for_known_app() {
      let reg = ToolRegistry::new();
      reg.register("shopping", "http://app:9000", &[]);
      assert_eq!(reg.get_callback("shopping"), Some("http://app:9000".to_string()));
  }

  #[test]
  fn test_get_callback_returns_none_for_unknown_app() {
      let reg = ToolRegistry::new();
      assert_eq!(reg.get_callback("unknown"), None);
  }
  ```

- [ ] **Step 2: Run registry tests to confirm they fail**

  ```bash
  cd bridge && cargo test registry -- --nocapture 2>&1 | head -20
  ```

  Expected: compile error — `get_callback` not found.

- [ ] **Step 3: Add get_callback to ToolRegistry in bridge/src/registry.rs**

  Inside `impl ToolRegistry`, after `pub fn list(...)`:

  ```rust
  pub fn get_callback(&self, app_id: &str) -> Option<String> {
      self.app_callbacks.read().expect("lock poisoned").get(app_id).cloned()
  }
  ```

- [ ] **Step 4: Run registry tests to confirm they pass**

  ```bash
  cd bridge && cargo test registry -- --nocapture
  ```

  Expected: all registry tests PASS.

- [ ] **Step 5: Write failing router tests**

  Add to `bridge/src/router.rs` `#[cfg(test)]` mod:

  ```rust
  #[tokio::test]
  async fn test_get_app_info_returns_callback_url() {
      let registry = Arc::new(ToolRegistry::new());
      registry.register("shopping", "http://app:9000", &[]);
      let app = make_router(Arc::clone(&registry));

      let resp = app
          .oneshot(Request::builder().uri("/v1/apps/shopping").body(Body::empty()).unwrap())
          .await
          .unwrap();

      assert_eq!(resp.status(), StatusCode::OK);
      let body = resp.into_body().collect().await.unwrap().to_bytes();
      let json: serde_json::Value = serde_json::from_slice(&body).unwrap();
      assert_eq!(json["app_id"], "shopping");
      assert_eq!(json["callback_url"], "http://app:9000");
  }

  #[tokio::test]
  async fn test_get_app_info_returns_404_for_unknown_app() {
      let registry = Arc::new(ToolRegistry::new());
      let app = make_router(Arc::clone(&registry));

      let resp = app
          .oneshot(Request::builder().uri("/v1/apps/unknown").body(Body::empty()).unwrap())
          .await
          .unwrap();

      assert_eq!(resp.status(), StatusCode::NOT_FOUND);
  }

  #[tokio::test]
  async fn test_register_rejects_non_http_callback_url() {
      let registry = Arc::new(ToolRegistry::new());
      let app = make_router(Arc::clone(&registry));

      let body = serde_json::json!({
          "app_id": "evil",
          "callback_url": "file:///etc/passwd",
          "tools": []
      });
      let resp = app
          .oneshot(
              Request::builder()
                  .method("POST")
                  .uri("/v1/register")
                  .header("content-type", "application/json")
                  .body(Body::from(body.to_string()))
                  .unwrap(),
          )
          .await
          .unwrap();

      assert_eq!(resp.status(), StatusCode::BAD_REQUEST);
  }

  #[tokio::test]
  async fn test_register_accepts_https_callback_url() {
      let registry = Arc::new(ToolRegistry::new());
      let app = make_router(Arc::clone(&registry));

      let body = serde_json::json!({
          "app_id": "myapp",
          "callback_url": "https://app.internal:9000",
          "tools": []
      });
      let resp = app
          .oneshot(
              Request::builder()
                  .method("POST")
                  .uri("/v1/register")
                  .header("content-type", "application/json")
                  .body(Body::from(body.to_string()))
                  .unwrap(),
          )
          .await
          .unwrap();

      assert_eq!(resp.status(), StatusCode::NO_CONTENT);
  }

  #[tokio::test]
  async fn test_register_rejects_empty_host_url() {
      // Prefix matching alone accepts "http:///path" (empty host). URL parsing must reject it.
      let registry = Arc::new(ToolRegistry::new());
      let app = make_router(Arc::clone(&registry));

      let body = serde_json::json!({
          "app_id": "evil",
          "callback_url": "http:///path",
          "tools": []
      });
      let resp = app
          .oneshot(
              Request::builder()
                  .method("POST")
                  .uri("/v1/register")
                  .header("content-type", "application/json")
                  .body(Body::from(body.to_string()))
                  .unwrap(),
          )
          .await
          .unwrap();

      assert_eq!(resp.status(), StatusCode::BAD_REQUEST);
  }
  ```

- [ ] **Step 6: Run router tests to confirm new tests fail**

  ```bash
  cd bridge && cargo test router::tests::test_get_app_info router::tests::test_register_rejects_non_http router::tests::test_register_rejects_empty_host -- --nocapture 2>&1 | head -30
  ```

  Expected: compile error (routes not found) and URL-parse validation not present.

- [ ] **Step 7: Add url crate, AppInfoResponse, handle_app_info, URL-parse validation**

  First, add `url = "2"` to `[dependencies]` in `bridge/Cargo.toml`:

  ```toml
  url = "2"
  ```

  After the `NotificationsProviderResponse` struct in `bridge/src/router.rs`, add:

  ```rust
  #[derive(Serialize)]
  pub struct AppInfoResponse {
      pub app_id: String,
      pub callback_url: String,
  }
  ```

  In `handle_register`, after the tool namespace check loop (after the `return Err(...)` block), add
  URL-parse validation before the store write:

  ```rust
  // Validate callback URL — only http:// and https:// with a non-empty host are permitted.
  // Prefix matching alone accepts "http:///path" (empty host), which is an SSRF risk.
  let parsed_url = url::Url::parse(&req.callback_url).map_err(|_| (
      StatusCode::BAD_REQUEST,
      format!("callback_url {:?} is not a valid URL", req.callback_url),
  ))?;
  if !matches!(parsed_url.scheme(), "http" | "https") || parsed_url.host().is_none() {
      return Err((
          StatusCode::BAD_REQUEST,
          format!(
              "callback_url {:?} must use http:// or https:// with a non-empty host",
              req.callback_url
          ),
      ));
  }
  ```

  After `handle_notifications_provider`, add:

  ```rust
  async fn handle_app_info(
      State(state): State<AppState>,
      axum::extract::Path(app_id): axum::extract::Path<String>,
  ) -> Result<Json<AppInfoResponse>, StatusCode> {
      match state.registry.get_callback(&app_id) {
          Some(callback_url) => Ok(Json(AppInfoResponse { app_id, callback_url })),
          None => Err(StatusCode::NOT_FOUND),
      }
  }
  ```

  In `create_router`, after `.route("/v1/notifications/provider", ...)`, add:

  ```rust
  .route("/v1/apps/:app_id", get(handle_app_info))
  ```

- [ ] **Step 8: Run all bridge tests**

  ```bash
  cd bridge && cargo test -- --nocapture
  ```

  Expected: all tests PASS including the 5 new router tests (including empty-host rejection).

- [ ] **Step 9: Commit**

  ```bash
  git add bridge/src/registry.rs bridge/src/router.rs bridge/Cargo.toml
  git commit -m "feat(bridge): GET /v1/apps/:app_id; URL-parse validate callback URL (scheme + non-empty host)"
  ```

---

## Task 8: Gateway — App Action Proxy + Header Sanitization + RBAC Seed

**Files:**
- Modify: `gateway/config.go`
- Modify: `gateway/config_test.go`
- Create: `gateway/appproxy/handler.go`
- Create: `gateway/appproxy/handler_test.go`
- Modify: `gateway/main.go`
- Modify: `scripts/seed_permissions.py`

The proxy must sanitize headers to prevent forwarding Cloudflare auth cookies and hop-by-hop headers
to apps. It must also validate the callback URL scheme received from Bridge before proxying.

- [ ] **Step 1: Write failing config tests**

  In `gateway/config_test.go`, add:

  ```go
  func TestBridgeURLFromEnv(t *testing.T) {
      t.Setenv("BRIDGE_URL", "http://bridge:8081")
      cfg := LoadConfig()
      if cfg.BridgeURL != "http://bridge:8081" {
          t.Fatalf("expected BridgeURL=http://bridge:8081, got %q", cfg.BridgeURL)
      }
  }

  func TestBridgeURLDefault(t *testing.T) {
      t.Setenv("BRIDGE_URL", "")
      cfg := LoadConfig()
      if cfg.BridgeURL != "http://localhost:8081" {
          t.Fatalf("expected default BridgeURL=http://localhost:8081, got %q", cfg.BridgeURL)
      }
  }
  ```

- [ ] **Step 2: Run config tests to confirm they fail**

  ```bash
  cd gateway && go test ./... -run TestBridgeURL -v
  ```

  Expected: compile error — `cfg.BridgeURL` undefined.

- [ ] **Step 3: Add BridgeURL to gateway/config.go**

  Add `BridgeURL string` to `Config` struct, and `BridgeURL: getEnv("BRIDGE_URL", "http://localhost:8081")` to `LoadConfig()`.

  Full updated `Config`:
  ```go
  type Config struct {
      Port           string
      RedisURL       string
      CFTeamDomain   string
      CFAudience     string
      AppsRoot       string
      GatewayURL     string
      TrustedUserIDs string
      BridgeURL      string
  }
  ```

  Full updated `LoadConfig`:
  ```go
  func LoadConfig() Config {
      return Config{
          Port:           getEnv("PORT", "8080"),
          RedisURL:       getEnvOr(os.Getenv("GATEWAY_REDIS_URL"), getEnv("REDIS_URL", "redis://localhost:6379")),
          CFTeamDomain:   getEnv("CF_TEAM_DOMAIN", ""),
          CFAudience:     getEnv("CF_AUDIENCE", ""),
          AppsRoot:       getEnv("APPS_ROOT", "../apps"),
          GatewayURL:     getEnv("GATEWAY_URL", "http://localhost:8080"),
          TrustedUserIDs: getEnv("TRUSTED_USER_IDS", ""),
          BridgeURL:      getEnv("BRIDGE_URL", "http://localhost:8081"),
      }
  }
  ```

- [ ] **Step 4: Run config tests to confirm they pass**

  ```bash
  cd gateway && go test ./... -run TestBridgeURL -v
  ```

  Expected: both config tests PASS.

- [ ] **Step 5: Write failing appproxy tests in gateway/appproxy/handler_test.go**

  ```go
  package appproxy_test

  import (
      "context"
      "encoding/json"
      "io"
      "net/http"
      "net/http/httptest"
      "strings"
      "testing"

      "belgrade-os/gateway/appproxy"
      "belgrade-os/gateway/auth"
      "belgrade-os/gateway/redis"
  )

  func injectClaims(r *http.Request, userID string) *http.Request {
      claims := &auth.Claims{UserID: userID}
      return r.WithContext(context.WithValue(r.Context(), auth.ClaimsKey, claims))
  }

  func makeBridge(t *testing.T, appID, callbackURL string) *httptest.Server {
      t.Helper()
      return httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
          if r.URL.Path == "/v1/apps/"+appID {
              json.NewEncoder(w).Encode(map[string]string{
                  "app_id": appID, "callback_url": callbackURL,
              })
              return
          }
          http.NotFound(w, r)
      }))
  }

  func TestServeAPIReturns401WithoutClaims(t *testing.T) {
      h := appproxy.NewHandler("http://bridge", nil)
      req := httptest.NewRequest(http.MethodPost, "/api/shopping/add_item", strings.NewReader(`{}`))
      w := httptest.NewRecorder()
      h.ServeAPI(w, req)
      if w.Code != http.StatusUnauthorized {
          t.Fatalf("expected 401, got %d", w.Code)
      }
  }

  func TestServeAPIReturns404ForEmptyAppID(t *testing.T) {
      h := appproxy.NewHandler("http://bridge", nil)
      req := injectClaims(httptest.NewRequest(http.MethodPost, "/api/", nil), "user1")
      w := httptest.NewRecorder()
      h.ServeAPI(w, req)
      if w.Code != http.StatusNotFound {
          t.Fatalf("expected 404, got %d", w.Code)
      }
  }

  func TestServeAPIReturns403WhenRBACFails(t *testing.T) {
      rClient, err := redis.NewRedisClient("redis://localhost:6379")
      if err != nil {
          t.Skipf("redis unavailable: %v", err)
      }
      defer rClient.Close()

      h := appproxy.NewHandler("http://bridge", rClient)
      req := injectClaims(
          httptest.NewRequest(http.MethodPost, "/api/shopping/add_item", strings.NewReader(`{}`)),
          "no-perms-user",
      )
      req.Header.Set("Content-Type", "application/json")
      w := httptest.NewRecorder()
      h.ServeAPI(w, req)
      if w.Code != http.StatusForbidden {
          t.Fatalf("expected 403, got %d", w.Code)
      }
  }

  func TestServeAPIReturns404WhenAppNotRegistered(t *testing.T) {
      rClient, err := redis.NewRedisClient("redis://localhost:6379")
      if err != nil {
          t.Skipf("redis unavailable: %v", err)
      }
      defer rClient.Close()

      ctx := context.Background()
      rClient.RDB.HSet(ctx, "perms:user3", "shopping:api", "member")
      defer rClient.RDB.Del(ctx, "perms:user3")

      bridge := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
          http.NotFound(w, r)
      }))
      defer bridge.Close()

      h := appproxy.NewHandler(bridge.URL, rClient)
      req := injectClaims(
          httptest.NewRequest(http.MethodPost, "/api/shopping/add_item", strings.NewReader(`{}`)),
          "user3",
      )
      w := httptest.NewRecorder()
      h.ServeAPI(w, req)
      if w.Code != http.StatusNotFound {
          t.Fatalf("expected 404, got %d", w.Code)
      }
  }

  func TestServeAPIProxiesRequestToApp(t *testing.T) {
      rClient, err := redis.NewRedisClient("redis://localhost:6379")
      if err != nil {
          t.Skipf("redis unavailable: %v", err)
      }
      defer rClient.Close()

      ctx := context.Background()
      rClient.RDB.HSet(ctx, "perms:user4", "shopping:api", "member")
      defer rClient.RDB.Del(ctx, "perms:user4")

      var receivedUserID string
      appServer := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
          receivedUserID = r.Header.Get("X-User-ID")
          body, _ := io.ReadAll(r.Body)
          w.Header().Set("Content-Type", "application/json")
          w.WriteHeader(http.StatusOK)
          w.Write(body)
      }))
      defer appServer.Close()

      bridge := makeBridge(t, "shopping", appServer.URL)
      defer bridge.Close()

      h := appproxy.NewHandler(bridge.URL, rClient)
      req := injectClaims(
          httptest.NewRequest(http.MethodPost, "/api/shopping/add_item", strings.NewReader(`{"item":"milk"}`)),
          "user4",
      )
      req.Header.Set("Content-Type", "application/json")
      w := httptest.NewRecorder()
      h.ServeAPI(w, req)

      if w.Code != http.StatusOK {
          t.Fatalf("expected 200, got %d: %s", w.Code, w.Body.String())
      }
      if receivedUserID != "user4" {
          t.Fatalf("expected X-User-ID=user4, got %q", receivedUserID)
      }
      if !strings.Contains(w.Body.String(), "milk") {
          t.Fatalf("expected echoed body to contain 'milk', got %q", w.Body.String())
      }
  }

  func TestServeAPIStripsAuthHeaders(t *testing.T) {
      rClient, err := redis.NewRedisClient("redis://localhost:6379")
      if err != nil {
          t.Skipf("redis unavailable: %v", err)
      }
      defer rClient.Close()

      ctx := context.Background()
      rClient.RDB.HSet(ctx, "perms:user5", "shopping:api", "member")
      defer rClient.RDB.Del(ctx, "perms:user5")

      var receivedJWT, receivedCookie, receivedAuth string
      appServer := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
          receivedJWT = r.Header.Get("Cf-Access-Jwt-Assertion")
          receivedCookie = r.Header.Get("Cookie")
          receivedAuth = r.Header.Get("Authorization")
          w.WriteHeader(http.StatusOK)
      }))
      defer appServer.Close()

      bridge := makeBridge(t, "shopping", appServer.URL)
      defer bridge.Close()

      h := appproxy.NewHandler(bridge.URL, rClient)
      req := injectClaims(
          httptest.NewRequest(http.MethodPost, "/api/shopping/do", nil),
          "user5",
      )
      req.Header.Set("Cf-Access-Jwt-Assertion", "secret-jwt")
      req.Header.Set("Cookie", "CF_Authorization=cookie-token; session=abc")
      req.Header.Set("Authorization", "Bearer some-token")
      w := httptest.NewRecorder()
      h.ServeAPI(w, req)

      if receivedJWT != "" {
          t.Fatalf("Cf-Access-Jwt-Assertion must be stripped, got %q", receivedJWT)
      }
      if receivedCookie != "" {
          t.Fatalf("Cookie must be stripped, got %q", receivedCookie)
      }
      if receivedAuth != "" {
          t.Fatalf("Authorization must be stripped, got %q", receivedAuth)
      }
  }
  ```

- [ ] **Step 6: Run tests to confirm they fail**

  ```bash
  cd gateway && go test ./appproxy/... -v 2>&1 | head -20
  ```

  Expected: compile error — package `appproxy` does not exist.

- [ ] **Step 7: Implement gateway/appproxy/handler.go**

  ```go
  package appproxy

  import (
      "encoding/json"
      "errors"
      "fmt"
      "io"
      "net/http"
      "net/url"
      "strings"
      "time"

      "belgrade-os/gateway/auth"
      "belgrade-os/gateway/redis"
  )

  var ErrAppNotFound = errors.New("app not registered")

  // hopByHopHeaders are stripped from both inbound and outbound proxy requests.
  // Forwarding them would confuse downstream servers or leak connection state.
  var hopByHopHeaders = []string{
      "Connection", "Keep-Alive", "Proxy-Authenticate", "Proxy-Authorization",
      "Te", "Trailers", "Transfer-Encoding", "Upgrade",
  }

  type AppProxyHandler struct {
      bridgeURL    string
      redis        *redis.RedisClient
      bridgeClient *http.Client
      appClient    *http.Client
  }

  func NewHandler(bridgeURL string, rClient *redis.RedisClient) *AppProxyHandler {
      return &AppProxyHandler{
          bridgeURL:    strings.TrimRight(bridgeURL, "/"),
          redis:        rClient,
          bridgeClient: &http.Client{Timeout: 5 * time.Second},
          appClient:    &http.Client{Timeout: 30 * time.Second},
      }
  }

  // ServeAPI handles /api/{app_id}/{path...} for all HTTP methods.
  // Auth is enforced by the AuthMiddleware that wraps this handler in main.go.
  // RBAC is checked against perms:{userID} → {appID}:api.
  // The request is proxied to the app's registered callback URL (from Bridge) after
  // sanitizing Cloudflare auth headers, cookies, and hop-by-hop headers.
  func (h *AppProxyHandler) ServeAPI(w http.ResponseWriter, r *http.Request) {
      claims, ok := r.Context().Value(auth.ClaimsKey).(*auth.Claims)
      if !ok {
          http.Error(w, "unauthorized", http.StatusUnauthorized)
          return
      }

      path := strings.TrimPrefix(r.URL.Path, "/api/")
      parts := strings.SplitN(path, "/", 2)
      if len(parts) == 0 || parts[0] == "" {
          http.NotFound(w, r)
          return
      }
      appID := parts[0]
      subPath := ""
      if len(parts) == 2 {
          subPath = parts[1]
      }

      if _, err := h.redis.GetPermission(r.Context(), claims.UserID, appID, "api"); err != nil {
          http.Error(w, "forbidden", http.StatusForbidden)
          return
      }

      callbackURL, err := h.fetchCallbackURL(r, appID)
      if errors.Is(err, ErrAppNotFound) {
          http.NotFound(w, r)
          return
      }
      if err != nil {
          http.Error(w, "gateway error", http.StatusBadGateway)
          return
      }

      targetURL := strings.TrimRight(callbackURL, "/")
      if subPath != "" {
          targetURL = targetURL + "/" + subPath
      }
      if r.URL.RawQuery != "" {
          targetURL = targetURL + "?" + r.URL.RawQuery
      }

      h.proxyRequest(w, r, targetURL, claims.UserID)
  }

  func (h *AppProxyHandler) fetchCallbackURL(r *http.Request, appID string) (string, error) {
      req, err := http.NewRequestWithContext(r.Context(), http.MethodGet,
          fmt.Sprintf("%s/v1/apps/%s", h.bridgeURL, appID), nil)
      if err != nil {
          return "", fmt.Errorf("build bridge request: %w", err)
      }

      resp, err := h.bridgeClient.Do(req)
      if err != nil {
          return "", fmt.Errorf("bridge unavailable: %w", err)
      }
      defer resp.Body.Close()

      if resp.StatusCode == http.StatusNotFound {
          return "", ErrAppNotFound
      }
      if resp.StatusCode != http.StatusOK {
          return "", fmt.Errorf("bridge error: HTTP %d", resp.StatusCode)
      }

      var info struct {
          CallbackURL string `json:"callback_url"`
      }
      if err := json.NewDecoder(resp.Body).Decode(&info); err != nil {
          return "", fmt.Errorf("bridge response decode: %w", err)
      }

      // Parse and validate — Bridge enforces this at registration, but defense-in-depth.
      // Prefix matching alone accepts "http:///path" (empty host).
      parsed, parseErr := url.Parse(info.CallbackURL)
      if parseErr != nil || (parsed.Scheme != "http" && parsed.Scheme != "https") || parsed.Host == "" {
          return "", fmt.Errorf("bridge returned callback_url with disallowed scheme or empty host: %q", info.CallbackURL)
      }
      return info.CallbackURL, nil
  }

  func (h *AppProxyHandler) proxyRequest(w http.ResponseWriter, r *http.Request, targetURL, userID string) {
      outReq, err := http.NewRequestWithContext(r.Context(), r.Method, targetURL, r.Body)
      if err != nil {
          http.Error(w, "proxy error", http.StatusInternalServerError)
          return
      }

      // Copy headers, then sanitize.
      outReq.Header = r.Header.Clone()
      outReq.Header.Set("X-User-ID", userID)

      // Strip Cloudflare auth credentials — apps must not receive or trust them.
      outReq.Header.Del("Cf-Access-Jwt-Assertion")
      outReq.Header.Del("Cookie")      // includes CF_Authorization cookie
      outReq.Header.Del("Authorization")

      // Strip hop-by-hop headers — these are connection-scoped and must not be forwarded.
      for _, h := range hopByHopHeaders {
          outReq.Header.Del(h)
      }

      resp, err := h.appClient.Do(outReq)
      if err != nil {
          http.Error(w, "app unavailable", http.StatusBadGateway)
          return
      }
      defer resp.Body.Close()

      // Copy response headers, stripping hop-by-hop.
      for key, vals := range resp.Header {
          if isHopByHop(key) {
              continue
          }
          for _, v := range vals {
              w.Header().Add(key, v)
          }
      }
      w.WriteHeader(resp.StatusCode)
      io.Copy(w, resp.Body) //nolint:errcheck
  }

  func isHopByHop(header string) bool {
      for _, h := range hopByHopHeaders {
          if strings.EqualFold(header, h) {
              return true
          }
      }
      return false
  }
  ```

- [ ] **Step 8: Run appproxy tests**

  ```bash
  cd gateway && go test ./appproxy/... -v
  ```

  Expected: `TestServeAPIReturns401WithoutClaims` and `TestServeAPIReturns404ForEmptyAppID` PASS without Redis. Redis-dependent tests skip if Redis is down, PASS if Redis is available.

- [ ] **Step 9: Register /api/ routes in gateway/main.go**

  Add import:
  ```go
  "belgrade-os/gateway/appproxy"
  ```

  After constructing `uiH`, add:
  ```go
  proxyH := appproxy.NewHandler(cfg.BridgeURL, rClient)
  ```

  After the UI route, add:
  ```go
  // Direct app action routes — auth-gated, RBAC-enforced, no inference stream.
  mux.Handle("GET /api/", uiMiddleware(http.HandlerFunc(proxyH.ServeAPI)))
  mux.Handle("POST /api/", uiMiddleware(http.HandlerFunc(proxyH.ServeAPI)))
  mux.Handle("PUT /api/", uiMiddleware(http.HandlerFunc(proxyH.ServeAPI)))
  mux.Handle("DELETE /api/", uiMiddleware(http.HandlerFunc(proxyH.ServeAPI)))
  mux.Handle("PATCH /api/", uiMiddleware(http.HandlerFunc(proxyH.ServeAPI)))
  ```

- [ ] **Step 10: Update scripts/seed_permissions.py to include `api` bundle**

  Find the `apps` list in `seed()`:
  ```python
  apps = [
      ('shopping', 'web', 'admin'),
      ('shopping', 'mobile', 'admin'),
      ('demo_app', 'web', 'admin'),
      ('dashboard', 'web', 'admin'),
  ]
  ```

  Replace with:
  ```python
  apps = [
      ('shopping', 'web', 'admin'),
      ('shopping', 'mobile', 'admin'),
      ('shopping', 'api', 'admin'),
      ('demo_app', 'web', 'admin'),
      ('demo_app', 'api', 'admin'),
      ('dashboard', 'web', 'admin'),
      ('dashboard', 'api', 'admin'),
  ]
  ```

- [ ] **Step 11: Run full gateway test suite**

  ```bash
  cd gateway && go test ./... -v
  ```

  Expected: all tests PASS.

- [ ] **Step 12: Commit**

  ```bash
  git add gateway/config.go gateway/config_test.go gateway/appproxy/handler.go \
    gateway/appproxy/handler_test.go gateway/main.go scripts/seed_permissions.py
  git commit -m "feat(gateway): /api/{app_id}/... proxy — RBAC, header sanitization, scheme validation; seed api bundle"
  ```

---

## Self-Review

### Spec coverage check

| Requirement | Covered by |
|---|---|
| Doc: Gateway is not the inference API | Task 1 |
| Doc: Inference is internal, apps own workflows | Task 1 |
| Doc: app-owned inference is always UNTRUSTED | Task 1 + Task 4 |
| Inference overrides execution_mode=UNTRUSTED for app tasks (defense-in-depth) | Task 5 (`effective_execution_mode`) |
| Doc: Bridge callback URLs are internal trust boundary | Task 1 + Task 7 |
| SSE subscribe-only endpoint documented as capability-token model with caveats | Task 6 (inline note) |
| r.PathValue tested through ServeMux (not direct handler call) | Task 6 (`TestStreamTaskPathValuePopulatedByMux`) |
| Mark /v1/tasks as legacy/internal | Task 1 |
| Task metadata includes app_id, tenant_id, execution_mode | Task 2 + Task 4 |
| App-owned inference enqueues to Redis, not HTTP | Task 4 |
| Inference propagates tenant_id, execution_mode into ToolCall | Task 5 |
| Result path for app-owned inference | Task 6 (subscribe-only SSE) |
| Redis ACL allows app user to write tasks:inbound | Task 3 |
| Gateway app action routing /api/{app_id}/... | Task 8 |
| Gateway enforces RBAC before forwarding | Task 8 |
| RBAC api bundle is seeded | Task 8 (seed_permissions.py) |
| Cloudflare JWT + Cookie + Authorization stripped from proxy | Task 8 |
| Hop-by-hop headers stripped from proxy request and response | Task 8 |
| Bridge validates callback URL scheme on registration | Task 7 |
| Gateway validates callback URL scheme before proxying | Task 8 |
| No Gateway→Inference HTTP dependency | Verified: no new HTTP calls to inference in any task |
| Tests pass for touched services | Task 1 (go test), Task 2 (make build + go test), Task 3 (manual verify), Task 4 (pytest), Task 5 (pytest), Task 6 (go test), Task 7 (cargo test), Task 8 (go test) |

### Placeholder scan

No TBD, TODO, or "similar to Task N" patterns. All code blocks are complete.

### Type consistency

- `InferenceAdapter.request(prompt)` — no `execution_mode` parameter; always `UNTRUSTED`. Consistent across Task 4 implementation and tests.
- `AppProxyHandler.ServeAPI` defined in Task 8 Step 7, registered in Task 8 Step 9.
- `appproxy.NewHandler(bridgeURL string, rClient *redis.RedisClient)` — consistent across handler.go and main.go.
- `hopByHopHeaders` slice defined once in handler.go, used by `proxyRequest` and `isHopByHop`. No duplication.
- `Handler.StreamTask` defined in Task 6 Step 3, registered in Task 6 Step 4.
- `r.PathValue("task_id")` — requires Go 1.22+; project uses Go 1.26.2 per `gateway/go.mod`.
- Proto field numbers (7=app_id, 8=tenant_id) consistent between Task 2 (proto) and Task 4 (SDK, via generated code) and Task 5 (inference, via generated code).
- `ToolCall.execution_mode` — field 8 already defined in proto (Security Track B). Task 5 sets it from `task.execution_mode`.
