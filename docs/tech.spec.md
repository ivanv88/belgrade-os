# Belgrade AI OS: Technical Specification v2.3

> **⚠️ DEPRECATED** — This document describes the original FastAPI monolith architecture that was superseded in April 2026 by a distributed architecture (Go gateway, Rust bridge, Python inference/runner/platform-controller, Redis transport). The current architecture is documented in `CLAUDE.md` and `docs/main.spec.md`. This file is kept for historical reference.

---

## 1. System Overview

A modular monolith running on a Raspberry Pi 4 (8GB) + 4TB WD Red HDD. The **Core** is a platform engine (the Bootstrapper) that discovers, mounts, and runs **Apps**. The **Admin Agent** (Gemini 1.5 Pro in Docker) orchestrates the platform — it can discover apps via MCP, deploy new services on demand, and self-heal from logs.

**Guiding principles:** Boring technology. Modular monolith (no microservices without business justification). Local-first — family data stays on the Pi; cloud is for reasoning and metadata only. **Platform core is generic** — no app-specific types or knowledge in `core/`.

---

## 2. Infrastructure

| Layer | Technology | Notes |
|---|---|---|
| Hardware | Raspberry Pi 4 (8GB) | 24/7 host |
| Storage | 4TB WD Red HDD | Mounted at `/mnt/storage` |
| Runtime | Docker + Docker Compose | All services except FastAPI |
| FastAPI | Bare-metal (venv) | Entry point: `uvicorn core.main:app` |
| Database | PostgreSQL 15 (Docker) | Per-app schemas + shared schema |
| Obsidian Sync | CouchDB (Docker) + LiveSync | Real-time, offline-first |
| Tunnel | Cloudflare Zero Trust | Access at `beg-os.fyi`, email OTP |
| Cloud | Supabase | Metadata, AI logs, task queues only |
| Monitoring | Dozzle | JSON-formatted log viewer |

Family data path: `/mnt/storage/shares/family/obsidian`

---

## 3. App Anatomy

Every app lives at `/apps/<app-id>/` and must contain:

```
/apps/<app-id>/
├── manifest.json   # platform contract: triggers, storage, mcp
├── main.py         # async def execute(ctx: AppContext) -> None
└── (optional)
    ├── models.py   # SQLModel tables scoped to app_{id} schema
    ├── events.py   # EVENT_SCHEMAS dict — topics this app emits
    └── metrics.py  # MetricsSchema — app's typed view of shared.config
```

`async def execute` is the enforced contract — no sync entry points.

### 3.1 App Patterns

| Pattern | Trigger | Data Flow | Use Case |
|---|---|---|---|
| Worker | `cron` | Pi ↔ Pi | Nightly jobs, DB processing |
| Observer | `observer` (file change) | Vault → Logic → Vault | High-volume content processing (notes, wiki) — not for single-value metrics input |
| Bridge | `hook` (HTTP) | Client → Pi → Response | Direct input endpoints, micro-UIs (Streamlit/Vite) |
| Orchestrator | `hook` + multi-step | External → AI → Logic → Pi | Gmail, Drive, complex flows |

### 3.2 Manifest Schema

Defined as a Pydantic v2 model in `core/models/manifest.py`.

```json
{
  "app_id": "nutrition",
  "name": "Nutrition Tracker",
  "description": "Tracks daily calorie deficit against goal",
  "version": "1.0.0",
  "pattern": "worker",
  "mcp_enabled": false,
  "triggers": [
    { "type": "cron", "schedule": "0 20 * * *" },
    { "type": "event", "topic": "system.app_failed" }
  ],
  "storage": {
    "scope": "nutrition",
    "adapter": "local"
  },
  "config": {
    "shared_write": false
  }
}
```

**Trigger types:**

| Type | Maps to | Required fields |
|---|---|---|
| `cron` | APScheduler | `schedule` (cron expression) |
| `observer` | watchdog `Observer` | `path` |
| `hook` | FastAPI `add_api_route` | — |
| `event` | EventBus subscription | `topic` (namespaced: `{origin}.{event_type}`) |

**Adapter options:** `local` (default), `obsidian` (markdown transformer), `gdrive` (write-through cache).

**`mcp_enabled: true`** — Bootstrapper auto-generates an MCP tool (`run_{app_id}`) from the manifest.

