# Workflow Inference Support Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let SDK apps await inference results, stream ThoughtEvents, and cancel in-flight tasks — enabling app-owned multi-step workflows.

**Architecture:** `InferenceAdapter` gains `stream()` (async generator over `sse:{task_id}` pub/sub), `await_result()` (drives stream to completion, returns shaped result), and `cancel()` (sets `tasks:cancel:{task_id}` Redis key). The inference controller checks the cancel key at the top of each tool loop iteration. Two new exception classes (`InferenceError`, `InferenceTimeoutError`) are added to a new `exceptions.py` module. `RedisClient` gains `get()` and `delete()` to support the cancel check.

**Tech Stack:** Python / redis.asyncio pub/sub / asyncio / pytest-asyncio / proto3

---

## File Map

| Action | Path | Purpose |
|---|---|---|
| Modify | `config/redis.acl.template` | Grant app user `+set +del ~tasks:cancel:*`; inference user `+get +del` |
| Modify | `setup.sh` | Same ACL change in `generate_acl()` |
| Modify | `inference/redis_client.py` | Add `get()` and `delete()` methods |
| Modify | `inference/tests/test_redis_client.py` | Tests for `get()` and `delete()` |
| Modify | `inference/worker.py` | Cancel key check at top of tool loop |
| Modify | `inference/tests/test_worker.py` | Two cancel tests; update `_make_redis()` |
| Create | `sdk/belgrade_sdk/exceptions.py` | `InferenceError`, `InferenceTimeoutError` |
| Modify | `sdk/belgrade_sdk/context.py` | `InferenceResult` dataclass; `stream()`, `await_result()`, `cancel()` on `InferenceAdapter` |
| Modify | `sdk/tests/test_inference.py` | 8 new tests for the new SDK methods |

---

## Task 1: Redis ACL — grant cancel key access

**Files:**
- Modify: `config/redis.acl.template:8` (inference user), `config/redis.acl.template:10` (app user)
- Modify: `setup.sh:111` (inference user), `setup.sh:117` (app user)

- [ ] **Step 1: Update `config/redis.acl.template`**

  Find the inference user line:
  ```
  user inference on >INFERENCE_REDIS_PASSWORD ~tasks:* &sse:* +ping +xreadgroup +xread +xadd +xack +xgroup +publish
  ```
  Replace with:
  ```
  user inference on >INFERENCE_REDIS_PASSWORD ~tasks:* &sse:* +ping +xreadgroup +xread +xadd +xack +xgroup +publish +get +del
  ```

  Find the app user line:
  ```
  user app on >APP_REDIS_PASSWORD ~tasks:inbound ~tasks:vault_ops ~tasks:notifications +ping +xadd
  ```
  Replace with:
  ```
  user app on >APP_REDIS_PASSWORD ~tasks:inbound ~tasks:vault_ops ~tasks:notifications ~tasks:cancel:* +ping +xadd +set +del
  ```

- [ ] **Step 2: Update `setup.sh` `generate_acl()` — same two lines**

  Find (line 111):
  ```bash
  user inference on >${INFERENCE_PASS} ~tasks:* &sse:* +ping +xreadgroup +xread +xadd +xack +xgroup +publish
  ```
  Replace with:
  ```bash
  user inference on >${INFERENCE_PASS} ~tasks:* &sse:* +ping +xreadgroup +xread +xadd +xack +xgroup +publish +get +del
  ```

  Find (line 117):
  ```bash
  user app on >${APP_PASS} ~tasks:inbound ~tasks:vault_ops ~tasks:notifications +ping +xadd
  ```
  Replace with:
  ```bash
  user app on >${APP_PASS} ~tasks:inbound ~tasks:vault_ops ~tasks:notifications ~tasks:cancel:* +ping +xadd +set +del
  ```

- [ ] **Step 3: Verify**

  ```bash
  grep "user inference\|user app" /Users/ivanvladisavljevic/Projects/belgrade-os/config/redis.acl.template /Users/ivanvladisavljevic/Projects/belgrade-os/setup.sh
  ```

  Expected: both files show `+get +del` on inference and `~tasks:cancel:* +set +del` on app.

- [ ] **Step 4: Commit**

  ```bash
  cd /Users/ivanvladisavljevic/Projects/belgrade-os
  git add config/redis.acl.template setup.sh
  git commit -m "fix(acl): grant inference +get +del; app user ~tasks:cancel:* +set +del"
  ```

