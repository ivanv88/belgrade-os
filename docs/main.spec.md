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
| **Platform Controller** | Python | App lifecycle — process supervision, APScheduler cron |
| **SDK** | Python (`belgrade_sdk`) | App base class — tool registration, `/execute` callback |
| **Transport** | Redis (Streams + Pub/Sub) | All inter-service communication |
| **Database** | PostgreSQL 15 (Docker) | Platform controller state, per-app schemas |
| **Security** | Cloudflare Tunnel + Zero Trust | Identity-based access (beg-os.fyi, email OTP) |
| **Sync** | CouchDB + LiveSync | Obsidian real-time sync (planned) |

---

## 3. Implementation Roadmap — Original Monolith Design

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

## 4. Security & Data Policy

- **Access Control:** Restricted to specific emails via Cloudflare Zero Trust
- **Persistence:** Live data in `/data/postgres` and `/mnt/storage` — git-ignored, never committed
- **Secrets:** Managed via `.env` file only
- **Family data:** Never leaves the Pi — Supabase is for metadata and AI logs only

---

## 5. Roadmap (Current Status)

### ✅ Phase 1: Distributed Foundation (Completed Today)
- [x] **5-Service Architecture**: Gateway (Go), Bridge (Rust), Inference (Py), Runner (Py), Controller (Py).
- [x] **OS Kernel**: Platform Controller with sub-process management and hot-reload.
- [x] **Belgrade SDK**: @tool and @on_event decorators with automatic registration.
- [x] **Dynamic Scheduler**: Postgres-backed cron jobs managed by the Controller.
- [x] **Event Bus**: Authoritative pub/sub broker implemented in the Bridge.
- [x] **Log Visibility**: Automatic redirection of app logs to apps/{id}/app.log.
- [x] **Identity Loop**: Propagation of user_id/tenant_id for scoped DB/Notify.

### 🚀 Phase 2: The Nervous System (Next)
- [ ] **Notification Service**: Centralized service for ntfy.sh, email, and system alerts.
- [ ] **Multi-UI Service**: Serve static assets for app dashboards and Obsidian plugins.
- [ ] **Platform Connectors**: OAuth-managed connectors for Google Drive, Calendar, and Gmail.
- [x] **Persistent Bridge**: Bridge registry migrated to write-through Redis cache (2026-04-30).

### 🔮 Phase 3: AI Intelligence
- [ ] **Admin Agent Evolution**: Enable the agent to auto-install apps from Git URLs.
- [ ] **Self-Healing**: Agent monitoring of app.log to fix bugs automatically.
- [ ] **Workflow Orchestrator**: LLM-driven complex multi-app sequences.
