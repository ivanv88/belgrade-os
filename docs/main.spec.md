# Belgrade AI OS: Vision & Roadmap

**Version:** 2.0.0
**Host:** Lenovo i5 10th gen IdeaPad (12GB RAM, Pop!_OS headless)
**Storage:** `/mnt/storage` (HDD)
**Security:** Cloudflare Zero Trust (Email OTP)

---

## 1. System Vision

A "Modular Monolith" designed for personal orchestration. The **Core** acts as a platform engine (the Bootstrapper), while **Apps** act as pluggable, manifest-driven modules. Apps can be Workers, Observers, Bridges, or Orchestrators. The Admin Agent (Gemini 1.5 Pro) can discover, invoke, and deploy apps on demand via MCP. Obsidian is the universal client for apps without a custom UI.

For full technical detail see `docs/tech.spec.md`.

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

The Gateway (`gateway/`) authenticates requests, enforces RBAC, serves static UI assets, and
routes direct app actions. It is **not** the product inference API. Inference is an internal
platform capability, not a public endpoint.

### Inference is internal — apps own their workflows

The Inference Controller (`inference/`) is a background worker that reads from `tasks:inbound`.
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

## 4. Implementation Roadmap — Original Monolith Design

> **⚠️ SUPERSEDED** — This roadmap described the original FastAPI monolith architecture (`core/loader.py`, `AppContext`, `asyncio.Queue` EventBus). The system was redesigned as a distributed architecture in April 2026. See Section 5 for the current roadmap.

<details>
<summary>Archived phases (click to expand)</summary>

### ✅ Phase 1: Infrastructure (Complete)
- [x] Cloudflare Tunnel & Zero Trust Auth (Email OTP)
- [x] GitHub Monorepo sync between MBP M2 and Pi
- [x] PostgreSQL Docker container
- [x] Cloudflared tunnel service in docker-compose
- [x] Architecture spec and CLAUDE.md

### Phase 2: Platform Core
- `core/models/manifest.py`, `core/loader.py`, `core/context.py`, `core/io.py`, `core/eventbus.py`, `core/executor.py`, `shared/ntfy.py`

### Phase 3–6: Apps, Admin Agent, Obsidian, Orchestrators
- These phases were scoped for the monolith design and are not directly applicable to the distributed architecture.

</details>

---

## 5. Security & Data Policy

- **Access Control:** Restricted to specific emails via Cloudflare Zero Trust
- **Persistence:** Live data in `/data/postgres` and `/mnt/storage` — git-ignored, never committed
- **Secrets:** Managed via `.env` file only
- **Family data:** Never leaves the Pi — Supabase is for metadata and AI logs only

---

## 6. Roadmap (Current Status)

### ✅ Phase 1: Distributed Foundation (Completed Today)
- [x] **6-Service Architecture**: Gateway, Bridge, Inference, Runner, Controller, Notification.
- [x] **Notification Service**: Centralized Redis-based service with ntfy.sh and DLO support.
- [x] **Persistent Bridge**: Bridge registry migrated to write-through Redis cache.
- [x] **OS Kernel**: Platform Controller with sub-process management and manifest injection.
- [x] **Belgrade SDK**: Shared connection pooling for DB/Redis and native notification support.
- [x] **Event Bus**: Authoritative pub/sub broker implemented in the Bridge.
- [x] **Multi-UI Service**: Go-based secure static asset serving with auto-config injection.
- [x] **Vault Service**: Conflict-free, indirect vault writing via Redis streams and locks.
- [x] **RBAC Foundation**: Centralized permission management with high-performance Redis caching.

### 🚀 Phase 2: The Nervous System (Next)
- [ ] **Platform Connectors**: OAuth-managed connectors for Google Drive, Calendar, and Gmail.
- [ ] **Stateful Workflows**: Built-in support for long-running multi-app sequences.
- [ ] **Dashboard Shell**: A unified entry point listing all authorized user apps.

### 🔮 Phase 3: AI Intelligence
- [ ] **Admin Agent Evolution**: Enable the agent to auto-install apps from Git URLs.
- [ ] **Self-Healing**: Agent monitoring of app.log to fix bugs automatically.
- [ ] **Workflow Orchestrator**: LLM-driven complex multi-app sequences.
**: LLM-driven complex multi-app sequences.
s from Git URLs.
- [ ] **Self-Healing**: Agent monitoring of app.log to fix bugs automatically.
- [ ] **Workflow Orchestrator**: LLM-driven complex multi-app sequences.
