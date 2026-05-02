# Belgrade OS

A distributed personal operating system designed for orchestration on a 2020 Lenovo IdeaPad (i5 10th gen, 12GB RAM, 256GB SSD) running Pop!_OS (headless), featuring a modular, microservices-based architecture communicating exclusively via Redis.

## Architecture Overview

Belgrade OS employs a "Redis-as-a-Transport" architecture. Services are decoupled and do not make direct inter-service calls; instead, they communicate using Redis Streams (for tasks and tool calls) and Pub/Sub (for real-time streaming to the gateway).

### Services

| Directory | Language | Role |
| :--- | :--- | :--- |
| `gateway/` | Go | HTTP entry point, JWT authentication (Cloudflare), task ingestion, SSE proxying, and **secure UI serving**. |
| `inference/` | Python | Inference Controller; interacts with LLMs (Claude) and drives the tool-use loop. |
| `runner/` | Python | Resource Runner; consumes tool calls from Redis and dispatches them to the Capability Bridge. |
| `bridge/` | Rust | Capability Bridge; the central tool registry and event broker. Forwards tool calls and events to apps. |
| `platform_controller/` | Python | The OS Kernel; manages app sub-processes, hot-reloading, dynamic scheduler, and **RBAC sync**. |
| `vault_service/` | Python | Vault Service; gatekeeps the Obsidian vault, performing atomic writes via Redis streams. |
| `notification/` | Python | Notification Service; consumes `tasks:notifications` and dispatches via ntfy.sh. |
| `sdk/` | Python | Belgrade SDK; provides the `@tool` and `@on_event` decorators and **indirect Vault access**. |

### App Execution & Tool Discovery

The Belgrade OS uses a distributed, callback-based model managed by the **Platform Controller**:

1.  **Lifecycle**: The Platform Controller scans `/apps`, spawns them as sub-processes, and redirects logs to `apps/{id}/app.log`.
2.  **Registration**: Apps use the SDK to register tools and subscriptions with the `bridge` via `POST /v1/register` on startup.
3.  **Security (RBAC)**: The Platform Controller syncs app permissions from Postgres to Redis. The Gateway enforces these permissions for both API calls and UI asset serving.
4.  **Multi-UI**: The Gateway serves static assets from `/apps/{app_id}/static/{bundle}/` and injects `window.BELGRADE_CONFIG` for zero-config frontend development.
5.  **Multi-Tenancy**: `user_id` and `tenant_id` are propagated through all messages, enabling the SDK to provide scoped `ctx.db` (Postgres schemas) and `ctx.notify`.

### Data Flow & Communication


1.  **Ingestion**: `gateway` receives a request, validates the JWT, and XADDs a `Task` to the `tasks:inbound` Redis Stream.
2.  **Orchestration**: `inference` reads from `tasks:inbound`, calls the LLM, and emits tool calls to `tasks:tool_calls`.
3.  **Execution**: `runner` reads from `tasks:tool_calls`, executes the requested tool, and returns the result to `tasks:tool_results`.
4.  **Feedback**: `inference` processes results and publishes `ThoughtEvent` messages to the `sse:{task_id}` Pub/Sub channel.
5.  **Delivery**: `gateway` subscribes to the task channel and streams events to the client via SSE.

## Building and Running

The project uses a `Makefile` for primary development tasks.

### Prerequisites

```bash
# Install toolchain (macOS)
make deps
```

### Development Commands

```bash
# Generate Protobuf code for all services (Required after .proto changes)
make proto

# Build Go and Rust binaries
make build

# Start local infrastructure (Redis and Cloudflare Tunnel)
make dev

# Run all tests across all services
make test

# Clean generated artifacts
make clean
```

### Service-Specific Testing

```bash
# Go (Gateway)
cd gateway && go test ./... -v

# Python (Inference/Runner/Notification)
cd runner && python3 -m pytest tests/ -v
cd inference && python3 -m pytest tests/ -v
cd notification && python3 -m pytest tests/ -v

# Rust (Bridge)
cd bridge && cargo test
```

## Development Conventions

### Protobuf as Source of Truth

All inter-service message shapes are defined in `proto/belgrade_os.proto`. **Never** modify generated code directly. Always update the `.proto` file and run `make proto`.

### Redis Transport Patterns

*   **Streams**: Used for reliable task delivery (`XADD`, `XREADGROUP`).
*   **Pub/Sub**: Used for ephemeral, real-time event streaming (`PUBLISH`, `SUBSCRIBE`).
*   **Leases**: Ephemeral keys with TTLs used for worker state tracking.

### Reliability & Tracing

*   **Distributed Tracing**: Every `Task` carries a `trace_id` which must be propagated through all tool calls, results, and events.
*   **Circuit Breaking**: Execution durations are tracked in `ToolResult` to enable monitoring and circuit breaking in the controller.
*   **Graceful Shutdown**: The Gateway and services should handle SIGINT/SIGTERM by allowing in-flight tasks/streams to complete (typically a 30s timeout).

### Security

*   **Authentication**: Gateway validates `Cf-Access-Jwt-Assertion` (RS256 JWT from Cloudflare Zero Trust).
*   **Secrets**: Managed via `.env` files; never commit secrets to the repository.
*   **Local Dev**: Redis-dependent tests should skip gracefully if Redis is unavailable.
sts should skip gracefully if Redis is unavailable.
