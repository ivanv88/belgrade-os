# Platform Controller

App lifecycle manager and untrusted execution sandbox. Supervises app processes, handles scheduled tasks, and runs untrusted (app-owned) tool calls in ephemeral Docker containers.

## Role

- Discovers apps under `APPS_ROOT`, starts each as a subprocess, and injects environment (Redis URL, bridge URL, DB URL, notification driver)
- Validates each app's `manifest.json` on startup; skips apps with invalid manifests
- Watchdog loop restarts any app that exits unexpectedly
- Consumes untrusted tool calls from `tasks:untrusted_calls` and runs them in isolated Docker containers via `EphemeralRunner` (seccomp-confined, per-call container)
- Manages `shared.schedules` table (PostgreSQL); runs cron jobs via APScheduler, dispatching via Bridge `/v1/execute`
- Syncs app RBAC permissions from `shared.app_permissions` → Redis hashes for Gateway enforcement

## Transport

| Direction | Channel | Notes |
|---|---|---|
| Read | `tasks:untrusted_calls` (Redis stream, XREADGROUP) | Untrusted tool calls from Inference Controller |
| Write | `tasks:tool_results` (Redis stream) | Results back to Inference Controller |
| HTTP out | Bridge `/v1/execute` | Scheduled task dispatch |
| HTTP in | `/apps/reload` | Hot-reload a single app (token-protected) |
| HTTP in | `/apps` | List running apps and their ports |
| HTTP in | `/schedules` | CRUD for cron schedules |

## Key env vars

| Var | Default | Notes |
|---|---|---|
| `APPS_ROOT` | `../apps` | Root directory scanned for app subdirectories |
| `DATABASE_URL` | `postgresql+asyncpg://...` | PostgreSQL for schedules and permissions |
| `BEG_OS_REDIS_URL` | `redis://localhost:6379` | |
| `CONTROLLER_API_TOKEN` | — | Required for `/apps/reload` |
| `SECCOMP_PROFILE` | `/config/seccomp-untrusted.json` | Applied to EphemeralRunner containers |
