# Bridge

The capability registry and internal HTTP router. Apps register their tools here at startup; the Runner calls here to execute them.

## Role

- Stores the mapping of tool names → app callback URLs (write-through Redis cache)
- Dispatches tool execution: receives a tool call, looks up the registered callback URL, POSTs to the app's `/execute` endpoint
- Routes event publishing: receives `ctx.emit()` calls, fans out to all apps subscribed to that topic
- Exposes `/v1/notify` and `/v1/infer` as convenience HTTP endpoints for apps that want to trigger notifications or inference without writing to Redis directly
- Not exposed through Cloudflare — internal services only

## Transport

| Direction | Channel | Notes |
|---|---|---|
| Read | `bridge:*` (Redis hash) | Persisted tool registry, loaded on startup |
| Write | `bridge:*` (Redis hash) | Registry writes cached to Redis |
| HTTP in | `/v1/register` | Apps register their tools and callback URL here at startup |
| HTTP in | `/v1/execute` | Runner posts tool calls here for dispatch to app |
| HTTP in | `/v1/events/publish` | `ctx.emit()` posts here; Bridge fans out to subscribers |
| HTTP out | App `/execute` | Delivers tool call to the registered app |
| HTTP out | App `/events` | Delivers events to subscribed apps |

## HTTP Endpoints

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/v1/register` | Register app tools and callback URL |
| `GET` | `/v1/tools` | List all registered tools (used by Inference to build LLM tool list) |
| `POST` | `/v1/execute` | Execute a named tool call |
| `POST` | `/v1/events/publish` | Publish an event to all subscribers |
| `POST` | `/v1/notify` | Convenience: write a notification to `tasks:notifications` stream |
| `POST` | `/v1/infer` | Convenience: write a task to `tasks:inbound` stream (always UNTRUSTED) |
| `GET` | `/v1/apps/:app_id` | Look up registered callback URL for an app |
| `GET` | `/v1/notifications/provider` | Returns configured notification driver |
