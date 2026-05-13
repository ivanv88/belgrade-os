# Gateway

The authenticated edge of the system. All external traffic enters here — nothing reaches the platform without passing through this service.

## Role

- Validates Cloudflare Zero Trust JWTs on every request
- Routes inference requests to `tasks:inbound` (Redis)
- Proxies SSE streams from `sse:{task_id}` (Redis pub/sub) back to the browser
- Serves app static UI bundles from `apps/{app_id}/static/{bundle_id}/`
- Proxies direct app API calls (`/api/{app_id}/...`) to app processes via Bridge callback lookup, enforcing RBAC before forwarding

## Transport

| Direction | Channel | Notes |
|---|---|---|
| Write | `tasks:inbound` (Redis stream) | Inference task submission |
| Subscribe | `sse:{task_id}` (Redis pub/sub) | Streams ThoughtEvents to browser as SSE |
| Read | `perms:{user_id}` (Redis hash) | RBAC permission checks |
| HTTP out | Bridge `/v1/apps/:app_id` | Callback URL lookup for `/api/` proxy |
| HTTP out | App `/execute` callback | Direct app action proxy |

## HTTP Endpoints

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/v1/tasks` | Submit an inference task (legacy/internal) |
| `GET` | `/v1/tasks/{task_id}/stream` | Subscribe to SSE stream for app-owned tasks |
| `GET` | `/ui/{app_id}/{bundle_id}/...` | Serve static UI assets |
| `*` | `/api/{app_id}/...` | RBAC-checked proxy to app callback URL |

## Key env vars

| Var | Default | Notes |
|---|---|---|
| `PORT` | `8080` | |
| `REDIS_URL` | `redis://localhost:6379` | |
| `CF_TEAM_DOMAIN` | — | Required in production |
| `CF_AUDIENCE` | — | Required in production |
| `TRUSTED_USER_IDS` | — | Comma-separated; these users get `TRUSTED` execution mode |
| `BRIDGE_URL` | `http://localhost:8081` | |
