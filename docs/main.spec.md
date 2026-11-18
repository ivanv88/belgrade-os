# Belgrade AI OS: Vision & Roadmap

**Version:** 2.1.0
**Host:** Lenovo i5 10th gen IdeaPad (12GB RAM, Pop!_OS headless)
**Storage:** `/mnt/storage` (HDD)
**Security:** Cloudflare Zero Trust (Email OTP)

---

## 1. System Vision

A distributed personal orchestration platform. Services communicate exclusively through Redis. **Apps** are manifest-driven Python processes supervised by the Platform Controller. The Admin Agent (Phase 4) will be able to discover, invoke, and deploy apps on demand via MCP. Obsidian is the universal client for apps without a custom UI.

---

## 2. Technical Stack

| Layer | Technology | Purpose |
|---|---|---|
| **Hardware** | Lenovo i5 10th gen (12GB RAM) | 24/7 Production Host (Pop!_OS headless) |
| **Gateway** | Go | HTTP entry point — JWT auth, task ingestion, SSE proxy |
| **Bridge** | Rust (Axum) | Capability registry — tool registration, write-through Redis cache |
| **Inference** | Python | Inference controller — Claude API, tool loop |
| **Runner** | Python | Resource runner — executes tool calls from apps |
| **Platform Controller** | Python | App lifecycle — process supervision, APScheduler cron, **RBAC sync** |
| **Vault Service** | Python | Gateway for Obsidian Vault — atomic writes via Redis |
| **Notification Service**| Python | Consumer for `tasks:notifications` — pluggable ntfy/firebase drivers |
| **SDK** | Python (`belgrade_sdk`) | App base class — tool registration, `/execute` callback, **Indirect Vault access** |
| **Transport** | Redis (Streams + Pub/Sub) | All inter-service communication |
| **Database** | PostgreSQL 15 (Docker) | Platform controller state, per-app schemas |
| **Security** | Cloudflare Tunnel + Zero Trust | Identity-based access (beg-os.fyi, email OTP) |
| **Sync** | CouchDB + LiveSync | Obsidian real-time sync (planned) |

---

## 3. Architecture Principles

These rules govern how the system is designed. Violations indicate planning drift.

### Gateway is an edge, not an inference API

The Gateway (`services/gateway/`) authenticates requests, enforces RBAC, serves static UI assets, and
routes direct app actions. It is **not** the product inference API. Inference is an internal
platform capability, not a public endpoint.

### Inference is internal — apps own their workflows

The Inference Controller (`services/inference/`) is a background worker that reads from `tasks:inbound`.
Apps decide when AI is needed. When an app needs inference it enqueues a `Task` proto directly
to Redis via `ctx.inference.request(prompt)` from the Belgrade SDK — no HTTP call to Gateway.

### App-owned inference is always UNTRUSTED

Apps may not self-assert trusted execution. `ExecutionMode` on app-owned tasks is always stamped
`UNTRUSTED` by the SDK. The Gateway is the only system component that stamps `TRUSTED`, derived
from the `TRUSTED_USER_IDS` env var checked against the validated JWT identity. An internal
platform trusted path for apps is a future concern.

**Defense-in-depth:** Inference overrides `execution_mode` to `UNTRUSTED` for every task where
`task.app_id != ""`. Redis ACLs cannot validate proto field values — an app with the `app` Redis
credential could manually serialize `execution_mode=TRUSTED`. The override in Inference is the
enforcement boundary, not the SDK default.

### App actions are deterministic by default

`POST /api/{app_id}/...` routes reach the app's own HTTP callback server directly (via Bridge
lookup). They do **not** enqueue inference tasks. An app may internally call
`ctx.inference.request()` if its domain logic requires AI, but this is the exception.

### `/v1/tasks` is legacy/internal

`POST /v1/tasks` on the Gateway remains functional for backward compatibility, developer tooling,
and future Admin App use. It is **not** the recommended integration path for apps or clients.
New code must use `ctx.inference.request()` from the SDK instead.

