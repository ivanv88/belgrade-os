# External App Integration Contract

An **external app** runs in its own container with its own database, but uses
platform capabilities (notifications, inference, tool registry) via the bridge.

---

## What the bridge provides

| Endpoint | Purpose |
|---|---|
| `POST /v1/register` | Register tools + obtain Bearer token |
| `POST /v1/execute` | Bridge calls this on YOUR app when a tool is invoked |
| `POST /v1/notify` | Send a notification to a user (requires Bearer token) |
| `POST /v1/infer` | Trigger inference (requires Bearer token) |
| `GET /v1/tools` | List all registered tools (debug) |
| `GET /v1/apps/:app_id` | Look up callback URL for an app |

---

## What your app must expose

```
POST /execute
```

Request body (JSON, sent by the bridge):
```json
{
  "tool_name": "myapp:do_thing",
  "input_json": "{\"key\": \"value\"}",
  "trace_id": "trace-abc",
  "user_id": "user@example.com",
  "tenant_id": "household-xyz"
}
```

Response body (JSON, returned to the bridge):
```json
{ "success": true, "output_json": "{\"result\": \"ok\"}", "error": "" }
```
or on failure:
```json
{ "success": false, "output_json": "", "error": "reason" }
```

---

## Registration

On startup, POST to `http://${BEG_OS_BRIDGE_URL}/v1/register`:

```json
{
  "app_id": "myapp",
  "callback_url": "http://myapp:8000",
  "tools": [
    {
      "name": "myapp:do_thing",
      "description": "Does the thing",
      "input_schema_json": "{\"type\":\"object\",\"properties\":{\"key\":{\"type\":\"string\"}}}"
    }
  ]
}
```

Response `200 OK`:
```json
{ "app_token": "a1b2c3...64hexchars" }
```

Store this token. Use it as `Authorization: Bearer <token>` on all subsequent
capability calls. Re-registering regenerates the token.

---

## Sending a notification

```
POST http://${BEG_OS_BRIDGE_URL}/v1/notify
Authorization: Bearer <app_token>
Content-Type: application/json

{
  "app_id": "myapp",
  "user_id": "user@example.com",
  "title": "Shopping list updated",
  "body": "3 items added based on this week's menu.",
  "priority": 2,
  "tags": ["shopping_cart"],
  "click_url": "https://beg-os.fyi/ui/shopping/web/",
  "trace_id": "trace-abc"
}
```

`priority`: 0 = unspecified, 1 = low, 2 = normal, 3 = high.
`tags`, `click_url`, `trace_id` are optional.

Response: `202 Accepted` (fire-and-forget — delivery is async).

---

## Triggering inference

```
POST http://${BEG_OS_BRIDGE_URL}/v1/infer
Authorization: Bearer <app_token>
Content-Type: application/json

{
  "app_id": "myapp",
  "user_id": "user@example.com",
  "prompt": "Summarise the week's activity.",
  "tenant_id": "household-xyz",
  "trace_id": "trace-abc"
}
```

`tenant_id` and `trace_id` are optional.

Response `202 Accepted`:
```json
{ "task_id": "app-<uuid>", "trace_id": "trace-abc" }
```

Pass `task_id` to the client so it can open `GET /v1/tasks/{task_id}/stream` on
the gateway for SSE results. Execution mode is always `UNTRUSTED` — cannot be
overridden by the caller.

---

## Required environment variables

| Variable | Value | Notes |
|---|---|---|
| `BEG_OS_APP_ID` | `myapp` | Must match `app_id` in registration |
| `BEG_OS_BRIDGE_URL` | `http://localhost:8081` | Bridge address |
| `BEG_OS_CALLBACK_URL` | `http://myapp:8000` | Where bridge calls `/execute` |

---

## Networking

The bridge runs as a bare process on the host. Your container must be able to
reach it, and the bridge must be able to reach your container's `/execute`.

Add your service to `core_net` in `docker-compose.yml`:

```yaml
services:
  myapp:
    image: ghcr.io/you/myapp:latest
    container_name: beg-os-myapp
    networks:
      - core_net
    environment:
      BEG_OS_APP_ID: myapp
      BEG_OS_BRIDGE_URL: http://172.17.0.1:8081   # Docker host gateway
      BEG_OS_CALLBACK_URL: http://myapp:8000       # container-to-container
```

> **Why `172.17.0.1` for `BEG_OS_BRIDGE_URL`?** The bridge runs on the host,
> not in a container. Containers reach the host via the Docker bridge gateway
> IP. On Linux this is typically `172.17.0.1`; check with
> `docker network inspect bridge | grep Gateway`. On macOS Docker Desktop,
> use `host.docker.internal` instead.

The bridge calls back to `BEG_OS_CALLBACK_URL/execute`. Because both your app
and the bridge know each other's addresses, use container hostnames for
container-to-container and the host gateway IP for container-to-host.

---

## Token lifecycle

- Issued at `POST /v1/register` — one token per app, regenerated on every registration.
- Stored in Redis under `bridge:token:{hex}` — survives bridge restarts (Redis persists).
- No expiry — invalidate by re-registering (new token replaces old).
- Keep the token out of logs and version control.
