# Belgrade OS — Repository Restructure Design

**Date:** 2026-05-16  
**Status:** Approved  
**Goal:** Eliminate root-level clutter, group services logically, eliminate code duplication, enforce consistent conventions across all Python services.

---

## 1. New Root Layout

```
belgrade-os/
├── services/              ← all platform services
│   ├── gateway/           (Go)
│   ├── inference/         (Python)
│   ├── runner/            (Python)
│   ├── bridge/            (Rust)
│   ├── notification/      (Python)
│   ├── vault_service/     (Python)
│   ├── platform_controller/ (Python)
│   ├── mcp_server/        (Python)
│   └── watchdog/          (Python — new dir, was watchdog.py at root)
├── apps/                  ← unchanged
├── sdk/                   ← unchanged
├── shared/                ← new: common Python constants
│   ├── __init__.py
│   └── streams.py         (Redis stream name constants used by multiple services)
├── proto/                 ← unchanged
├── config/                ← unchanged
├── scripts/               ← ops scripts (setup.sh, start.sh, stop.sh move here)
│   └── seed_permissions.py (already here)
├── deploy/                ← deployment artifacts
│   ├── watchdog.service   (systemd unit, moved from root)
│   └── runner-base/       (Dockerfile, moved from root)
│       └── Dockerfile
├── docs/                  ← real architectural docs only
│   ├── external-app-contract.md
│   ├── main.spec.md
│   └── tech.spec.md
├── .claude/               ← AI tooling (docs/superpowers/ moves here)
│   └── docs/
│       └── superpowers/
│           ├── plans/
│           └── specs/
├── docker-compose.yml     ← stays at root (convenience)
├── Makefile               ← stays at root, paths updated
├── .env.example           ← stays at root
├── README.md              ← updated
├── CLAUDE.md              ← updated
└── GEMINI.md              ← updated
```

**Deleted:** `gateway/api/` (empty directory), root `tests/` (watchdog test moves to `services/watchdog/tests/`).

---

## 2. shared/ Package

`shared/` is a plain directory on `PYTHONPATH`. Services import from it without installation.

### shared/streams.py

Centralises Redis stream name constants that appear in more than one service:

```python
INBOUND_STREAM         = "tasks:inbound"
TOOL_CALLS_STREAM      = "tasks:tool_calls"
TOOL_RESULTS_STREAM    = "tasks:tool_results"
UNTRUSTED_CALLS_STREAM = "tasks:untrusted_calls"
NOTIFICATIONS_STREAM   = "tasks:notifications"
VAULT_OPS_STREAM       = "tasks:vault_ops"
```

Services that currently define these as local module-level constants (`inference/redis_client.py`, `runner/redis_client.py`, `notification/redis_client.py`, `vault_service/redis_client.py`) will import from `shared.streams` instead. Each service's `RedisClient` class stays per-service — they are all fundamentally different.

`shared/` is made available by:
- `start.sh`: `export PYTHONPATH=.` before starting services
- Each `conftest.py`: adds repo root to `sys.path` (see Section 4)

---

## 3. scripts/ and deploy/

```
scripts/
├── setup.sh               (moved from root)
├── start.sh               (moved from root)
├── stop.sh                (moved from root)
└── seed_permissions.py    (already here)

deploy/
├── watchdog.service       (moved from root)
└── runner-base/
    └── Dockerfile         (moved from root runner-base/)
```

`docker-compose.yml` stays at root — it only mounts `data/` and `config/`, not service code, and is more ergonomic at root.

---

## 4. Python Service Convention Standard

Every Python service under `services/` follows this internal layout:

```
services/<name>/
├── main.py
├── worker.py              (if applicable)
├── config.py              (all env vars; no hardcoded values in logic files)
├── redis_client.py        (service-specific; stays per-service)
├── conftest.py            (pytest path setup — standardised across all services)
├── pytest.ini             (standardised content)
├── requirements.txt
├── requirements-dev.txt
├── tests/
│   ├── __init__.py
│   └── test_*.py
└── README.md
```

### conftest.py (standard content for all Python services)

```python
import sys
from pathlib import Path

SERVICE_DIR = Path(__file__).resolve().parent
REPO_ROOT   = SERVICE_DIR.parent.parent  # services/<name> → services → repo root

sys.path.insert(0, str(SERVICE_DIR))
sys.path.insert(0, str(REPO_ROOT))
```

Services currently missing `conftest.py`: `notification`, `vault_service`, `platform_controller`, `mcp_server`.

### pytest.ini (standard content for all Python services)

```ini
[pytest]
asyncio_mode = auto
asyncio_default_fixture_loop_scope = function
testpaths = tests
pythonpath = .
norecursedirs = gen
```

Currently inconsistent across services — some missing `testpaths`, `asyncio_default_fixture_loop_scope`, or `norecursedirs`.

### config.py (add where missing)

`platform_controller`, `vault_service`, and `mcp_server` currently have hardcoded defaults inline (via `os.getenv`). Each gets a `config.py` using `pydantic-settings`, consistent with `inference`, `runner`, and `notification`.

---

## 5. watchdog Service

`watchdog.py` at root becomes a proper service directory:

```
services/watchdog/
├── main.py                (renamed from watchdog.py)
├── tests/
│   ├── __init__.py
│   └── test_watchdog.py   (moved from root tests/)
├── conftest.py
├── pytest.ini
└── requirements.txt
```

`tests/test_watchdog.py` currently imports watchdog via a hardcoded relative path (`parent.parent / "watchdog.py"`). After the move it becomes `parent.parent / "main.py"`.