### No direct HTTP from Gateway to Inference Controller

Gateway never calls Inference over HTTP. All Gateway→Inference communication is indirect:
Gateway (or SDK) XADDs to `tasks:inbound`; Inference consumes from that stream.

### Bridge callback URLs are an internal trust boundary

App callback URLs are registered at startup by apps running under Platform Controller supervision.
The Bridge is not exposed through Cloudflare/Gateway — only internal services can register.
Registered callback URLs must use `http://` or `https://` schemes; other schemes are rejected
at registration time.

---

## 4. Security & Data Policy

- **Access Control:** Restricted to specific emails via Cloudflare Zero Trust
- **Persistence:** Postgres/Redis in Docker named volumes (`beg-os-pg-data`, `beg-os-redis-data`); family files under `/mnt/storage` — never committed
- **Secrets:** Managed via `.env` (`0600`) only; apps receive an env allowlist via a per-app `0600` file, never the platform `.env`
- **App isolation:** In production each app runs as a sandboxed systemd unit (`belgrade-os-app@<id>`) with its own dynamic user, read-only view of only its code + SDK + venv, localhost-only network, and its own non-superuser Postgres role (`app_<id>`)
- **Network:** Internal services listen on localhost; only `mcp.beg-os.fyi` is published via the tunnel; host firewall default-deny
- **Family data:** Never leaves the Lenovo — Supabase is for metadata and AI logs only

---

## 5. Roadmap (Current Status)

### ✅ Phase 1: Distributed Foundation
- [x] **6-Service Architecture**: Gateway (Go), Bridge (Rust), Inference (Python), Runner (Python), Platform Controller (Python), Notification (Python).
- [x] **Notification Service**: Redis stream consumer, ntfy driver, dead-letter queue.
- [x] **Persistent Bridge**: Tool registry with write-through Redis cache; URL-parse validation on registration.
- [x] **Platform Controller**: App subprocess supervision, crash-restart watchdog, manifest injection.
- [x] **Belgrade SDK** (`belgrade_sdk`): `BelgradeApp` base class — `@tool`, `@on_event`, `/execute` callback, `AppContext` with db/redis/vault/notify/emit/inference.
- [x] **Event Bus**: Bridge `/v1/events/publish` → app `/events` callback routing.
- [x] **Multi-UI Serving**: Gateway `GET /ui/{app_id}/{bundle_id}/...` with per-app config injection.
- [x] **Vault Service**: Conflict-free Obsidian writes via Redis streams and distributed lock.
- [x] **RBAC Foundation**: Permission model in Postgres, high-performance Redis cache, permission sync.
- [x] **Scheduled Tasks (Infrastructure)**: APScheduler cron via Platform Controller `/schedules` CRUD API, persisted to Postgres.
- [x] **App-Owned Scheduling**: `ctx.schedule(cron, tool_name, params)` and `ctx.unschedule(schedule_id)` in SDK; Platform Controller consumes `tasks:schedule_ops` stream with UPSERT/DELETE ops, validates cron before persisting.
- [x] **Multi-tenant DB Isolation**: Per-app Postgres schema (`app_{tenant_id}_{app_id}`) via `AppContext.db`.
- [x] **Trust Model**: Gateway stamps TRUSTED/UNTRUSTED from JWT identity. Inference enforces UNTRUSTED for all app-owned tasks (defense-in-depth). Tool calls routed to bare-metal runner (TRUSTED) or ephemeral Docker container (UNTRUSTED).
- [x] **Ephemeral Runner**: Sandboxed Docker execution — seccomp, read-only FS, `--network none`, 30s timeout.
- [x] **App Action Proxy**: Gateway `POST/GET/PUT/DELETE/PATCH /api/{app_id}/...` — RBAC-checked, header-sanitized reverse proxy via Bridge callback lookup.
- [x] **Inference Providers**: Claude (Anthropic), Gemini, Ollama (local LLMs via OpenAI-compatible API).
- [x] **App-Owned Inference Workflows**: `ctx.inference.request/stream/await_result/cancel` — apps drive stateful multi-step AI workflows; cancel key checked at each tool-loop iteration.
- [x] **Manifest Schema Validation**: `_load_manifest()` validates against `_AppManifest` (Pydantic); rejects apps with invalid JSON or schema violations at startup.
- [x] **MCP Server** (`services/mcp_server/`): JSON-RPC 2.0 over HTTP; Cloudflare Access JWT → Belgrade JWT OAuth flow; `initialize`, `tools/list`, `tools/call`; tool discovery via Bridge `/v1/tools?mcp=true`; execution via Bridge `/v1/execute`. Port 8083.
- [x] **Dashboard Shell**: Static iframe shell at `apps/dashboard/` — sidebar nav, app iframe loader, shows `user_id` from injected config. Hardcoded nav (shopping only); no dynamic app discovery.