---

## Task 2: `RedisClient.get()` and `delete()`

**Files:**
- Modify: `inference/redis_client.py`
- Modify: `inference/tests/test_redis_client.py`

- [ ] **Step 1: Write the failing tests**

  Add to `inference/tests/test_redis_client.py` just before the end of the file:

  ```python
  # ---------------------------------------------------------------------------
  # get / delete
  # ---------------------------------------------------------------------------

  async def test_get_returns_decoded_string():
      client, mock_redis = _make_client()
      mock_redis.get = AsyncMock(return_value=b"1")
      result = await client.get("tasks:cancel:t1")
      assert result == "1"
      mock_redis.get.assert_awaited_once_with("tasks:cancel:t1")


  async def test_get_returns_none_when_key_missing():
      client, mock_redis = _make_client()
      mock_redis.get = AsyncMock(return_value=None)
      result = await client.get("tasks:cancel:t1")
      assert result is None


  async def test_delete_calls_redis_delete():
      client, mock_redis = _make_client()
      mock_redis.delete = AsyncMock()
      await client.delete("tasks:cancel:t1")
      mock_redis.delete.assert_awaited_once_with("tasks:cancel:t1")
  ```

- [ ] **Step 2: Run tests to confirm they fail**

  ```bash
  cd /Users/ivanvladisavljevic/Projects/belgrade-os/inference && python3 -m pytest tests/test_redis_client.py::test_get_returns_decoded_string -v
  ```

  Expected: `FAILED` — `RedisClient` has no `get` method.

- [ ] **Step 3: Add `get()` and `delete()` to `RedisClient`**

  In `inference/redis_client.py`, add just before `async def close()`:

  ```python
  async def get(self, key: str) -> Optional[str]:
      """GET key, returns decoded string or None."""
      value = await self._redis.get(key)
      if value is None:
          return None
      return value.decode() if isinstance(value, bytes) else value

  async def delete(self, key: str) -> None:
      """DEL key."""
      await self._redis.delete(key)
  ```

- [ ] **Step 4: Run all three new tests**

  ```bash
  cd /Users/ivanvladisavljevic/Projects/belgrade-os/inference && python3 -m pytest tests/test_redis_client.py -v 2>&1 | tail -15
  ```

  Expected: all existing tests pass, 3 new tests pass.

- [ ] **Step 5: Commit**

  ```bash
  cd /Users/ivanvladisavljevic/Projects/belgrade-os
  git add inference/redis_client.py inference/tests/test_redis_client.py
  git commit -m "feat(inference): RedisClient.get() and delete() for cancel key support"
  ```

---

## Task 3: Inference controller — cancel check

**Files:**
- Modify: `inference/worker.py:64` (top of `while True:` loop)
- Modify: `inference/tests/test_worker.py` (`_make_redis()` + 2 new tests)

- [ ] **Step 1: Update `_make_redis()` and write failing tests**

  In `inference/tests/test_worker.py`, update `_make_redis()` (currently returns `AsyncMock()` with only `push_untrusted_tool_call`):

  ```python
  def _make_redis() -> AsyncMock:
      mock = AsyncMock()
      mock.push_untrusted_tool_call = AsyncMock()
      mock.get = AsyncMock(return_value=None)   # no cancel key by default
      mock.delete = AsyncMock()
      return mock
  ```

  Then add two new tests just before the end of the file:

  ```python
  # ---------------------------------------------------------------------------
  # Cancel key tests
  # ---------------------------------------------------------------------------

  async def test_cancel_key_stops_loop_and_publishes_error():
      task = _make_task(task_id="t-cancel", trace_id="tr-c")
      mock_redis = _make_redis()
      mock_redis.get = AsyncMock(return_value="1")   # cancel key is set

      provider = MagicMock()   # generate should never be called

      await process_task(task, mock_redis, provider, "worker-1")

      # Cancel key was deleted
      mock_redis.delete.assert_awaited_once_with("tasks:cancel:t-cancel")

      # ERROR event published, content is "cancelled"
      events = _parse_published_events(mock_redis)
      assert len(events) == 1
      assert events[0].type == belgrade_os_pb2.ERROR
      assert events[0].content == "cancelled"
      assert events[0].task_id == "t-cancel"
      assert events[0].trace_id == "tr-c"

      # Provider was never invoked
      provider.generate.assert_not_called()


  async def test_no_cancel_key_proceeds_normally():
      from providers.base import TextChunk, StreamDone

      task = _make_task(task_id="t-no-cancel")
      mock_redis = _make_redis()
      # get returns None (default) — no cancel key

      provider = await _provider_from_events([
          [TextChunk("hi"), StreamDone("end_turn")],
      ])

      await process_task(task, mock_redis, provider, "worker-1")

      mock_redis.delete.assert_not_awaited()
      events = _parse_published_events(mock_redis)
      done_events = [e for e in events if e.type == belgrade_os_pb2.DONE]
      assert len(done_events) == 1
  ```