The root `tests/` directory is deleted once `test_watchdog.py` is moved.

---

## 6. Complete File Change Inventory

### Files moved (content unchanged)

| From | To |
|---|---|
| `gateway/` | `services/gateway/` |
| `inference/` | `services/inference/` |
| `runner/` | `services/runner/` |
| `bridge/` | `services/bridge/` |
| `notification/` | `services/notification/` |
| `vault_service/` | `services/vault_service/` |
| `platform_controller/` | `services/platform_controller/` |
| `mcp_server/` | `services/mcp_server/` |
| `watchdog.py` | `services/watchdog/main.py` |
| `tests/test_watchdog.py` | `services/watchdog/tests/test_watchdog.py` |
| `setup.sh` | `scripts/setup.sh` |
| `start.sh` | `scripts/start.sh` |
| `stop.sh` | `scripts/stop.sh` |
| `watchdog.service` | `deploy/watchdog.service` |
| `runner-base/` | `deploy/runner-base/` |
| `docs/superpowers/` | `.claude/docs/superpowers/` |

### Files with path/content updates

| File | What changes |
|---|---|
| `Makefile` | `cd <service>` → `cd services/<service>`; gen file paths; requirements paths; `./start.sh` → `./scripts/start.sh`; `./stop.sh` → `./scripts/stop.sh`; add missing `vault_service` to `make test` |
| `scripts/start.sh` | All binary + python paths prefixed with `services/`; add `export PYTHONPATH=.` |
| `scripts/stop.sh` | No path changes (uses run/*.pid only) |
| `scripts/setup.sh` | Requirements paths in `make deps` (via Makefile); `mkdir -p` lines (logs/data stay at root) |
| `deploy/watchdog.service` | `ExecStart`: `/opt/belgrade-os/watchdog.py` → `/opt/belgrade-os/services/watchdog/main.py` |
| `services/bridge/build.rs` | `../proto/` → `../../proto/` |
| `.gitignore` | All `<service>/gen/` → `services/<service>/gen/`; add `logs/` and `run/` entries |
| `README.md` | Service table paths; setup instructions (`./setup.sh` → `./scripts/setup.sh`) |
| `GEMINI.md` | Service table paths |
| `CLAUDE.md` | Architecture table; per-service test commands; gen output paths; add mcp_server to architecture |
| `docs/main.spec.md` | `gateway/`, `inference/`, `notification/drivers/` references |
| `services/watchdog/tests/test_watchdog.py` | Import path: `parent.parent / "watchdog.py"` → `parent.parent / "main.py"` |
| All Python service `conftest.py` | Standardise to add both service dir and repo root to sys.path |
| All Python service `pytest.ini` | Standardise content |
| Services with local stream constants | Import from `shared.streams` instead |

### Files created

| File | Purpose |
|---|---|
| `shared/__init__.py` | Makes shared/ a package |
| `shared/streams.py` | Redis stream name constants |
| `services/watchdog/conftest.py` | |
| `services/watchdog/pytest.ini` | |
| `services/watchdog/requirements.txt` | |
| `services/notification/conftest.py` | (missing) |
| `services/vault_service/conftest.py` | (missing) |
| `services/platform_controller/conftest.py` | (missing) |
| `services/mcp_server/conftest.py` | (missing) |
| `services/platform_controller/config.py` | Extract from main.py os.getenv calls |
| `services/vault_service/config.py` | Extract from main.py os.getenv calls |
| `services/mcp_server/config.py` | Extract from main.py os.getenv calls |

### Files deleted

| File | Reason |
|---|---|
| `gateway/api/` | Empty directory |
| `tests/` (root) | Watchdog test moves to services/watchdog/tests/ |
| `watchdog.py` | Moved to services/watchdog/main.py |
| `watchdog.service` | Moved to deploy/ |
| `runner-base/` | Moved to deploy/ |
| `setup.sh` / `start.sh` / `stop.sh` | Moved to scripts/ |

---

## 7. What Does NOT Change

- `docker-compose.yml` — no service paths in it; stays at root
- Go source files — imports are module-relative (`belgrade-os/gateway/...`); no edits needed
- Python service `RedisClient` classes — stay per-service; only stream constants move to shared
- `proto/belgrade_os.proto` — stays at root
- `config/` — stays at root
- `apps/` — stays at root
- `sdk/` — stays at root
- `data/`, `logs/` — gitignored, not tracked
- `.env`, `.env.example` — stay at root

---

## 8. mcp_server Architecture Documentation

`mcp_server` is a live service (MCP JSON-RPC server, OAuth endpoint, bridge integration) that is currently absent from `CLAUDE.md`'s architecture section. It will be added in the documentation update:

- **Port:** 8083 (via `MCP_PORT` env var)
- **Role:** Exposes Belgrade OS tools to external MCP clients (e.g., Claude Desktop) via JSON-RPC over HTTP with Cloudflare JWT auth
- **Streams used:** none directly; calls bridge HTTP API
- **Start order:** after bridge

---

## 9. Verification Checklist (per implementation step)

Before marking any step done:

1. **Service move**: `cd services/<name> && <build/test command>` passes
2. **bridge**: `cd services/bridge && cargo build` succeeds (proto path check)
3. **Makefile**: `make proto && make build && make test` all pass from repo root
4. **shared/**: Each modified service's tests still pass (`from shared.streams import ...` resolves)
5. **Docs**: No broken path references remain in CLAUDE.md, GEMINI.md, README.md, docs/main.spec.md