### 🚀 Phase 2: First Apps + Platform Hardening (Current)

Focus: validate the platform with real apps, close known gaps before adding new features.

**Done:**
- [x] **First App (Shopping List)** — in daily use since 2026-10-23: household list backed by Postgres (`add_item`, `list_items`, `check_item`, `remove_item`, …), used from Claude Desktop over MCP.
- [x] **Claude Desktop access**: local stdio MCP proxy (`tools/mcp_proxy/`) → `mcp.beg-os.fyi` (Cloudflare Access service token) → tunnel → MCP Server.
- [x] **Hybrid deployment**: Redis, Postgres and cloudflared in Docker (named volumes, pinned images); platform services as `belgrade-os@<service>` systemd units under `belgrade-os.target`.
- [x] **Network hardening**: services bound to localhost, ufw default-deny, key-only SSH, Redis ACLs.
- [x] **App isolation (H1–H4)**: pluggable app runner (`subprocess` for dev, sandboxed systemd for prod), one dynamic user per app, per-app non-superuser Postgres roles, SDK refuses schemas owned by another role. Deployed and verified on the Lenovo 2026-11-18.

**Open:**
- [ ] **Shopping — rest of the stack**: shopping uses only `ctx.db` + MCP. Exercise the remaining SDK surface with a real use case — notifications, scheduled tasks, vault writes, inference workflows.
- [ ] **Filesystem Hot-Reload**: Platform Controller has explicit `POST /apps/reload` and a crash-restart watchdog, but no filesystem watcher. Add inotify-based watch on `apps/` so edits to `main.py` or `manifest.json` trigger a reload automatically.
- [ ] **Dashboard — Dynamic App Discovery**: Current dashboard has a hardcoded sidebar (`shopping` only). Replace with a call to Platform Controller `/apps` to list running apps and build nav dynamically, with status indicators.
- [ ] **Firebase Notification Driver**: `services/notification/drivers/` has the interface (`base.py`) and ntfy implementation. Firebase driver would unlock mobile push.
- [ ] **Security follow-ups** (see Obsidian *Beg-OS Security Notes*): M1 — apps may only schedule their own tools (`<app_id>:` prefix); M2 — per-service env files instead of one shared `.env`; M4 — per-person MCP tokens with expiry.
- [ ] **Tech debt**: migrate the Gemini provider from deprecated `google-generativeai` to `google-genai`; `demo_app` — fix or remove.

### 🔮 Phase 3: External Connectivity

- [ ] **Standalone App Mode** (Feature 1.1): Documented path for apps that run in their own containers but consume platform services (gateway auth, notifications, inference). Define the networking contract and env vars.
- [ ] **Platform Connectors**: OAuth-managed connectors for Google Drive, Calendar, Gmail — exposed as tools apps can call via the SDK.

### 🔮 Phase 4: AI Intelligence

- [ ] **Admin Agent**: Agent with access to platform internals — can install apps from Git URLs, inspect logs, restart services.
- [ ] **Self-Healing**: Agent monitoring of `app.log` output; detects errors and proposes or applies fixes.
- [ ] **Platform Self-Modification**: Agent can edit app code and UI within safety guardrails. Requires audit log and human-approval gate.
