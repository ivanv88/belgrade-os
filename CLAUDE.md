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
```

Dependencies are installed via `setup.sh` (no requirements.txt). Core packages: `fastapi`, `uvicorn`, `sqlmodel`, `pydantic-settings`, `psycopg2-binary`, `apscheduler`, `watchdog`, `python-dotenv`.

## Architecture

Belgrade AI OS is a **modular monolith** running on a Raspberry Pi 4 (8GB) + 4TB WD Red HDD (`/mnt/storage`). The Core is a platform engine (Bootstrapper) that discovers, mounts, and runs Apps. The Admin Agent (Gemini 1.5 Pro in Docker) orchestrates the platform and can deploy new apps on demand.

### App anatomy

Every app lives at `/apps/<app-id>/` and must contain:

```
/apps/<app-id>/
├── manifest.json   # declares pattern, triggers, storage scope
└── main.py         # entry point: async def execute(ctx): ...
```

### App patterns

| Pattern | Trigger | Data Flow | Use case |
|---|---|---|---|
| Worker | `cron` | Pi ↔ Pi | Nightly jobs, DB processing |
| Observer | `observer` (file change) | Obsidian → Logic → Obsidian | React to vault file saves |
| Bridge | `hook` (HTTP) | AI → UI → User | Micro-UIs (Streamlit/Vite) |
| Orchestrator | `hook` + multi-step | External → AI → Logic → Pi | Gmail, Drive, complex flows |

### Manifest schema

```json
{
  "app_id": "nutrition",
  "description": "Tracks daily calorie deficit",
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

`adapter` options: `local` (default), `obsidian` (markdown transformer), `gdrive` (write-through cache, async up-sync).

### The Bootstrapper (`core/loader.py`)

On startup, for each manifest found in `/apps/*/manifest.json`:

1. Parse + validate manifest (Pydantic)
2. Provision resources — `CREATE SCHEMA IF NOT EXISTS app_{id}`, `mkdir ./data/apps/{id}/`
3. Register triggers:
   - `cron` → `apscheduler.schedulers.asyncio.AsyncIOScheduler`
   - `observer` → `watchdog.observers.Observer` (runs in a thread — bridge to asyncio via `asyncio.run_coroutine_threadsafe(execute(ctx), loop)`)
   - `hook` → `app.add_api_route()` on the FastAPI instance
4. On trigger fire → construct fresh `ctx` and call `execute(ctx)`

`ctx` is a **factory** (not a singleton) — a new instance is constructed per invocation so `ctx.db` sessions are never shared across calls.

### The Context API (`ctx`)

| Property | What it provides |
|---|---|
| `ctx.io` | Adapter-wrapped file ops. App calls `ctx.io.write("log.md")` — platform resolves absolute path. Adapter (`local`, `obsidian`, `gdrive`) injected transparently. |
| `ctx.db` | Scoped to the app's own Postgres schema (`app_{id}`). Fresh session per invocation. |
| `ctx.notify` | Push notifications via ntfy.sh |
| `ctx.meta` | `app_id`, current timestamp, resolved secrets |

### Storage adapter model

Local FS (`./data/apps/{scope}/`) is always the source of truth. Adapters are lenses over it:

- **`local`** — raw file ops, no transformation
- **`obsidian`** — `write(data)` serialises to YAML frontmatter + Markdown body; `read()` parses back to `{ frontmatter: dict, body: str }`
- **`gdrive`** — `write()` is non-blocking; local write completes immediately, admin agent handles up-sync in background (write-through cache)

Apps never see absolute paths — the base path is injected into `ctx.io` at boot.

### Shared infrastructure

- `shared/database.py` — SQLModel engine (`localhost:5432`, db `belgrade_os`, user `laurent`). Per-app schemas provisioned by the Bootstrapper.
- `core/config.py` — `pydantic_settings` from `.env`. Required: `DB_PASSWORD`. Optional: `NTFY_TOPIC`, `GEMINI_API_KEY`, `CLOUDFLARE_TOKEN`.
- `shared/` — domain connectors (Google Drive, Gmail, Obsidian REST). Used internally by adapters, not called directly by apps.

## Deployment

- **Production host:** Raspberry Pi 4 (8GB), FastAPI runs bare-metal; everything else in Docker.
- **Storage:** WD Red HDD at `/mnt/storage`. Family data at `/mnt/storage/shares/family/obsidian`.
- **Access:** Cloudflare Tunnel (`beg-os.fyi`) + Zero Trust email OTP. `tunnel` service in `docker-compose.yml` runs `cloudflared`.
- **Obsidian sync:** Self-hosted CouchDB (Docker) + Obsidian LiveSync plugin. Primary UI for all apps that don't need a custom interface.
- **Admin Agent:** Gemini 1.5 Pro in Docker with `docker.sock` access — discovers apps via MCP, can hot-deploy new services, parses logs for self-healing.
- **Monitoring:** Dozzle (JSON logs), internal health API (CPU/temp, WD Red capacity).
- **Cloud:** Supabase for structured metadata, AI logs, and high-availability task queues only. Family data never leaves the Pi.
- `/data/postgres` holds live Postgres data — git-ignored, never commit.
