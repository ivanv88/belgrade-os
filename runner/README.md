# Resource Runner

Executes trusted tool calls. Reads from the tool call stream, forwards each call to Bridge for dispatch to the registered app, and writes the result back to Redis.

## Role

- Consumes tool calls from `tasks:tool_calls` (XREADGROUP)
- Acquires a worker lease (`lease:{worker_id}`) for the duration of each call — signals in-flight work
- POSTs each call to Bridge `/v1/execute`, which looks up the registered app and forwards to its `/execute` callback
- Writes the `ToolResult` proto back to `tasks:tool_results` for the Inference Controller to pick up
- Releases the lease on completion or error

## Trust model

Only handles `TRUSTED` tasks. Untrusted (app-owned) tool calls are routed to `tasks:untrusted_calls` and handled by the Platform Controller's EphemeralRunner instead.

## Transport

| Direction | Channel | Notes |
|---|---|---|
| Read | `tasks:tool_calls` (Redis stream, XREADGROUP) | Trusted tool calls from Inference Controller |
| Write | `tasks:tool_results` (Redis stream) | Tool results back to Inference Controller |
| Write/Delete | `lease:{worker_id}` (Redis key, TTL) | In-flight worker lease |
| HTTP out | Bridge `/v1/execute` | Dispatches tool call to registered app |
