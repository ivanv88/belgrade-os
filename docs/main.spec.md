# Belgrade AI OS: Vision & Roadmap

**Version:** 2.0.0
**Host:** Raspberry Pi 4 (8GB RAM)
**Storage:** 4TB WD Red HDD
**Security:** Cloudflare Zero Trust (Email OTP)

---

## 1. System Vision

A "Modular Monolith" designed for personal orchestration. The **Core** acts as a platform engine (the Bootstrapper), while **Apps** act as pluggable, manifest-driven modules. Apps can be Workers, Observers, Bridges, or Orchestrators. The Admin Agent (Gemini 1.5 Pro) can discover, invoke, and deploy apps on demand via MCP. Obsidian is the universal client for apps without a custom UI.

For full technical detail see `docs/tech.spec.md`.

---

## 2. Technical Stack

| Layer | Technology | Purpose |
|---|---|---|
| **Hardware** | Raspberry Pi 4 (8GB) | 24/7 Production Host |
| **Backend** | FastAPI + SQLModel | Bootstrapper + Plugin API |
| **Database** | PostgreSQL 15 (Docker) | Per-app schemas |
| **Sync** | CouchDB + LiveSync | Obsidian real-time sync |
| **AI Agent** | Gemini 1.5 Pro (Docker) | Orchestration + self-healing |
| **Security** | Cloudflare Tunnel | Identity-based access (beg-os.fyi) |
| **Cloud** | Supabase | Metadata, AI logs, task queues only |

---

## 3. Implementation Roadmap

### ✅ Phase 1: Infrastructure (Complete)
- [x] Cloudflare Tunnel & Zero Trust Auth (Email OTP)
- [x] GitHub Monorepo sync between MBP M2 and Pi
- [x] PostgreSQL Docker container
- [x] Cloudflared tunnel service in docker-compose
- [x] Architecture spec and CLAUDE.md

### 🕒 Phase 2: Platform Core (Current)
- [ ] `core/models/manifest.py` — Pydantic v2 `AppManifest` schema
- [ ] `core/loader.py` — Bootstrapper (manifest discovery, provisioning, trigger registration)
- [ ] `core/context.py` — `AppContext` typed class (`io`, `db`, `notify`, `emit`, `meta`)
- [ ] `core/io.py` — IO adapter classes (local, obsidian, gdrive)
- [ ] `core/eventbus.py` — internal asyncio.Queue pub/sub
- [ ] `core/executor.py` — `safe_execute` wrapper (error handling, circuit breaker, semaphore)
- [ ] `shared/ntfy.py` — ntfy.sh notification helper
- [ ] mypy setup

### ⬜ Phase 3: First App (Nutrition Worker)
- [ ] `apps/nutrition/manifest.json` using the new contract
- [ ] Nutrition `models.py` scoped to `app_nutrition` schema
- [ ] `execute(ctx)` — nightly calorie deficit calculation at 20:00
- [ ] ntfy.sh alert when goal met/missed

### ⬜ Phase 4: Admin Agent
- [ ] Tecnativa docker-socket-proxy service in docker-compose (filters Docker API)
- [ ] Admin Agent container with scoped volume mounts (`/apps` RW, `core/` RO, family data not mounted)
- [ ] All managed containers labelled `beg-os.managed=true`
- [ ] FastMCP `/mcp/sse` endpoint with Cloudflare Access auth
- [ ] MCP tool auto-generation from `mcp_enabled` manifests
- [ ] Agent subscribed to `system.*` — reads logs, proposes fixes via ntfy.sh, applies on approval
- [ ] System backup cron script at `/mnt/storage/backups/backup.sh` (weekly, outside platform)

### ⬜ Phase 5: Obsidian Integration
- [ ] CouchDB service in docker-compose
- [ ] Observer pattern app reacting to vault file saves
- [ ] obsidian adapter (`write` → frontmatter + MD, `read` → parsed dict)

### ⬜ Phase 6: Orchestrators
- [ ] Gmail API connector in `/shared`
- [ ] Google Drive connector + gdrive adapter (write-through cache)
- [ ] First Orchestrator app (email attachments or Drive sync)

---

## 4. Security & Data Policy

- **Access Control:** Restricted to specific emails via Cloudflare Zero Trust
- **Persistence:** Live data in `/data/postgres` and `/mnt/storage` — git-ignored, never committed
- **Secrets:** Managed via `.env` file only
- **Family data:** Never leaves the Pi — Supabase is for metadata and AI logs only
