# Belgrade SDK

Python base class and context API for building Belgrade apps. Apps subclass `BelgradeApp`, register tools with decorators, and interact with the platform through the `AppContext` passed to each tool handler.

## Role

- `BelgradeApp`: wraps FastAPI, registers tools and event subscriptions with Bridge at startup, exposes `/execute` and `/events` callbacks
- `AppContext`: per-request context handed to tool handlers; provides `ctx.db`, `ctx.notify()`, `ctx.emit()`, `ctx.io` (vault), and `ctx.inference`
- Tool functions are decorated with `@app.tool(name, description)` and receive `(ctx, **kwargs)` — no direct Redis or HTTP handling needed
- Event handlers subscribe to Bridge topics via `@app.on_event(topic)`

## AppContext capabilities

| Accessor | What it does |
|---|---|
| `ctx.db` | AsyncSession scoped to the request |
| `ctx.notify(title, body)` | Writes `NotificationRequest` to `tasks:notifications` stream |
| `ctx.emit(topic, payload)` | POSTs to Bridge `/v1/events/publish` for fan-out to subscribers |
| `ctx.io.write(path, content)` | Writes `VaultOperation` to `tasks:vault_ops` stream (atomic, conflict-free) |
| `ctx.io.delete(path)` | Same stream, DELETE op |
| `ctx.inference.request(prompt)` | Submits inference task via Bridge `/v1/infer` (always UNTRUSTED) |
| `ctx.inference.stream(task_id)` | Async generator of `ThoughtEvent` chunks from `sse:{task_id}` pub/sub |
| `ctx.inference.await_result(task_id)` | Awaits completion, returns text / event list / `InferenceResult` |
| `ctx.inference.cancel(task_id)` | Sets `tasks:cancel:{task_id}` key |

## Exceptions

- `InferenceError` — base for inference failures
- `InferenceTimeoutError(InferenceError)` — raised by `stream()` / `await_result()` on timeout
