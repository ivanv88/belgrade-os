# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# First-time setup (installs system deps, creates venv)
bash setup.sh
source venv/bin/activate

# Start all Docker services (Postgres + CouchDB + Cloudflare tunnel)
docker-compose up -d

# Run the FastAPI server (from project root, venv active)
uvicorn core.main:app --reload

# Type checking
mypy .
```

Dependencies via `setup.sh`: `fastapi`, `uvicorn`, `sqlmodel`, `pydantic-settings`, `psycopg2-binary`, `apscheduler`, `watchdog`, `python-dotenv`, `mypy`.

## Architecture

Belgrade AI OS is a **modular monolith** on Raspberry Pi 4 (8GB) + 4TB WD Red HDD. The Core is a generic platform engine (Bootstrapper) — no app-specific types or knowledge in `core/`. Full spec in `docs/tech.spec.md`.

### App anatomy

```
/apps/<app-id>/
├── manifest.json   # required: triggers, storage, mcp contract
├── main.py         # required: async def execute(ctx: AppContext) -> None
├── models.py       # optional: SQLModel tables → app_{id} schema
├── events.py       # optional: EVENT_SCHEMAS dict for emitted topics
└── metrics.py      # optional: MetricsSchema — typed view of shared.config
```

### App patterns

| Pattern | Trigger | Use case |
|---|---|---|
| Worker | `cron` | Nightly jobs, DB processing |
| Observer | `observer` | React to Obsidian vault file saves |
| Bridge | `hook` (HTTP) | Micro-UIs (Streamlit/Vite) |
| Orchestrator | `hook` + multi-step | Gmail, Drive, complex flows |

### Trigger types

| Type | Maps to | Key field |
|---|---|---|
| `cron` | APScheduler | `schedule` |
| `observer` | watchdog (thread→asyncio bridge) | `path` |
| `hook` | FastAPI `add_api_route` | — |
| `event` | internal EventBus | `topic` (`{origin}.{event_type}`) |

### Bootstrapper (`core/loader.py`) — startup sequence

1. Parse + validate manifest (`core/models/manifest.py`)
2. Provision — `CREATE SCHEMA IF NOT EXISTS app_{id}`, `mkdir ./data/apps/{id}/`
3. Import `models.py` → `create_all` for this app's schema
4. Import `events.py` → register schemas with EventBus Schema Registry
5. Import `metrics.py` → register `MetricsSchema` with hydration layer
6. Build Subscription Map (`topic → [app_ids]`) from `event` triggers
7. Register all triggers; register MCP tools for `mcp_enabled` apps
8. On trigger fire → `safe_execute(app_id, ctx)`

Apps are **lazy-loaded** — `main.py` imported only when a trigger fires.

### Execution: `safe_execute` (`core/executor.py`)

- `asyncio.Semaphore(3)` — max concurrent executions
- Error tiers: Logic (catch+log), Transient (retry+backoff), Resource (system halt)
- Circuit breaker: 3 failures/10min → app disabled → `system.circuit_broken` emitted
- `finally` always rolls back `ctx.db` and closes handles

### Context API (`ctx: AppContext`)

| Property | Access | Description |
|---|---|---|
| `ctx.user` | read-only | Stable identity from `identity.json` (name, email, timezone) |
| `ctx.metrics` | read-only | App's typed view of `shared.config` JSONB, validated against app's own `MetricsSchema` |
| `ctx.db` | read/write | `AsyncSession` scoped to `app_{id}` schema |
| `ctx.io` | read/write | Adapter-wrapped file ops, base path pre-resolved |
| `ctx.emit(topic, data)` | write | EventBus publish. Validated at emit (fail-fast). Topics: `{origin}.{event_type}` |
| `ctx.notify` | write | ntfy.sh push to device |
| `ctx.meta` | read-only | `app_id`, timestamp, secrets |

### User state — three tiers

| Tier | Storage | Access |
|---|---|---|
| Identity | `identity.json` | `ctx.user` — immutable (name, height, DOB, timezone) |
| Metrics | `shared.config` JSONB + `shared.current_metrics` view | `ctx.metrics` — mutable (weight, goals, preferences) |
| App state | `app_{id}.*` tables | `ctx.db` — private per-app data |

Only apps with `"config": { "shared_write": true }` in their manifest can write to `shared.config`.

### EventBus — Schema Registry

Platform is generic. Apps own their schemas in `events.py`. Bootstrapper registers them; platform calls `model.model_validate(data)` without knowing field names.

Validation tiers: `system.*` always strict → registered schemas always validated → unregistered topics permissive (dict + warning).

Validated Pydantic objects (never raw dicts) are dispatched to subscribers. When an app is deleted, its schemas vanish automatically.

### Shared infrastructure

- `shared/database.py` — SQLModel engine (`localhost:5432`, db `belgrade_os`, user `laurent`)
- `core/config.py` — pydantic_settings from `.env`. Required: `DB_PASSWORD`. Optional: `NTFY_TOPIC`, `GEMINI_API_KEY`, `CLOUDFLARE_TOKEN`
- `shared/` — Google Drive, Gmail, Obsidian REST connectors. Used by adapters only, never imported directly by apps.

## Deployment

- **Host:** Raspberry Pi 4 (8GB), FastAPI bare-metal; everything else Docker
- **Storage:** `/mnt/storage`. Family data at `/mnt/storage/shares/family/obsidian`
- **Access:** Cloudflare Tunnel (`beg-os.fyi`) + Zero Trust email OTP
- **Obsidian sync:** CouchDB (Docker) + LiveSync. Obsidian is the edit interface for `shared.config` via Observer sync loop
- **Admin Agent:** Gemini 1.5 Pro in Docker. Volume mounts: `/apps` RW, `core/` RO, family data not mounted. Subscribes to `system.*` via MCP. Phase 1: read logs + propose fixes via ntfy.sh + apply on human approval. Phase 2 (later): autonomous writes per-scope.
- **Docker access:** via Tecnativa socket-proxy (not raw `docker.sock`). `CONTAINERS=1`, `POST=1`, `NETWORKS=0`, `VOLUMES=0`. Only manages containers labelled `beg-os.managed=true`.
- **MCP:** FastMCP at `/mcp/sse`, `X-Cloudflare-Access-Identity` auth, tools auto-generated from manifests
- **Backup:** system cron (`0 3 * * 0`) at `/mnt/storage/backups/backup.sh` — pg_dump both DBs + rsync Obsidian vault, 4-week retention. Runs outside the platform.
- **Monitoring:** Dozzle (JSON logs)
- **Cloud:** Supabase for metadata/AI logs only — family data never leaves the Pi
- `/data/postgres` — git-ignored, never commit