- [ ] **Step 2: Run tests to confirm they fail**

  ```bash
  cd /Users/ivanvladisavljevic/Projects/belgrade-os/inference && python3 -m pytest tests/test_worker.py::test_cancel_key_stops_loop_and_publishes_error -v
  ```

  Expected: `FAILED` — `process_task` never checks a cancel key.

- [ ] **Step 3: Add cancel check to `inference/worker.py`**

  In `inference/worker.py`, the `while True:` loop starts at line 64. Add the cancel check as the very first thing inside that loop, before `async for event in provider.generate(...)`:

  ```python
  try:
      while True:
          # Check for cancellation before each inference round.
          if await redis.get(f"tasks:cancel:{task.task_id}"):
              await redis.delete(f"tasks:cancel:{task.task_id}")
              error_event = belgrade_os_pb2.ThoughtEvent(
                  task_id=task.task_id,
                  user_id=task.user_id,
                  trace_id=task.trace_id,
                  type=belgrade_os_pb2.ERROR,
                  content="cancelled",
              )
              await redis.publish_thought(task.task_id, error_event.SerializeToString())
              return

          async for event in provider.generate(messages, tools or None):
  ```

  The rest of the function is unchanged.

- [ ] **Step 4: Run all worker tests**

  ```bash
  cd /Users/ivanvladisavljevic/Projects/belgrade-os/inference && python3 -m pytest tests/test_worker.py -v 2>&1 | tail -15
  ```

  Expected: all existing tests pass, 2 new cancel tests pass.

- [ ] **Step 5: Commit**

  ```bash
  cd /Users/ivanvladisavljevic/Projects/belgrade-os
  git add inference/worker.py inference/tests/test_worker.py
  git commit -m "feat(inference): cancel check at top of tool loop — publishes ERROR and exits"
  ```

---

## Task 4: SDK exceptions and `cancel()`

**Files:**
- Create: `sdk/belgrade_sdk/exceptions.py`
- Modify: `sdk/belgrade_sdk/context.py` (add `cancel()` to `InferenceAdapter`)
- Modify: `sdk/tests/test_inference.py` (2 new tests)

