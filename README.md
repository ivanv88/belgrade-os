# Belgrade OS

A distributed personal operating system designed for orchestration on a 2020 Lenovo IdeaPad (i5 10th gen, 12GB RAM, 256GB SSD) running Pop!_OS (headless), featuring a modular, polyglot architecture communicating exclusively via Redis.

## 🏛️ Architecture Overview

Belgrade OS employs a "Redis-as-a-Transport" architecture. Services are decoupled and do not make direct inter-service calls; instead, they communicate using Redis Streams and Pub/Sub.

### Services

| Directory | Language | Role |
| :--- | :--- | :--- |
| `gateway/` | Go | HTTP entry point, JWT auth, secure UI serving, and RBAC enforcement. |
| `bridge/` | Rust | Capability registry and event broker with write-through Redis persistence. |
| `inference/` | Python | Inference Controller; drives the tool-use loop (supports Claude & Gemini). |
| `runner/` | Python | Resource Runner; consumes tool calls and dispatches to app processes. |
| `platform_controller/` | Python | The OS Kernel; manages app lifecycles and RBAC permission sync. |
| `vault_service/` | Python | Vault Gatekeeper; manages atomic writes to Obsidian knowledge base. |
| `notification/` | Python | Notification Service; dispatches alerts via ntfy.sh/Firebase. |
| `sdk/` | Python | Belgrade SDK; provides decorators and context for rapid app development. |

---

## 🚀 Getting Started

### 1. Prerequisites
- **Docker & Docker Compose** (for Redis and Postgres)
- **Go** (1.22+)
- **Rust** (Cargo)
- **Python** (3.9+)
- **Cloudflare Tunnel** (for secure remote access)

### 2. Initial Setup
```bash
# Install toolchain and dependencies
make deps

# Generate Protobuf code for all services
make proto

# Build Go and Rust binaries
make build
```

### 3. Environment Configuration
Create a `.env` file in the root directory (see `.env.example` if available) with:
- `CLOUDFLARE_TOKEN`
- `DB_PASSWORD`
- `ANTHROPIC_API_KEY` or `GOOGLE_API_KEY`

### 4. Running the OS
```bash
# 1. Start infrastructure (Redis, Postgres, Tunnel)
make dev

# 2. Seed permissions (required for UI access)
python3 scripts/seed_permissions.py

# 3. Start services (In separate terminals or via your process manager)
# In production, these are managed by the Platform Controller
cd platform_controller && python3 main.py
```

### 5. Running Tests
```bash
# Run all tests across all services
make test
```

---

## 🛍️ Developing Apps
Apps live in the `/apps` directory. Each app needs:
1.  `main.py`: Using the Belgrade SDK.
2.  `manifest.json`: Defining metadata and UI capabilities.
3.  `static/`: Optional folder for web/mobile UI assets.

To see the system in action, check out the [Demo App Guide](./apps/demo_app/README.md).

---

## 📜 Conventions
- **Proto First:** All message shapes are defined in `proto/belgrade_os.proto`.
- **Identity First:** `user_id` and `tenant_id` are propagated through every message.
- **Durable IO:** Apps should never write to disk directly; use `ctx.vault.write()`.
