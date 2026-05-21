# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Install toolchain (macOS, one-time)
make deps

# Regenerate protobuf code for all services
make proto

# Build Go + Rust binaries (requires proto first)
make build

# Run all tests across all services
make test

# Start Redis (required for integration tests and local dev)
make dev                         # docker-compose up -d redis tunnel (does NOT start db)

# Per-service tests
cd services/gateway      && go test ./... -v
cd services/runner       && python3 -m pytest tests/ -v
cd services/inference    && python3 -m pytest tests/ -v
cd services/notification && python3 -m pytest tests/ -v
cd services/bridge       && cargo test
cd services/watchdog     && python3 -m pytest tests/ -v
cd services/mcp_server   && python3 -m pytest tests/ -v

# Wipe generated artifacts
make clean
```

## Architecture

Belgrade OS is a **distributed monorepo** running on Lenovo i5 10th gen IdeaPad (12GB RAM, Pop!_OS headless). Core services communicate exclusively through Redis — no direct inter-service calls.

```
browser / Obsidian
      │  HTTP + SSE
      ▼
┌─────────────┐   XADD tasks:inbound    ┌──────────────────────┐
│Edge Gateway │ ──────────────────────► │Inference Controller  │
│   (Go)      │                         │     (Python)         │
│             │ ◄────────────────────── │                      │
└─────────────┘  PUB sse:{task_id}      └──────────┬───────────┘
                                                    │ XADD tasks:tool_calls
                                                    ▼
                                         ┌──────────────────────┐
                                         │  Resource Runner     │
                                         │    (Python)          │
                                         └──────────┬───────────┘
                                                    │ XADD tasks:tool_results
                                                    ▼
                                         ┌──────────────────────┐
                                         │ Capability Bridge    │
                                         │     (Rust)           │
                                         │  tool registry       │
                                         └──────────────────────┘
```

### Services

| Dir | Language | Role |
|---|---|---|
| `services/gateway/` | Go | HTTP entry point — JWT auth, task ingestion, SSE proxy, static UI serving |
| `services/inference/` | Python | Inference Controller — calls Claude API, drives tool loop |
| `services/runner/` | Python | Resource Runner — executes tool calls from apps |
| `services/bridge/` | Rust | Capability Bridge — tool registry, write-through Redis cache |
| `services/notification/` | Python | Notification Service — Redis stream consumer, ntfy driver, DLO |
| `services/vault_service/` | Python | Vault Service — conflict-free Obsidian writes via Redis streams |
| `services/platform_controller/` | Python | App lifecycle manager — supervises app processes, manifest injection, APScheduler cron |
| `services/mcp_server/` | Python | MCP Server — exposes Belgrade OS tools to external MCP clients (Claude Desktop) via JSON-RPC over HTTP with Cloudflare JWT auth |
| `services/watchdog/` | Python | Watchdog — monitors service health, restarts crashed processes |

### Apps & SDK

| Dir | Role |
|---|---|
| `sdk/belgrade_sdk/` | Python SDK (`BelgradeApp`) — base class for building Belgrade apps; handles tool registration with bridge, `/execute` callback endpoint, and event routing |
| `apps/health/` | Health tracking app |
| `apps/nutrition/` | Nutrition tracking app |
| `apps/shopping/` | Shopping list app |

Apps register their tools at startup via `POST /v1/register` on the bridge, and expose a callback URL the bridge calls at `{callback_url}/execute` when a tool is invoked.

### Redis transport

| Channel | Type | Producer → Consumer |
|---|---|---|
| `tasks:inbound` | Stream (XREADGROUP) | Gateway → Inference Controller |
| `tasks:tool_calls` | Stream (XREADGROUP) | Inference Controller → Resource Runner |
| `tasks:tool_results` | Stream (XREADGROUP) | Resource Runner → Inference Controller |
| `tasks:notifications` | Stream (XREADGROUP) | SDK `ctx.notify()` → Notification Service |
| `tasks:vault_ops` | Stream (XREADGROUP) | SDK `ctx.io` / apps → Vault Service |
| `sse:{task_id}` | Pub/Sub | Inference Controller → Gateway → browser |
| `lease:{worker_id}` | Key (TTL) | Resource Runner worker leases |

### Proto contract

`proto/belgrade_os.proto` is the **single source of truth** for all message shapes. No gRPC — proto is used only for binary serialization over Redis.

Generated outputs (gitignored, rebuilt with `make proto`):
- `services/gateway/gen/belgrade_os.pb.go`
- `services/runner/gen/belgrade_os_pb2.py`
- `services/inference/gen/belgrade_os_pb2.py`
- `services/notification/gen/belgrade_os_pb2.py`
- `sdk/belgrade_sdk/gen/belgrade_os_pb2.py`
- `services/bridge/` — built by `cargo build` via `services/bridge/build.rs` (prost)

Key message types: `Task`, `ToolCall`, `ToolResult`, `ThoughtEvent`, `Tool`, `AppToolsRegistration`, `ToolListResponse`, `WorkerLease`, `NotificationRequest`. All carry `trace_id` for distributed tracing.

### Gateway (`services/gateway/`)

- `POST /v1/tasks` — validates `Cf-Access-Jwt-Assertion` (Cloudflare Zero Trust RS256 JWT), extracts `user_id` from `sub` claim, builds `Task` proto, XADDs to `tasks:inbound`
- When `stream: true` in request body: upgrades response to `text/event-stream`, subscribes to `sse:{task_id}` Pub/Sub, proxies `ThoughtEvent` payloads as SSE
- `GET /ui/{app_id}/{bundle_id}/...` — serves static assets from `apps/{app_id}/static/{bundle_id}/`; enforces path containment; injects per-app config into `index.html` responses
- JWKS fetched from `https://${CF_TEAM_DOMAIN}.cloudflareaccess.com/cdn-cgi/access/certs`, cached 24 h
- Auth: `services/gateway/auth/` sub-package; Redis: `services/gateway/redis/` sub-package