- [ ] **Step 1: Write the failing tests**

  Add to `sdk/tests/test_inference.py` (after existing imports, add `from belgrade_sdk.exceptions import InferenceError, InferenceTimeoutError` at the top once the file exists — for now write the tests and they'll fail on import):

  ```python
  async def test_cancel_sets_redis_key_with_ttl():
      mock_pool = AsyncMock()
      mock_pool.set = AsyncMock()
      ctx = _make_ctx(redis_pool=mock_pool)
      await ctx.inference.cancel("task-abc")
      mock_pool.set.assert_awaited_once_with("tasks:cancel:task-abc", "1", ex=3600)


  async def test_cancel_raises_without_redis():
      ctx = _make_ctx(redis_pool=None)
      with pytest.raises(RuntimeError, match="Redis pool not initialized"):
          await ctx.inference.cancel("task-abc")
  ```

  Also add to the imports block at the top of `sdk/tests/test_inference.py`:
  ```python
  from unittest.mock import AsyncMock, MagicMock
  ```
  (if `MagicMock` is not already imported — check and add only what's missing).

- [ ] **Step 2: Run tests to confirm they fail**

  ```bash
  cd /Users/ivanvladisavljevic/Projects/belgrade-os/sdk && python3 -m pytest tests/test_inference.py::test_cancel_sets_redis_key_with_ttl -v
  ```

  Expected: `FAILED` — `InferenceAdapter` has no `cancel` method.

- [ ] **Step 3: Create `sdk/belgrade_sdk/exceptions.py`**

  ```python
  class InferenceError(RuntimeError):
      """Raised when the inference controller publishes an ERROR event."""


  class InferenceTimeoutError(RuntimeError):
      """Raised when no terminal event arrives within the configured timeout."""
  ```

- [ ] **Step 4: Add `cancel()` to `InferenceAdapter` in `sdk/belgrade_sdk/context.py`**

  Add this method to `InferenceAdapter`, after `request()`:

  ```python
  async def cancel(self, task_id: str) -> None:
      if not self.ctx._redis_pool:
          raise RuntimeError("Redis pool not initialized in AppContext")
      await self.ctx._redis_pool.set(f"tasks:cancel:{task_id}", "1", ex=3600)
  ```

- [ ] **Step 5: Run new tests**

  ```bash
  cd /Users/ivanvladisavljevic/Projects/belgrade-os/sdk && python3 -m pytest tests/test_inference.py -v 2>&1 | tail -15
  ```

  Expected: all existing tests pass, 2 new cancel tests pass.

- [ ] **Step 6: Commit**

  ```bash
  cd /Users/ivanvladisavljevic/Projects/belgrade-os
  git add sdk/belgrade_sdk/exceptions.py sdk/belgrade_sdk/context.py sdk/tests/test_inference.py
  git commit -m "feat(sdk): InferenceError/InferenceTimeoutError exceptions; InferenceAdapter.cancel()"
  ```

---

## Task 5: SDK `stream()`

**Files:**
- Modify: `sdk/belgrade_sdk/context.py` (add `InferenceResult` dataclass + `stream()`)
- Modify: `sdk/tests/test_inference.py` (4 new tests)

- [ ] **Step 1: Write the failing tests**

  Add to `sdk/tests/test_inference.py`. First add missing imports at the top:

  ```python
  import asyncio
  from unittest.mock import AsyncMock, MagicMock
  from belgrade_sdk.exceptions import InferenceError, InferenceTimeoutError
  ```

  Then add the four tests:

  ```python
  def _make_pubsub_mock(messages: list) -> AsyncMock:
      """Build a mock pubsub that returns messages in order then None forever."""
      mock_pubsub = AsyncMock()
      mock_pubsub.subscribe = AsyncMock()
      mock_pubsub.unsubscribe = AsyncMock()
      side_effects = messages + [None] * 10
      mock_pubsub.get_message = AsyncMock(side_effect=side_effects)
      return mock_pubsub


  async def test_stream_yields_events_until_done():
      from belgrade_sdk.gen import belgrade_os_pb2

      chunk = belgrade_os_pb2.ThoughtEvent(
          task_id="t1", type=belgrade_os_pb2.RESPONSE_CHUNK, content="hello"
      )
      done_evt = belgrade_os_pb2.ThoughtEvent(
          task_id="t1", type=belgrade_os_pb2.DONE
      )
      mock_pubsub = _make_pubsub_mock([
          {"type": "message", "data": chunk.SerializeToString()},
          {"type": "message", "data": done_evt.SerializeToString()},
      ])
      mock_pool = AsyncMock()
      mock_pool.pubsub = MagicMock(return_value=mock_pubsub)

      ctx = _make_ctx(redis_pool=mock_pool)
      events = []
      async for event in ctx.inference.stream("t1"):
          events.append(event)

      assert len(events) == 2
      assert events[0].type == belgrade_os_pb2.RESPONSE_CHUNK
      assert events[0].content == "hello"
      assert events[1].type == belgrade_os_pb2.DONE
      mock_pubsub.subscribe.assert_awaited_once_with("sse:t1")
      mock_pubsub.unsubscribe.assert_awaited_once_with("sse:t1")


  async def test_stream_yields_error_event_and_terminates():
      from belgrade_sdk.gen import belgrade_os_pb2

      error_evt = belgrade_os_pb2.ThoughtEvent(
          task_id="t1", type=belgrade_os_pb2.ERROR, content="API failure"
      )
      mock_pubsub = _make_pubsub_mock([
          {"type": "message", "data": error_evt.SerializeToString()},
      ])
      mock_pool = AsyncMock()
      mock_pool.pubsub = MagicMock(return_value=mock_pubsub)

      ctx = _make_ctx(redis_pool=mock_pool)
      events = []
      async for event in ctx.inference.stream("t1"):
          events.append(event)

      assert len(events) == 1
      assert events[0].type == belgrade_os_pb2.ERROR
      assert events[0].content == "API failure"
      mock_pubsub.unsubscribe.assert_awaited_once_with("sse:t1")


  async def test_stream_raises_timeout_when_no_terminal_event():
      mock_pubsub = _make_pubsub_mock([])   # always returns None
      mock_pool = AsyncMock()
      mock_pool.pubsub = MagicMock(return_value=mock_pubsub)

      ctx = _make_ctx(redis_pool=mock_pool)
      with pytest.raises(InferenceTimeoutError):
          async for _ in ctx.inference.stream("t1", timeout=0.001):
              pass
      mock_pubsub.unsubscribe.assert_awaited_once_with("sse:t1")


  async def test_stream_raises_without_redis():
      ctx = _make_ctx(redis_pool=None)
      with pytest.raises(RuntimeError, match="Redis pool not initialized"):
          async for _ in ctx.inference.stream("t1"):
              pass
  ```

- [ ] **Step 2: Run tests to confirm they fail**

  ```bash
  cd /Users/ivanvladisavljevic/Projects/belgrade-os/sdk && python3 -m pytest tests/test_inference.py::test_stream_yields_events_until_done -v
  ```

  Expected: `FAILED` — `InferenceAdapter` has no `stream` method.

- [ ] **Step 3: Add `InferenceResult` dataclass and `stream()` to `sdk/belgrade_sdk/context.py`**

  First, update the imports at the top of `context.py`:

  ```python
  from __future__ import annotations
  import asyncio
  import logging
  import time
  import uuid as _uuid
  from dataclasses import dataclass
  from typing import Any, AsyncGenerator, Optional, Union
  import httpx
  from sqlalchemy.ext.asyncio import AsyncSession, AsyncEngine, async_sessionmaker
  from sqlalchemy import text
  from redis.asyncio import Redis
  from . import defaults
  ```

  Then add the `InferenceResult` dataclass just before the `InferenceAdapter` class:

  ```python
  @dataclass
  class InferenceResult:
      text: str
      tool_calls: list[str]
      trace_id: str
  ```

  Then add `stream()` to `InferenceAdapter`, after `cancel()`:

  ```python
  async def stream(self, task_id: str, timeout: float = 300.0):
      from .gen import belgrade_os_pb2
      from .exceptions import InferenceTimeoutError

      if not self.ctx._redis_pool:
          raise RuntimeError("Redis pool not initialized in AppContext")

      pubsub = self.ctx._redis_pool.pubsub()
      await pubsub.subscribe(f"sse:{task_id}")
      try:
          deadline = asyncio.get_event_loop().time() + timeout
          while True:
              remaining = deadline - asyncio.get_event_loop().time()
              if remaining <= 0:
                  raise InferenceTimeoutError(
                      f"No terminal event for task {task_id} within {timeout}s"
                  )
              try:
                  message = await asyncio.wait_for(
                      pubsub.get_message(ignore_subscribe_messages=True, timeout=0.5),
                      timeout=min(remaining, 2.0),
                  )
              except asyncio.TimeoutError:
                  continue
              if message is None:
                  continue
              event = belgrade_os_pb2.ThoughtEvent()
              event.ParseFromString(message["data"])
              yield event
              if event.type in (belgrade_os_pb2.DONE, belgrade_os_pb2.ERROR):
                  return
      finally:
          await pubsub.unsubscribe(f"sse:{task_id}")
  ```

- [ ] **Step 4: Run all SDK tests**

  ```bash
  cd /Users/ivanvladisavljevic/Projects/belgrade-os/sdk && python3 -m pytest tests/test_inference.py -v 2>&1 | tail -20
  ```

  Expected: all existing tests pass, 4 new stream tests pass.

- [ ] **Step 5: Commit**

  ```bash
  cd /Users/ivanvladisavljevic/Projects/belgrade-os
  git add sdk/belgrade_sdk/context.py sdk/tests/test_inference.py
  git commit -m "feat(sdk): InferenceAdapter.stream() — async generator over sse:{task_id} pub/sub"
  ```

---

## Task 6: SDK `await_result()`

**Files:**
- Modify: `sdk/belgrade_sdk/context.py` (add `await_result()`)
- Modify: `sdk/tests/test_inference.py` (4 new tests)

- [ ] **Step 1: Write the failing tests**

  Add to `sdk/tests/test_inference.py`:

  ```python
  async def test_await_result_text_mode_concatenates_chunks():
      from belgrade_sdk.gen import belgrade_os_pb2

      chunk1 = belgrade_os_pb2.ThoughtEvent(
          task_id="t1", type=belgrade_os_pb2.RESPONSE_CHUNK, content="Hello"
      )
      chunk2 = belgrade_os_pb2.ThoughtEvent(
          task_id="t1", type=belgrade_os_pb2.RESPONSE_CHUNK, content=" world"
      )
      done_evt = belgrade_os_pb2.ThoughtEvent(task_id="t1", type=belgrade_os_pb2.DONE)
      mock_pubsub = _make_pubsub_mock([
          {"type": "message", "data": chunk1.SerializeToString()},
          {"type": "message", "data": chunk2.SerializeToString()},
          {"type": "message", "data": done_evt.SerializeToString()},
      ])
      mock_pool = AsyncMock()
      mock_pool.pubsub = MagicMock(return_value=mock_pubsub)

      ctx = _make_ctx(redis_pool=mock_pool)
      result = await ctx.inference.await_result("t1", mode="text")
      assert result == "Hello world"


  async def test_await_result_full_mode_returns_all_events():
      from belgrade_sdk.gen import belgrade_os_pb2

      chunk = belgrade_os_pb2.ThoughtEvent(
          task_id="t1", type=belgrade_os_pb2.RESPONSE_CHUNK, content="hi"
      )
      done_evt = belgrade_os_pb2.ThoughtEvent(task_id="t1", type=belgrade_os_pb2.DONE)
      mock_pubsub = _make_pubsub_mock([
          {"type": "message", "data": chunk.SerializeToString()},
          {"type": "message", "data": done_evt.SerializeToString()},
      ])
      mock_pool = AsyncMock()
      mock_pool.pubsub = MagicMock(return_value=mock_pubsub)

      ctx = _make_ctx(redis_pool=mock_pool)
      result = await ctx.inference.await_result("t1", mode="full")
      assert isinstance(result, list)
      assert len(result) == 2
      assert result[0].type == belgrade_os_pb2.RESPONSE_CHUNK
      assert result[1].type == belgrade_os_pb2.DONE


  async def test_await_result_raises_inference_error_on_error_event():
      from belgrade_sdk.gen import belgrade_os_pb2

      error_evt = belgrade_os_pb2.ThoughtEvent(
          task_id="t1", type=belgrade_os_pb2.ERROR, content="upstream failure"
      )
      mock_pubsub = _make_pubsub_mock([
          {"type": "message", "data": error_evt.SerializeToString()},
      ])
      mock_pool = AsyncMock()
      mock_pool.pubsub = MagicMock(return_value=mock_pubsub)

      ctx = _make_ctx(redis_pool=mock_pool)
      with pytest.raises(InferenceError, match="upstream failure"):
          await ctx.inference.await_result("t1")


  async def test_await_result_summary_mode_returns_inference_result():
      from belgrade_sdk.gen import belgrade_os_pb2
      from belgrade_sdk.context import InferenceResult

      chunk = belgrade_os_pb2.ThoughtEvent(
          task_id="t1", trace_id="tr1", type=belgrade_os_pb2.RESPONSE_CHUNK, content="Done."
      )
      done_evt = belgrade_os_pb2.ThoughtEvent(
          task_id="t1", trace_id="tr1", type=belgrade_os_pb2.DONE
      )
      mock_pubsub = _make_pubsub_mock([
          {"type": "message", "data": chunk.SerializeToString()},
          {"type": "message", "data": done_evt.SerializeToString()},
      ])
      mock_pool = AsyncMock()
      mock_pool.pubsub = MagicMock(return_value=mock_pubsub)

      ctx = _make_ctx(redis_pool=mock_pool)
      result = await ctx.inference.await_result("t1", mode="summary")
      assert isinstance(result, InferenceResult)
      assert result.text == "Done."
      assert result.tool_calls == []
      assert result.trace_id == "tr1"
  ```

- [ ] **Step 2: Run tests to confirm they fail**

  ```bash
  cd /Users/ivanvladisavljevic/Projects/belgrade-os/sdk && python3 -m pytest tests/test_inference.py::test_await_result_text_mode_concatenates_chunks -v
  ```

  Expected: `FAILED` — `InferenceAdapter` has no `await_result` method.

- [ ] **Step 3: Add `await_result()` to `InferenceAdapter` in `sdk/belgrade_sdk/context.py`**

  Add after `stream()`:

  ```python
  async def await_result(self, task_id: str, mode: str = "text", timeout: float = 300.0):
      from .gen import belgrade_os_pb2
      from .exceptions import InferenceError

      events = []
      async for event in self.stream(task_id, timeout=timeout):
          events.append(event)

      for event in events:
          if event.type == belgrade_os_pb2.ERROR:
              raise InferenceError(event.content)

      if mode == "text":
          return "".join(
              e.content for e in events if e.type == belgrade_os_pb2.RESPONSE_CHUNK
          )
      if mode == "full":
          return events
      if mode == "summary":
          text = "".join(
              e.content for e in events if e.type == belgrade_os_pb2.RESPONSE_CHUNK
          )
          tool_calls = [
              e.content for e in events if e.type == belgrade_os_pb2.TOOL_USE
          ]
          trace_id = events[0].trace_id if events else ""
          return InferenceResult(text=text, tool_calls=tool_calls, trace_id=trace_id)
      raise ValueError(f"Unknown mode {mode!r}. Use 'text', 'full', or 'summary'.")
  ```

- [ ] **Step 4: Run all SDK tests**

  ```bash
  cd /Users/ivanvladisavljevic/Projects/belgrade-os/sdk && python3 -m pytest tests/test_inference.py -v 2>&1 | tail -20
  ```

  Expected: all tests pass (existing 5 + cancel 2 + stream 4 + await_result 4 = 15 total).

- [ ] **Step 5: Commit**

  ```bash
  cd /Users/ivanvladisavljevic/Projects/belgrade-os
  git add sdk/belgrade_sdk/context.py sdk/tests/test_inference.py
  git commit -m "feat(sdk): InferenceAdapter.await_result() — text/full/summary modes; InferenceError on ERROR event"
  ```

---

## Self-Review

### Spec coverage

| Requirement | Task |
|---|---|
| `stream()` async generator over `sse:{task_id}` | Task 5 |
| `stream()` terminates on DONE or ERROR | Task 5 |
| `stream()` raises `InferenceTimeoutError` on timeout | Task 5 |
| `stream()` always unsubscribes on exit | Task 5 (finally block) |
| `await_result()` text mode | Task 6 |
| `await_result()` full mode | Task 6 |
| `await_result()` summary mode returns `InferenceResult` | Task 6 |
| `await_result()` raises `InferenceError` on ERROR event | Task 6 |
| `cancel()` sets `tasks:cancel:{task_id}` with TTL | Task 4 |
| `InferenceError`, `InferenceTimeoutError` exceptions | Task 4 |
| `RedisClient.get()` and `delete()` | Task 2 |
| Cancel check in inference tool loop | Task 3 |
| Cancel check deletes key after detecting it | Task 3 |
| ACL grants for app and inference users | Task 1 |
| Race condition documented (pub/sub, no persistence) | spec only — known limitation |

### Placeholder scan

No TBD, TODO, "similar to", or missing code blocks. All steps contain complete code.

### Type consistency

- `InferenceResult` defined in Task 5 (in `context.py`), imported in Task 6 test — consistent.
- `InferenceError` defined in Task 4 (`exceptions.py`), used in Task 6 (`await_result`) — consistent.
- `InferenceTimeoutError` defined in Task 4, used in Task 5 (`stream`) — consistent.
- `cancel(task_id)` defined in Task 4, referenced in Task 3 (cancel key format `tasks:cancel:{task_id}`) — key format matches ACL grant in Task 1 (`~tasks:cancel:*`) — consistent.
- `pubsub.unsubscribe(f"sse:{task_id}")` in Task 5 matches `pubsub.subscribe(f"sse:{task_id}")` — consistent.
- `_make_redis()` updated in Task 3 to set `mock.get = AsyncMock(return_value=None)` — all existing worker tests remain valid since they don't set a cancel key.