**`config.shared_write: true`** — grants write access to `shared.config`. Should be declared only by apps explicitly designed as metrics input endpoints or the Admin Agent.

---

## 4. The Bootstrapper (`core/loader.py`)

On startup, for each `/apps/*/manifest.json` found:

1. **Parse** — validate manifest against `AppManifest` Pydantic schema
2. **Provision** — `CREATE SCHEMA IF NOT EXISTS app_{id}`, `mkdir ./data/apps/{id}/`
3. **Import models** — if `models.py` exists, register SQLModel tables → `create_all`
4. **Register event schemas** — if `events.py` exists, import `EVENT_SCHEMAS` → `EventBus.register_schema(topic, model)`
5. **Register metrics schema** — if `metrics.py` exists, import `MetricsSchema` → hydration layer stores it keyed by `app_id`
6. **Build Subscription Map** — `{ topic: [app_id, ...] }` from all `event` triggers
7. **Register triggers** — `cron` → APScheduler, `observer` → watchdog (thread→asyncio bridge), `hook` → `add_api_route`, `event` → Subscription Map
8. **Register MCP tools** — for `mcp_enabled` apps, generate and register tool with FastMCP
9. **On trigger fire** → `safe_execute(app_id, ctx)`

Apps are **lazy-loaded** — `importlib` imports `apps.{app_id}.main` only when a trigger fires.

`ctx` is a **factory** — constructed fresh per invocation.

---

## 5. Execution: `safe_execute`

Every invocation goes through the sandbox wrapper in `core/executor.py`:

```python
async def safe_execute(app_id, ctx, **kwargs):
    async with semaphore:          # max 3 concurrent executions
        try:
            module = await lazy_load(app_id)
            await execute_with_retry(module, ctx, **kwargs)
        except Exception as e:
            await handle_error(app_id, e, ctx)
        finally:
            await ctx.cleanup()    # rollback DB, close handles
```

**Error categories:**

| Type | Example | Platform Action |
|---|---|---|
| Logic Error | `KeyError`, `ValidationError` | Catch → log to app scope → ntfy.sh |
| Transient Error | `TimeoutError`, API failure | Catch → retry with exponential backoff |
| Resource Error | `DiskFull`, DB connection lost | System halt of non-essential workers → ntfy.sh critical |

**Error classification:** platform maps known exception types; apps can raise `TransientError` / `ResourceError` to override.

**Circuit breaker:** 3 failures within 10 minutes → app disabled in-memory → `system.circuit_broken` emitted → ntfy.sh alert. Requires manual restart or Admin Agent fix to re-enable.

**Concurrency:** `asyncio.Semaphore(3)` — preserves Pi 4 RAM.

---

## 6. The Context API (`ctx: AppContext`)

Typed interface in `core/context.py`. Constructed fresh per invocation by the Bootstrapper.

| Property | Type | Access | Description |
|---|---|---|---|
| `ctx.user` | `User` | read-only | Stable identity: name, email, timezone. Loaded from `identity.json`. |
| `ctx.metrics` | `MetricsSchema` | read-only | App's typed view of `shared.config` JSONB. Validated at hydration. |
| `ctx.db` | `AsyncSession` | read/write | Scoped to `app_{id}` schema. Rolled back and closed in `finally`. |
| `ctx.io` | `IOAdapter` | read/write | Adapter-wrapped file ops. Base path pre-resolved. |
| `ctx.emit` | `Callable` | write | Publishes to EventBus. Topics: `{origin}.{event_type}`. |
| `ctx.notify` | `NotifyService` | write | ntfy.sh push to device (external). |
| `ctx.meta` | `AppMeta` | read-only | `app_id`, timestamp, resolved secrets. |

---

## 7. User State Model

Three tiers, strictly separated:

| Tier | Storage | Access | Purpose |
|---|---|---|---|
| **Identity** | `identity.json` | `ctx.user` | Immutable: name, height, DOB, timezone |
| **Metrics** | `shared.config` JSONB | `ctx.metrics` | Mutable: weight, goals, preferences |
| **App State** | `app_{id}.*` tables | `ctx.db` | App-specific logs, caches, private data |

### Shared Postgres Schema

