# Belgrade AI OS: Technical Specification v2.0

## 1. System Overview

A modular monolith running on a Raspberry Pi 4 (8GB) + 4TB WD Red HDD. The **Core** is a platform engine (the Bootstrapper) that discovers, mounts, and runs **Apps**. The **Admin Agent** (Gemini 1.5 Pro in Docker) orchestrates the platform — it can discover apps via MCP, deploy new services on demand, and self-heal from logs.

**Guiding principles:** Boring technology. Modular monolith (no microservices without business justification). Local-first — family data stays on the Pi; cloud is for reasoning and metadata only.

---

## 2. Infrastructure

| Layer | Technology | Notes |
|---|---|---|
| Hardware | Raspberry Pi 4 (8GB) | 24/7 host |
| Storage | 4TB WD Red HDD | Mounted at `/mnt/storage` |
| Runtime | Docker + Docker Compose | All services except FastAPI |
| FastAPI | Bare-metal (venv) | Entry point: `uvicorn core.main:app` |
| Database | PostgreSQL 15 (Docker) | Per-app schemas |
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
├── manifest.json   # declares pattern, triggers, storage scope
└── main.py         # entry point: async def execute(ctx): ...
```

### 3.1 App Patterns

| Pattern | Trigger | Data Flow | Use Case |
|---|---|---|---|
| Worker | `cron` | Pi ↔ Pi | Nightly jobs, DB processing |
| Observer | File change | Obsidian → Logic → Obsidian | React to vault file saves |
| Bridge | HTTP hook | AI → UI → User | Micro-UIs (Streamlit/Vite) |
| Orchestrator | HTTP hook + multi-step | External → AI → Logic → Pi | Gmail, Drive, complex flows |

### 3.2 Manifest Schema

```json
{
  "app_id": "nutrition",
  "description": "Tracks daily calorie deficit against goal",
  "pattern": "worker",
  "mcp_enabled": false,
  "triggers": [
    { "type": "cron", "schedule": "0 20 * * *" }
  ],
  "storage": {
    "scope": "nutrition",
    "adapter": "local"
  }
}
```

**Trigger types:** `cron`, `observer` (path + event), `hook` (HTTP endpoint).

**Adapter options:** `local` (default), `obsidian` (markdown transformer), `gdrive` (write-through cache).

**`mcp_enabled: true`** exposes the app as an MCP server (JSON-RPC over stdio) — makes it discoverable and invokable by the Admin Agent.

---

## 4. The Bootstrapper (`core/loader.py`)

On startup, for each `/apps/*/manifest.json` found:

1. **Parse** — validate manifest against Pydantic schema
2. **Provision** — `CREATE SCHEMA IF NOT EXISTS app_{id}`, `mkdir ./data/apps/{id}/`
3. **Register triggers:**
   - `cron` → `apscheduler.schedulers.asyncio.AsyncIOScheduler`
   - `observer` → `watchdog.observers.Observer` (runs in a thread; bridge to asyncio via `asyncio.run_coroutine_threadsafe(execute(ctx), loop)`)
   - `hook` → `app.add_api_route()` on the FastAPI instance
4. **On trigger fire** → construct a fresh `ctx` and call `execute(ctx)`

`ctx` is a **factory, not a singleton** — a new instance is constructed per invocation so `ctx.db` sessions are never shared across calls or concurrent requests.

---

## 5. The Context API (`ctx`)

The object injected into every `execute(ctx)` call:

| Property | Description |
|---|---|
| `ctx.io` | Adapter-wrapped file ops. App calls `ctx.io.write("log.md")` — platform resolves absolute path and applies adapter logic transparently. |
| `ctx.db` | Scoped to the app's own Postgres schema (`app_{id}`). Fresh session per invocation. |
| `ctx.notify` | Push notifications via ntfy.sh |
| `ctx.meta` | `app_id`, current timestamp, resolved secrets |

---

## 6. Storage Adapter Model

The local filesystem (`./data/apps/{scope}/`) is always the **source of truth**. Adapters are lenses layered over it — apps never see absolute paths, the base path is injected into `ctx.io` at boot.

| Adapter | Behaviour |
|---|---|
| `local` | Raw file ops, no transformation |
| `obsidian` | `write(data)` serialises to YAML frontmatter + Markdown body. `read()` parses back to `{ frontmatter: dict, body: str }`. |
| `gdrive` | `write()` is non-blocking. Local write completes immediately; Admin Agent handles up-sync in background (write-through cache). |

Apps declare a **scope** (namespace), not a physical path. The platform maps scope → provider based on the manifest adapter.

---

## 7. Database Strategy

- Engine: PostgreSQL (Docker), db `belgrade_os`, user `laurent`
- **One schema per app:** `app_{id}` (e.g. `app_nutrition`)
- Schema provisioned automatically by the Bootstrapper on app discovery
- Shared engine in `shared/database.py`; per-invocation sessions via `ctx.db`

---

## 8. Admin Agent

- **Runtime:** Gemini 1.5 Pro, Dockerized
- **Privileges:** `docker.sock` access (deploy/manage services), WD Red filesystem (Filesystem MCP), Obsidian vault (Obsidian Local REST API + MCP)
- **Capability discovery:** Apps with `mcp_enabled: true` are exposed as MCP servers; the Agent sees a capability map without loading all tools into context
- **Service factory:** Can build and deploy new app containers on demand; all code versioned via local Git + private GitHub
- **Self-healing:** Parses Dozzle JSON logs; recurring failures trigger an AI-suggested patch

---

## 9. Shared Libraries (`/shared`)

Domain connectors used internally by adapters — apps access these via `ctx.io`, not directly:

- Google Drive connector
- Gmail API connector
- Obsidian REST API connector
- `database.py` — SQLModel engine, `init_db()`, `get_session()`
- `ntfy.py` — push notification helper
