# Belgrade OS

A distributed personal operating system for home orchestration, running on a 2020 Lenovo IdeaPad (i5 10th gen, 12GB RAM) with Pop!_OS headless. Services communicate exclusively via Redis — no direct inter-service HTTP calls.

---

## Architecture

| Service | Language | Role |
|---|---|---|
| `services/gateway/` | Go | HTTP entry point — JWT auth, task ingestion, SSE proxy, static UI serving, RBAC |
| `services/bridge/` | Rust | Capability registry — tool registration, write-through Redis cache, event broker |
| `services/inference/` | Python | Inference Controller — Claude/Gemini/Ollama tool loop |
| `services/runner/` | Python | Resource Runner — executes trusted tool calls from apps |
| `services/platform_controller/` | Python | App lifecycle — process supervision, crash-restart, manifest validation, RBAC sync |
| `services/vault_service/` | Python | Vault Service — atomic writes to Obsidian via Redis streams |
| `services/notification/` | Python | Notification Service — ntfy driver, dead-letter queue |
| `services/mcp_server/` | Python | MCP Server — exposes tools to external agents (Claude Desktop) via JSON-RPC |
| `services/watchdog/` | Python | Watchdog — monitors service health, restarts crashed processes |
| `sdk/` | Python | Belgrade SDK — `BelgradeApp` base class for building apps |

Apps live in `apps/` and are supervised by Platform Controller. Each app needs `main.py` (using the SDK) and `manifest.json`.

---

## Setup

**One-time (macOS):**
```bash
make deps
cp .env.example .env   # fill in required keys (see below)
make build             # compiles gateway (Go) and bridge (Rust)
make proto             # regenerates protobuf code for all services
```

**Required `.env` keys:**
```
ANTHROPIC_API_KEY=        # or GOOGLE_API_KEY / OLLAMA_BASE_URL
CF_TUNNEL_TOKEN=          # Cloudflare tunnel token
DB_PASSWORD=              # Postgres password
```

---

## Running

```bash
# 1. Start infrastructure (Redis, Postgres, Cloudflare tunnel, Docker socket proxy)
make dev

# 2. Seed permissions (first run only)
python3 scripts/seed_permissions.py

# 3. Start all services
./scripts/start.sh

# Stop all services
./scripts/stop.sh
```

Services start in dependency order. Platform Controller auto-discovers and starts apps from `apps/`.

---

## Development

```bash
# Run all tests
make test

# Per-service
cd services/gateway      && go test ./... -v
cd services/runner       && python3 -m pytest tests/ -v
cd services/inference    && python3 -m pytest tests/ -v
cd services/notification && python3 -m pytest tests/ -v
cd services/bridge       && cargo test

# Wipe generated artifacts
make clean
```

---

## Conventions

- **Proto first:** All inter-service message shapes are defined in `proto/belgrade_os.proto` and regenerated with `make proto`.
- **Identity everywhere:** `user_id` and `tenant_id` are propagated through every message.
- **No direct disk writes from apps:** use `ctx.vault.write()` — all writes go through the Vault Service.
- **Redis only for transport:** services never call each other over HTTP; all communication is via Redis streams or Pub/Sub.