```sql
CREATE SCHEMA IF NOT EXISTS shared;

CREATE TABLE shared.config (
    namespace   VARCHAR NOT NULL,
    data        JSONB NOT NULL,
    updated_at  TIMESTAMP DEFAULT NOW(),
    PRIMARY KEY (namespace, updated_at)   -- composite key enables history
);

CREATE INDEX idx_shared_config_data ON shared.config USING GIN (data);

CREATE VIEW shared.current_metrics AS
SELECT DISTINCT ON (namespace) namespace, data, updated_at
FROM shared.config
ORDER BY namespace, updated_at DESC;
```

Bootstrapper hydrates `ctx.metrics` from `shared.current_metrics`. Platform reads a generic JSONB blob and validates it against the app's own `MetricsSchema` — platform never knows the field names.

### Metrics Write Path

**Postgres is the source of truth. Markdown is the view.**

Metrics are written via a direct HTTP POST to a `hook`-triggered Bridge app — not via Obsidian file observation. The 5-hop CouchDB → watchdog → parse → write chain is too fragile for single-value updates and fails silently.

```
POST /hooks/metrics          ← iOS Shortcut, Obsidian plugin, or any HTTP client
  → Bridge app validates payload against Schema Registry
  → writes to shared.config (requires config.shared_write: true)
  → emits system.metrics_updated
  → (optional) Worker down-syncs to metrics.md in Obsidian vault
```

This gives immediate acknowledgement on failure. If the POST fails, the caller knows instantly.

**CouchDB + Observer is for content**, not metrics: processing new vault notes, wiki compilation, bulk document changes where passive triggering makes sense.

Only apps with `config.shared_write: true` in their manifest can write to `shared.config`.

---

## 8. EventBus

Internal async pub/sub via `asyncio.Queue`. Platform core is generic — no app-specific event types in `core/`.

### Schema Registry

Apps declare event schemas in their own `events.py`:

```python
# apps/nutrition/events.py
class NutritionGoalReached(BaseModel):
    calories: int
    deficit_met: bool

EVENT_SCHEMAS = {
    "nutrition.goal_reached": NutritionGoalReached
}
```

Bootstrapper imports `events.py` and calls `EventBus.register_schema(topic, model)`. Platform stores `Dict[str, Type[BaseModel]]` and calls `model.model_validate(data)` generically — no app types in core.

When app is deleted, its schemas vanish from the registry automatically. No stale types, no God File updates.

### Validation Tiers

| Topic | Enforcement |
|---|---|
| `system.*` | Always strict — platform and Admin Agent depend on these |
| App topic with registered schema | Always validated — schema exists, enforce it |
| App topic without schema | Permissive — dict passes through, warning logged |

### Emit Flow

1. `ctx.emit("nutrition.goal_reached", data)`
2. EventBus looks up schema → `model_validate(data)` at producer (fail-fast, prevents silent queue poisoning)
3. Validated Pydantic object (not raw dict) placed on `asyncio.Queue`
4. Bootstrapper routes to subscribers via Subscription Map → `safe_execute` for each

Platform events (`system.*`) are emitted directly by the Bootstrapper or platform infrastructure, not via `ctx.emit`. Defined in `core/events.py`:

| Event | Emitted by | Payload |
|---|---|---|
| `system.app_failed` | Executor | `app_id`, `error`, `timestamp` |
| `system.circuit_broken` | Executor | `app_id`, `failure_count` |
| `system.storage_low` | Health monitor | `path`, `available_bytes` |
| `system.metrics_updated` | Metrics Bridge app | `namespace`, `updated_at` |

---

## 9. Storage Adapter Model

Local FS (`./data/apps/{scope}/`) is always the **source of truth**. Adapters are lenses over it.

| Adapter | Behaviour |
|---|---|
| `local` | Raw file ops, no transformation |
| `obsidian` | `write(data)` → YAML frontmatter + Markdown body. `read()` → `{ frontmatter: dict, body: str }`. |
| `gdrive` | Non-blocking `write()`. Local completes immediately; Admin Agent up-syncs in background. |

Apps never see absolute paths — base path injected into `ctx.io` at boot.

---

## 10. Database Strategy

- Engine: PostgreSQL 15, db `belgrade_os`, user `laurent`
- **`shared` schema** — user metrics, cross-app config (write-restricted)
- **`app_{id}` schema** — per-app tables, provisioned by Bootstrapper on discovery
- App models in `models.py` declare `__table_args__ = {"schema": "app_{id}"}`
- No migrations yet — `SQLModel.metadata.create_all(engine)` on startup
- Shared engine in `shared/database.py`; per-invocation async sessions via `ctx.db`