### MCP Server (`services/mcp_server/`)

- `POST /oauth/token` — validates `CF-Access-Jwt-Assertion` header (Cloudflare Access JWT), issues a short-lived Belgrade JWT for MCP sessions
- `POST /mcp` — JSON-RPC 2.0 endpoint; requires Bearer token (Belgrade JWT); supports `initialize`, `tools/list`, `tools/call` methods
- Auth flow: external MCP client (e.g., Claude Desktop) exchanges Cloudflare JWT for a Belgrade JWT, then uses the Belgrade JWT for all subsequent MCP calls
- Tool execution delegates to Bridge `POST /v1/execute`; tool discovery via Bridge `GET /v1/tools?mcp=true`
- **Port:** 8083 (via `MCP_PORT` env var)

### Environment variables

| Service | Key | Default | Notes |
|---|---|---|---|
| gateway | `PORT` | `8080` | |
| gateway | `REDIS_URL` | `redis://localhost:6379` | |
| gateway | `CF_TEAM_DOMAIN` | — | Required in production |
| gateway | `CF_AUDIENCE` | — | Required in production |
| mcp_server | `MCP_PORT` | `8083` | |
| mcp_server | `BRIDGE_URL` | `http://localhost:8081` | |
| mcp_server | `MCP_JWT_SECRET` | — | Required |
| mcp_server | `CF_TEAM_DOMAIN` | — | Required in production |
| mcp_server | `CF_MCP_AUDIENCE` | — | Required in production |
| mcp_server | `MCP_DEFAULT_USER_ID` | — | Fallback user identity |
| mcp_server | `MCP_DEFAULT_TENANT_ID` | — | Fallback tenant identity |

### Integration tests

Tests that touch Redis require it to be running:
```bash
docker-compose up -d redis
```

`platform_controller` also requires Postgres:
```bash
docker-compose up -d db
```

Redis-dependent tests skip gracefully (`t.Skipf`) when Redis is unreachable. Auth and config tests have no external deps.

## Deployment

- **Host:** Lenovo i5 10th gen IdeaPad (12GB RAM), Pop!_OS headless; services run as systemd units or Docker containers
- **Access:** Cloudflare Tunnel (`beg-os.fyi`) + Zero Trust email OTP
- **Storage:** `/mnt/storage`; family data at `/mnt/storage/shares/family/obsidian`
- **Backup:** weekly `pg_dump` + rsync at `/mnt/storage/backups/backup.sh`
- **Monitoring:** Dozzle (JSON logs)
- `/data/postgres`, `/data/redis` — git-ignored, never commit
