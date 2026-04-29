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
make dev                         # docker-compose up -d redis tunnel

# Per-service tests
cd gateway  && go test ./... -v
cd runner   && python3 -m pytest tests/ -v
cd inference && python3 -m pytest tests/ -v
cd bridge   && cargo test

# Wipe generated artifacts
make clean
```

## Architecture

Belgrade OS is a **distributed monorepo** running on Raspberry Pi 4 (8GB). Five services communicate exclusively through Redis — no direct inter-service calls.

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
| `gateway/` | Go | HTTP entry point — JWT auth, task ingestion, SSE proxy |
| `inference/` | Python | Inference Controller — calls Claude API, drives tool loop |
| `runner/` | Python | Resource Runner — executes tool calls from apps |
| `bridge/` | Rust | Capability Bridge — tool registry, serves tool lists |

### Redis transport

| Channel | Type | Producer → Consumer |
|---|---|---|
| `tasks:inbound` | Stream (XREADGROUP) | Gateway → Inference Controller |
| `tasks:tool_calls` | Stream (XREADGROUP) | Inference Controller → Resource Runner |
| `tasks:tool_results` | Stream (XREADGROUP) | Resource Runner → Inference Controller |
| `sse:{task_id}` | Pub/Sub | Inference Controller → Gateway → browser |
| `lease:{worker_id}` | Key (TTL) | Resource Runner worker leases |

### Proto contract

`proto/belgrade_os.proto` is the **single source of truth** for all message shapes. No gRPC — proto is used only for binary serialization over Redis.

Generated outputs (gitignored, rebuilt with `make proto`):
- `gateway/gen/belgrade_os.pb.go`
- `runner/gen/belgrade_os_pb2.py`
- `inference/gen/belgrade_os_pb2.py`
- `bridge/` — built by `cargo build` via `bridge/build.rs` (prost)

Key message types: `Task`, `ToolCall`, `ToolResult`, `ThoughtEvent`, `Tool`, `AppToolsRegistration`, `ToolListResponse`, `WorkerLease`. All carry `trace_id` for distributed tracing.

### Gateway (`gateway/`)

- `POST /v1/tasks` — validates `Cf-Access-Jwt-Assertion` (Cloudflare Zero Trust RS256 JWT), extracts `user_id` from `sub` claim, builds `Task` proto, XADDs to `tasks:inbound`
- When `stream: true` in request body: upgrades response to `text/event-stream`, subscribes to `sse:{task_id}` Pub/Sub, proxies `ThoughtEvent` payloads as SSE
- JWKS fetched from `https://${CF_TEAM_DOMAIN}.cloudflareaccess.com/cdn-cgi/access/certs`, cached 24 h

### Environment variables

| Service | Key | Default | Notes |
|---|---|---|---|
| gateway | `PORT` | `8080` | |
| gateway | `REDIS_URL` | `redis://localhost:6379` | |
| gateway | `CF_TEAM_DOMAIN` | — | Required in production |
| gateway | `CF_AUDIENCE` | — | Required in production |

### Integration tests

Tests that touch Redis require it to be running:
```bash
docker-compose up -d redis
```

Redis-dependent tests skip gracefully (`t.Skipf`) when Redis is unreachable. Auth and config tests have no external deps.

## Deployment

- **Host:** Raspberry Pi 4 (8GB); services run as systemd units or Docker containers
- **Access:** Cloudflare Tunnel (`beg-os.fyi`) + Zero Trust email OTP
- **Storage:** `/mnt/storage`; family data at `/mnt/storage/shares/family/obsidian`
- **Backup:** weekly `pg_dump` + rsync at `/mnt/storage/backups/backup.sh`
- **Monitoring:** Dozzle (JSON logs)
- `/data/postgres`, `/data/redis` — git-ignored, never commit