---

## 11. MCP Integration

- **Library:** FastMCP
- **Endpoint:** `/mcp/sse` (Server-Sent Events)
- **Auth:** `X-Cloudflare-Access-Identity` header validation
- **Tool generation:** for each `mcp_enabled` app, Bootstrapper auto-generates:
  - Tool name: `run_{app_id}`
  - Description: from manifest `description`
  - Input schema: from manifest `config`
- No static tool definitions — everything registered dynamically at boot

---

## 12. Type Safety

- **Runtime:** Pydantic v2 for all data boundaries (manifests, events, metrics hydration, DB models via SQLModel)
- **Static:** mypy across the codebase
- **`AppContext`:** fully typed — editors provide autocomplete for all app developers
- **No `Any`** without explicit justification
- **Platform rule:** no app-specific types in `core/` — apps own their schemas, platform runs them generically

---

## 13. Admin Agent

- **Runtime:** Gemini 1.5 Pro, Dockerized, separate compose stack
- **Subscribed to:** `system.*` topics via EventBus (MCP over `/mcp/sse`)

### Volume Mounts

| Path | Access | Reason |
|---|---|---|
| `/mnt/storage/belgrade-os/apps/` | Read/Write | Agent's working area — patch, deploy, experiment |
| `/mnt/storage/belgrade-os/core/` | Read-Only | Can read loader/context to understand platform, cannot modify it |
| `/mnt/storage/shares/` | Not mounted | Invisible to agent |
| Immich data | Not mounted | Invisible to agent |

### Docker Access

Raw `docker.sock` is not exposed. Instead, **Tecnativa docker-socket-proxy** sits between the agent and Docker, filtering API calls to a safe subset. The agent can only manage containers labelled `beg-os.managed=true` — it cannot see or touch `immich_server`, `immich_postgres`, Samba, or any other service.

Proxy environment variables:
```
CONTAINERS=1   # can list/inspect/restart containers
POST=1         # can execute POST actions (restart, exec)
NETWORKS=0     # cannot create network backdoors
VOLUMES=0      # cannot delete or create HDD volumes
```

### Capability Phases

**Phase 1 (launch):** Read-only advisor with controlled restarts.
1. Reads logs and EventBus `system.*` events
2. Analyses failure, proposes fix as a diff
3. Sends proposed fix via ntfy.sh for human review
4. On approval (HTTP reply to a `/approve` hook), applies patch and restarts the affected container

**Phase 2 (after months of validated proposals):** Autonomous write access enabled per-scope. Flip is per-app, not global.

### Service Factory

Builds and deploys new app containers on demand. All generated code committed to local Git before deployment. Only containers with `beg-os.managed=true` label are created or managed.

---

## 14. Backup Strategy

Backup runs as a **system-level cron job** on the Pi — entirely outside Belgrade OS so it cannot be disrupted by platform failures.

```bash
# /mnt/storage/backups/backup.sh — runs weekly via crontab (0 3 * * 0)
TIMESTAMP=$(date +%Y%m%d)
BACKUP_DIR="/mnt/storage/backups/$TIMESTAMP"
mkdir -p "$BACKUP_DIR"

docker exec immich_postgres pg_dump -U postgres immich > "$BACKUP_DIR/immich.sql"
docker exec belgrade-db pg_dump -U laurent belgrade_os > "$BACKUP_DIR/belgrade_os.sql"
rsync -a /mnt/storage/shares/family/obsidian/ "$BACKUP_DIR/obsidian/"

find /mnt/storage/backups/ -maxdepth 1 -mtime +28 -exec rm -rf {} \;
```

Retains 4 weekly snapshots. Worst case data loss from a bad agent patch: one week of non-critical Belgrade OS data. Immich and family files are unaffected by design (not mounted).

---

## 15. Shared Libraries (`/shared`)

Apps access these via `ctx` — never imported directly:

- `database.py` — SQLModel engine, `init_db()`, async session factory
- `ntfy.py` — push notification helper
- Google Drive connector (used by `gdrive` adapter)
- Gmail API connector
- Obsidian REST API connector
