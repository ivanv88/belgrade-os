# Workflow Inference Support — Design Spec

**Date:** 2026-05-13
**Status:** Approved

## Goal

Allow apps to drive stateful multi-step workflows by triggering inference and awaiting the result, with streaming support and cancellation.

---

## Context

`ctx.inference.request()` already returns `{"task_id", "trace_id"}` and the inference controller already publishes `ERROR` and `DONE` ThoughtEvents. The missing piece is the SDK-side: apps have no way to subscribe to `sse:{task_id}` and receive those events.

---

## What Changes

| Component | Change |
|---|---|
| `sdk/belgrade_sdk/context.py` | Add `stream()`, `await_result()`, `cancel()` to `InferenceAdapter` |
| `inference/worker.py` | Add cancel key check at top of each tool loop iteration |
| `config/redis.acl.template` + `setup.sh` | App user: `+set +del ~tasks:cancel:*`; inference user: `+get +del ~tasks:cancel:*` |

No new services. No proto changes. No new Redis streams.

---

## SDK — `InferenceAdapter`

### `stream(task_id, timeout=300)`

Async generator. Subscribes to `sse:{task_id}` on Redis pub/sub, decodes each message as a `ThoughtEvent` proto, and yields it. Terminates (`StopAsyncIteration`) when a `DONE` or `ERROR` event is received. Raises `InferenceTimeoutError` if no terminal event arrives within `timeout` seconds.

```python
async for event in ctx.inference.stream(task_id):
    if event.type == belgrade_os_pb2.RESPONSE_CHUNK:
        buffer += event.content
    elif event.type == belgrade_os_pb2.ERROR:
        raise MyWorkflowError(event.content)
```

The generator always unsubscribes on exit (normal, timeout, or exception).

**Race condition note:** Redis pub/sub does not persist messages. If `stream()` is called after the inference controller has already published DONE, the event will be missed and the stream will timeout. Apps must call `stream()` promptly after `request()` — in practice this is the natural pattern for a workflow step. A persistent result store is a future concern if needed.

### `await_result(task_id, mode="text", timeout=300)`

Drives `stream()` to completion and returns a result shaped by `mode`:

| mode | return type | description |
|---|---|---|
| `"text"` | `str` | Concatenated `RESPONSE_CHUNK` event content |
| `"full"` | `list[ThoughtEvent]` | All events in arrival order |
| `"summary"` | `InferenceResult` | `text: str`, `tool_calls: list[str]`, `trace_id: str` |

On `ERROR` event: raises `InferenceError(message)`.
On timeout: raises `InferenceTimeoutError(task_id)`.

`InferenceResult` is a small dataclass defined in `context.py` alongside `InferenceAdapter`. No separate file needed.

### `cancel(task_id)`

```python
await redis.set(f"tasks:cancel:{task_id}", "1", ex=3600)
```

Fire and forget. Returns immediately. The TTL ensures keys don't accumulate if the controller already finished. The app does not need to confirm cancellation — the ERROR event on the stream signals it.

### Exceptions

Two new exception classes, defined in `sdk/belgrade_sdk/exceptions.py`:

```python
class InferenceError(RuntimeError): pass      # ERROR event received
class InferenceTimeoutError(RuntimeError): pass  # timeout exceeded
```

---

## Inference Controller — Cancel Check

At the top of each tool loop iteration in `inference/worker.py`, before calling `provider.generate()`:

```python
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
```

The existing `except Exception` block already handles errors published on crash — no change needed there.

`RedisClient` (`inference/redis_client.py`) has no `get()` or `delete()` methods — both must be added:

```python
async def get(self, key: str) -> Optional[str]:
    return await self._redis.get(key)

async def delete(self, key: str) -> None:
    await self._redis.delete(key)
```

---

## Redis ACL

```
# config/redis.acl.template — app user line gains:
~tasks:cancel:* +set +del

# config/redis.acl.template — inference user line gains:
~tasks:cancel:* +get +del
```

The `tasks:cancel:*` key namespace fits within the existing `tasks:*` pattern already used by both users, so this is an additive key grant only.

---

## Data Flow

```
app                     SDK (InferenceAdapter)       Redis               Inference Controller
 │                              │                      │                          │
 │  request("plan this")        │                      │                          │
 ├─────────────────────────────►│  XADD tasks:inbound  │                          │
 │  {task_id, trace_id}         ├─────────────────────►│                          │
 │◄─────────────────────────────│                      │  consumes task            │
 │                              │                      │◄─────────────────────────┤
 │  stream(task_id)             │                      │                          │
 ├─────────────────────────────►│  SUBSCRIBE           │                          │
 │                              ├─────────────────────►│                          │
 │                              │                      │  PUBLISH RESPONSE_CHUNK  │
 │  yield RESPONSE_CHUNK        │◄─────────────────────┤◄─────────────────────────┤
 │◄─────────────────────────────│                      │                          │
 │                              │                      │  PUBLISH DONE            │
 │  yield DONE → stream ends    │◄─────────────────────┤◄─────────────────────────┤
 │◄─────────────────────────────│                      │                          │
 │                              │  UNSUBSCRIBE         │                          │
 │                              ├─────────────────────►│                          │
```

**Cancel path:** `cancel(task_id)` → `SET tasks:cancel:{task_id}` → inference controller sees key on next loop iteration → publishes ERROR → stream yields ERROR and terminates.

**Crash path:** inference controller hits unhandled exception → existing `except` block publishes ERROR → stream yields ERROR and terminates.

---

## Testing

### SDK tests (`sdk/tests/test_inference.py`)

- `test_stream_yields_events_until_done` — mock pub/sub yields THINKING + RESPONSE_CHUNK + DONE; assert all events yielded, generator stops
- `test_stream_yields_error_event` — mock yields ERROR event; assert it is yielded and stream stops
- `test_stream_raises_timeout` — mock never yields terminal event; assert `InferenceTimeoutError` after timeout
- `test_await_result_text_mode` — assert concatenated RESPONSE_CHUNK text returned
- `test_await_result_full_mode` — assert full list of ThoughtEvents returned
- `test_await_result_summary_mode` — assert `InferenceResult` with correct text and tool_calls
- `test_await_result_raises_on_error` — assert `InferenceError` raised when ERROR event received
- `test_cancel_sets_redis_key` — assert `SET tasks:cancel:{task_id}` called with correct TTL

### Inference controller tests (`inference/tests/test_worker.py`)

- `test_cancel_key_stops_loop` — set cancel key before calling `process_task`; assert ERROR event published, task exits cleanly
- `test_cancel_key_deleted_after_check` — assert `DEL tasks:cancel:{task_id}` called after detection

---

## What This Enables

An app can now drive a stateful multi-step workflow entirely within its own logic:

```python
# PLANNING step
task_id = (await ctx.inference.request("break into search queries: ..."))["task_id"]
plan = await ctx.inference.await_result(task_id, mode="text")

# FETCHING step (app runs its own tool calls, or triggers another inference)
task_id = (await ctx.inference.request(f"search for: {plan}"))["task_id"]
result = await ctx.inference.await_result(task_id, mode="summary")

# PUBLISHING step
await ctx.vault.write("research/output.md", result.text)
```

State persistence between steps (workflow instance table, current state) is app-owned — the platform provides no workflow state management. Apps use their own PostgreSQL schema for this.
