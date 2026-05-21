# Security Track B — Hybrid Execution Model Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Enforce per-user execution isolation — trusted users run tools via the existing SDK/Redis path; untrusted users run tools in ephemeral air-gapped Docker containers with seccomp confinement.

**Architecture:** The Gateway stamps each Task with `ExecutionMode` (TRUSTED/UNTRUSTED) based on a whitelist env var. The Inference Controller propagates `ExecutionMode` onto every ToolCall it emits. A new Platform Controller stream consumer reads `tasks:untrusted_calls`, runs untrusted tool code in a short-lived Docker container with `--network none` and a seccomp profile that blocks fork/exec, and publishes the result to `tasks:tool_results`. Trusted ToolCalls continue flowing through the existing Resource Runner unchanged. All services connect to Redis with scoped per-service credentials enforced via Redis ACL.

**Tech Stack:** proto3 (prost/protoc-gen-go), Redis 7 ACL, Docker seccomp (linux/seccomp), asyncio subprocess, pydantic-settings v2 AliasChoices, Go 1.21, Rust (prost), pytest, go test.

**Out of scope:** Inference Controller changes to route ToolCalls to `tasks:untrusted_calls` (separate follow-up plan — until that lands, `tasks:untrusted_calls` will sit empty and no isolation is enforced in practice). App-specific Docker images and the file-based I/O protocol (`input.json`/`output.json`) that untrusted apps must implement (separate follow-up — existing apps are SDK HTTP servers, not file-based, so the EphemeralRunner cannot execute them as-is). Platform Controller changes are infrastructure-only in this plan.

**Seccomp scope note:** The seccomp profile blocks process spawning (fork/exec family) as defense-in-depth alongside `--network none`. It is not a complete syscall whitelist sandbox — Python requires a large surface area. The primary isolation mechanism is network isolation; seccomp prevents a compromised process from spawning subprocesses that could further escalate.

---

## File Map

| Action | Path | Purpose |
|--------|------|---------|
| Modify | `proto/belgrade_os.proto` | Add `ExecutionMode` enum + field 6 on `Task`, field 8 on `ToolCall` |
| Create | `config/.gitkeep` | Track `config/` dir in git |
| Create | `config/redis.acl.template` | ACL template (placeholder passwords) |
| Modify | `.gitignore` | Ignore `config/redis.acl` |
| Modify | `setup.sh` | Add `generate_acl()` function |
| Modify | `docker-compose.yml` | Switch Redis from `--requirepass` to `--aclfile` volume |
| Create | `config/seccomp-untrusted.json` | Block fork/vfork/execve/execveat |
| Create | `runner-base/Dockerfile` | Minimal Python 3.12 image without network tools |
| Modify | `gateway/config.go` | Prefer `GATEWAY_REDIS_URL`, add `TrustedUserIDs` field |
| Modify | `gateway/config_test.go` | Tests for `GATEWAY_REDIS_URL` precedence + `TrustedUserIDs` |
| Modify | `runner/config.py` | Prefer `RUNNER_REDIS_URL` via `AliasChoices` |
| Modify | `inference/config.py` | Prefer `INFERENCE_REDIS_URL` via `AliasChoices` |
| Modify | `notification/config.py` | Prefer `NOTIFICATION_REDIS_URL` via `AliasChoices` |
| Create | `vault_service/config.py` | Prefer `VAULT_REDIS_URL` via `AliasChoices` (add if absent) |
| Modify | `bridge/src/config.rs` | Prefer `BRIDGE_REDIS_URL`, update test |
| Modify | `platform_controller/main.py` | Prefer `CONTROLLER_REDIS_URL`, add `TRUSTED_USER_IDS` |
| Create | `gateway/auth/trust.go` | `TrustedSet` + `ParseTrustedUsers` + `LoadTrustedUsers` |
| Create | `gateway/auth/trust_test.go` | Tests: parse, whitespace, empty, contains |
| Modify | `gateway/handler.go` | `ExecutionMode` on `taskRequest`, `trustedUsers` on `Handler`, proto field |
| Modify | `gateway/main.go` | Pass `auth.LoadTrustedUsers()` to `NewHandler` |
| Create | `platform_controller/ephemeral_runner.py` | Docker subprocess, 30 s timeout, output validation, cleanup |
| Create | `platform_controller/tests/test_ephemeral_runner.py` | Mock subprocess, timeout, validation, cleanup tests |
| Modify | `platform_controller/main.py` | `tasks:untrusted_calls` consumer, route to `EphemeralRunner` |
| Create | `platform_controller/tests/test_routing.py` | Routing integration tests |

---

## Task 1: Proto extension + Redis ACL infrastructure

**Files:**
- Modify: `proto/belgrade_os.proto`
- Create: `config/.gitkeep`
- Create: `config/redis.acl.template`
- Modify: `.gitignore`
- Modify: `setup.sh`
- Modify: `docker-compose.yml`

- [ ] **Step 1: Write the failing test (proto build)**

  No unit test for proto; verify by running `make proto` before the change and confirming it succeeds, so we have a known-good baseline:
  ```bash
  make proto
  ```
  Expected: `proto codegen complete`

- [ ] **Step 2: Add `ExecutionMode` enum + field to proto**

  Edit `proto/belgrade_os.proto`. After the `ThoughtEventType` enum block (line 60–67), add the new enum. Then add field 6 to `Task` and field 8 to `ToolCall`:

  Find the `enum ThoughtEventType` block:
  ```proto
  enum ThoughtEventType {
    THOUGHT_EVENT_TYPE_UNSPECIFIED = 0;
    THINKING                       = 1;
    TOOL_USE                       = 2;
    RESPONSE_CHUNK                 = 3;
    DONE                           = 4;
    ERROR                          = 5;
  }
  ```

  Add after it:
  ```proto
  enum ExecutionMode {
    EXECUTION_MODE_UNSPECIFIED = 0;
    TRUSTED                    = 1;
    UNTRUSTED                  = 2;
  }
  ```

  Change the `Task` message from:
  ```proto
  message Task {
    string task_id       = 1;
    string user_id       = 2;
    string prompt        = 3;
    int64  created_at_ms = 4;
    string trace_id      = 5;
  }
  ```
  To:
  ```proto
  message Task {
    string        task_id        = 1;
    string        user_id        = 2;
    string        prompt         = 3;
    int64         created_at_ms  = 4;
    string        trace_id       = 5;
    ExecutionMode execution_mode = 6;
  }
  ```

  Change the `ToolCall` message from:
  ```proto
  message ToolCall {
    string call_id    = 1;
    string task_id    = 2;
    string tool_name  = 3;
    string input_json = 4;
    string trace_id   = 5;
    string user_id    = 6;
    string tenant_id  = 7;
  }
  ```
  To:
  ```proto
  message ToolCall {
    string        call_id         = 1;
    string        task_id         = 2;
    string        tool_name       = 3;
    string        input_json      = 4;
    string        trace_id        = 5;
    string        user_id         = 6;
    string        tenant_id       = 7;
    ExecutionMode execution_mode  = 8;
  }
  ```

- [ ] **Step 3: Regenerate all proto outputs**

  ```bash
  make proto
  ```
  Expected: `proto codegen complete` with no errors.

- [ ] **Step 4: Verify gateway build still passes**

  ```bash
  cd gateway && go build ./...
  ```
  Expected: no output (success).

- [ ] **Step 5: Verify all service tests still pass**

  ```bash
  cd gateway && go test ./... -v 2>&1 | tail -5
  cd bridge && cargo test 2>&1 | tail -5
  cd runner && python3 -m pytest tests/ -v 2>&1 | tail -5
  ```
  Expected: `ok` / `test result: ok` / all green.

- [ ] **Step 6: Create config/ directory + template**

  ```bash
  touch config/.gitkeep
  ```

  Create `config/redis.acl.template`:
  ```
  user default off nopass nocommands
  user gateway on >GATEWAY_REDIS_PASSWORD ~perms:* ~tasks:inbound &sse:* +hget +hmget +xadd +subscribe
  user inference on >INFERENCE_REDIS_PASSWORD ~tasks:* &sse:* +xreadgroup +xread +xadd +xack +xgroup +publish
  user runner on >RUNNER_REDIS_PASSWORD ~tasks:tool_calls ~tasks:tool_results +xreadgroup +xadd +xack +xgroup
  user notification on >NOTIFICATION_REDIS_PASSWORD ~tasks:notifications +xreadgroup +xack +xgroup
  user vault on >VAULT_REDIS_PASSWORD ~tasks:vault_ops +xreadgroup +xack +xgroup
  user bridge on >BRIDGE_REDIS_PASSWORD ~registry:* +get +set +hget +hset +hmget
  user controller on >CONTROLLER_REDIS_PASSWORD ~perms:* ~tasks:tool_results ~tasks:untrusted_calls +hset +hget +hmget +xadd +xreadgroup +xack +xgroup
  ```

  Notes on the ACL syntax:
  - `~key:pattern` — grants key access to matching keys
  - `&channel:pattern` — grants pub/sub access to matching channels (required for `SUBSCRIBE`/`PUBLISH`)
  - `+command` — grants that Redis command
  - `>password` — the user's password

- [ ] **Step 7: Update .gitignore to exclude generated ACL file**

  Open `.gitignore` and add the following block (create the file if it doesn't exist):
  ```
  # Redis ACL (contains per-service passwords generated by setup.sh)
  config/redis.acl
  ```

- [ ] **Step 8: Add `generate_acl()` to setup.sh**

  Open `setup.sh`. Add the following function after the `generate_env()` function definition (before the `harden_firewall()` function):

  ```bash
  # --- Redis ACL Generation ---
  generate_acl() {
      if [ -f "config/redis.acl" ]; then
          echo "✅ config/redis.acl already exists — skipping generation."
          return
      fi

      if ! command -v openssl &> /dev/null; then
          echo "⚠️  openssl not found — copy config/redis.acl.template to config/redis.acl and fill in passwords manually."
          return
      fi

      echo "Generating config/redis.acl with per-service passwords..."
      mkdir -p config

      GATEWAY_PASS=$(openssl rand -hex 32)
      INFERENCE_PASS=$(openssl rand -hex 32)
      RUNNER_PASS=$(openssl rand -hex 32)
      NOTIFICATION_PASS=$(openssl rand -hex 32)
      VAULT_PASS=$(openssl rand -hex 32)
      BRIDGE_PASS=$(openssl rand -hex 32)
      CONTROLLER_PASS=$(openssl rand -hex 32)

      cat > config/redis.acl << EOF
  user default off nopass nocommands
  user gateway on >${GATEWAY_PASS} ~perms:* ~tasks:inbound &sse:* +hget +hmget +xadd +subscribe
  user inference on >${INFERENCE_PASS} ~tasks:* &sse:* +xreadgroup +xread +xadd +xack +xgroup +publish
  user runner on >${RUNNER_PASS} ~tasks:tool_calls ~tasks:tool_results +xreadgroup +xadd +xack +xgroup
  user notification on >${NOTIFICATION_PASS} ~tasks:notifications +xreadgroup +xack +xgroup
  user vault on >${VAULT_PASS} ~tasks:vault_ops +xreadgroup +xack +xgroup
  user bridge on >${BRIDGE_PASS} ~registry:* +get +set +hget +hset +hmget
  user controller on >${CONTROLLER_PASS} ~perms:* ~tasks:tool_results ~tasks:untrusted_calls +hset +hget +hmget +xadd +xreadgroup +xack +xgroup
  EOF
      chmod 600 config/redis.acl

      # Append per-service URLs to .env (idempotent: skip if already present)
      if ! grep -q "GATEWAY_REDIS_URL" .env 2>/dev/null; then
          cat >> .env << EOF

  # Per-service Redis credentials (generated by setup.sh generate_acl)
  GATEWAY_REDIS_URL=redis://gateway:${GATEWAY_PASS}@localhost:6379
  INFERENCE_REDIS_URL=redis://inference:${INFERENCE_PASS}@localhost:6379
  RUNNER_REDIS_URL=redis://runner:${RUNNER_PASS}@localhost:6379
  NOTIFICATION_REDIS_URL=redis://notification:${NOTIFICATION_PASS}@localhost:6379
  VAULT_REDIS_URL=redis://vault:${VAULT_PASS}@localhost:6379
  BRIDGE_REDIS_URL=redis://bridge:${BRIDGE_PASS}@localhost:6379
  CONTROLLER_REDIS_URL=redis://controller:${CONTROLLER_PASS}@localhost:6379
  EOF
      fi

      echo "✅ config/redis.acl created. .env updated with per-service Redis URLs."
  }
  ```

  Then add a call to `generate_acl` after the existing `generate_env` call (around line 128):
  ```bash
  # --- Generate .env ---
  generate_env

  # --- Generate Redis ACL ---
  generate_acl

  # --- Harden firewall ---
  harden_firewall
  ```

- [ ] **Step 9: Update docker-compose.yml redis service**

  In `docker-compose.yml`, the `redis` service currently has:
  ```yaml
      command: >
        redis-server
        --requirepass ${REDIS_PASSWORD}
        --appendonly yes
        --rename-command CONFIG ""
        --rename-command FLUSHALL ""
        --rename-command FLUSHDB ""
        --rename-command DEBUG ""
      volumes:
        - ./data/redis:/data
  ```

  Replace with:
  ```yaml
      command: >
        redis-server
        --aclfile /usr/local/etc/redis/users.acl
        --appendonly yes
        --rename-command CONFIG ""
        --rename-command FLUSHALL ""
        --rename-command FLUSHDB ""
        --rename-command DEBUG ""
      volumes:
        - ./data/redis:/data
        - ./config/redis.acl:/usr/local/etc/redis/users.acl:ro
  ```

- [ ] **Step 10: Commit**

  ```bash
  git add proto/belgrade_os.proto gateway/gen/belgrade_os.pb.go runner/gen/ inference/gen/ notification/gen/ sdk/belgrade_sdk/gen/ vault_service/gen/ config/.gitkeep config/redis.acl.template .gitignore setup.sh docker-compose.yml
  git commit -m "feat(proto,infra): ExecutionMode enum, Redis ACL infrastructure"
  ```

---

## Task 2: Seccomp profile + runner base image

**Files:**
- Create: `config/seccomp-untrusted.json`
- Create: `runner-base/Dockerfile`

- [ ] **Step 1: Verify Docker is available**

  ```bash
  docker --version
  ```
  Expected: `Docker version 24.x.x` or similar (any version).

- [ ] **Step 2: Create seccomp profile**

  Create `config/seccomp-untrusted.json`:
  ```json
  {
    "defaultAction": "SCMP_ACT_ALLOW",
    "syscalls": [
      {
        "names": ["fork", "vfork", "execve", "execveat"],
        "action": "SCMP_ACT_ERRNO",
        "args": []
      }
    ]
  }
  ```

  This profile:
  - Allows all syscalls by default (Python needs a large surface area)
  - Blocks only `fork`, `vfork`, `execve`, `execveat` — preventing subprocess creation
  - Allows `clone` — Python threads use `clone` without exec, which cannot load new code

- [ ] **Step 3: Create runner base Dockerfile**

  Create `runner-base/Dockerfile`:
  ```dockerfile
  FROM python:3.12-alpine

  # Remove tools that could be used for network exfiltration or code execution.
  # git, wget, and curl are the primary concern. apk-tools is removed so the
  # container cannot install additional packages at runtime.
  RUN apk del --purge curl wget git apk-tools 2>/dev/null; \
      addgroup -g 1000 runner && \
      adduser -u 1000 -G runner -D runner

  WORKDIR /workspace
  USER 1000:1000
  ```

  The seccomp profile (applied at `docker run` time) blocks exec-family syscalls, so the removal of shell tools is defense-in-depth — the container cannot spawn new processes even if a binary is present.

- [ ] **Step 4: Build the base image to verify the Dockerfile**

  ```bash
  docker build -t beg-os-runner-base:1.0 runner-base/
  ```
  Expected: `Successfully tagged beg-os-runner-base:1.0`

- [ ] **Step 5: Verify the image is non-root and network tools absent**

  ```bash
  docker run --rm beg-os-runner-base:1.0 id
  ```
  Expected: `uid=1000(runner) gid=1000(runner) groups=1000(runner)`

  ```bash
  docker run --rm beg-os-runner-base:1.0 sh -c "command -v curl || echo 'curl absent'"
  ```
  Expected: `curl absent`

- [ ] **Step 6: Commit**

  ```bash
  git add config/seccomp-untrusted.json runner-base/Dockerfile
  git commit -m "feat(security): seccomp profile + runner base image"
  ```

---

## Task 3: Service Redis credential wiring

Wire per-service Redis URLs so each service connects with scoped credentials. All services keep a fallback to the generic `REDIS_URL` env var for local dev without the ACL file.

**Files:**
- Modify: `gateway/config.go`
- Modify: `gateway/config_test.go`
- Modify: `runner/config.py`
- Modify: `inference/config.py`
- Modify: `notification/config.py`
- Modify: `vault_service/config.py` (or create if missing)
- Modify: `bridge/src/config.rs`
- Modify: `platform_controller/main.py`

- [ ] **Step 1: Write failing Go test for GATEWAY_REDIS_URL precedence**

  Add to `gateway/config_test.go`:
  ```go
  func TestGatewayRedisURLPrecedence(t *testing.T) {
  	t.Setenv("GATEWAY_REDIS_URL", "redis://gateway:pw@localhost:6379")
  	t.Setenv("REDIS_URL", "redis://generic:6379")
  	cfg := LoadConfig()
  	if cfg.RedisURL != "redis://gateway:pw@localhost:6379" {
  		t.Fatalf("GATEWAY_REDIS_URL should take precedence, got %s", cfg.RedisURL)
  	}
  }

  func TestRedisURLFallbackToGeneric(t *testing.T) {
  	os.Unsetenv("GATEWAY_REDIS_URL")
  	t.Setenv("REDIS_URL", "redis://fallback:6379")
  	cfg := LoadConfig()
  	if cfg.RedisURL != "redis://fallback:6379" {
  		t.Fatalf("should fall back to REDIS_URL, got %s", cfg.RedisURL)
  	}
  }
  ```

- [ ] **Step 2: Run the Go test to verify it fails**

  ```bash
  cd gateway && go test -run "TestGatewayRedisURLPrecedence|TestRedisURLFallbackToGeneric" -v
  ```
  Expected: `FAIL` — `GATEWAY_REDIS_URL` not yet wired.

- [ ] **Step 3: Update gateway/config.go**

  Replace the current `LoadConfig()` function and add `getEnvOr`:

  Current `config.go`:
  ```go
  package main

  import "os"

  type Config struct {
  	Port         string
  	RedisURL     string
  	CFTeamDomain string
  	CFAudience   string
  	AppsRoot     string
  	GatewayURL   string
  }

  func LoadConfig() Config {
  	return Config{
  		Port:         getEnv("PORT", "8080"),
  		RedisURL:     getEnv("REDIS_URL", "redis://localhost:6379"),
  		CFTeamDomain: getEnv("CF_TEAM_DOMAIN", ""),
  		CFAudience:   getEnv("CF_AUDIENCE", ""),
  		AppsRoot:     getEnv("APPS_ROOT", "../apps"),
  		GatewayURL:   getEnv("GATEWAY_URL", "http://localhost:8080"),
  	}
  }

  func getEnv(key, fallback string) string {
  	if v := os.Getenv(key); v != "" {
  		return v
  	}
  	return fallback
  }
  ```

  Replace with:
  ```go
  package main

  import "os"

  type Config struct {
  	Port           string
  	RedisURL       string
  	CFTeamDomain   string
  	CFAudience     string
  	AppsRoot       string
  	GatewayURL     string
  	TrustedUserIDs string
  }

  func LoadConfig() Config {
  	return Config{
  		Port:           getEnv("PORT", "8080"),
  		RedisURL:       getEnvOr("GATEWAY_REDIS_URL", getEnv("REDIS_URL", "redis://localhost:6379")),
  		CFTeamDomain:   getEnv("CF_TEAM_DOMAIN", ""),
  		CFAudience:     getEnv("CF_AUDIENCE", ""),
  		AppsRoot:       getEnv("APPS_ROOT", "../apps"),
  		GatewayURL:     getEnv("GATEWAY_URL", "http://localhost:8080"),
  		TrustedUserIDs: getEnv("TRUSTED_USER_IDS", ""),
  	}
  }

  func getEnv(key, fallback string) string {
  	if v := os.Getenv(key); v != "" {
  		return v
  	}
  	return fallback
  }

  // getEnvOr returns value if non-empty, otherwise fallback.
  func getEnvOr(value, fallback string) string {
  	if value != "" {
  		return value
  	}
  	return fallback
  }
  ```

- [ ] **Step 4: Run the Go test to verify it passes**

  ```bash
  cd gateway && go test -run "TestGatewayRedisURLPrecedence|TestRedisURLFallbackToGeneric" -v
  ```
  Expected: `PASS`

- [ ] **Step 5: Write failing Python test for RUNNER_REDIS_URL**

  Create `runner/tests/test_config.py`:
  ```python
  import os
  from unittest.mock import patch
  import pytest
  from config import Config


  def test_runner_redis_url_takes_precedence():
      env = {"RUNNER_REDIS_URL": "redis://runner:pw@localhost:6379", "REDIS_URL": "redis://generic:6379"}
      with patch.dict(os.environ, env):
          cfg = Config()
          assert cfg.redis_url == "redis://runner:pw@localhost:6379"


  def test_falls_back_to_redis_url():
      env = {"REDIS_URL": "redis://fallback:6379"}
      with patch.dict(os.environ, env, clear=True):
          cfg = Config()
          assert cfg.redis_url == "redis://fallback:6379"


  def test_default_when_neither_set():
      with patch.dict(os.environ, {}, clear=True):
          cfg = Config()
          assert cfg.redis_url == "redis://localhost:6379"
  ```

- [ ] **Step 6: Run the test to verify it fails**

  ```bash
  cd runner && python3 -m pytest tests/test_config.py -v
  ```
  Expected: `FAILED` — `RUNNER_REDIS_URL` not yet read.

- [ ] **Step 7: Update runner/config.py**

  Replace with:
  ```python
  from __future__ import annotations
  import socket
  from pydantic import Field, AliasChoices
  from pydantic_settings import BaseSettings


  class Config(BaseSettings):
      redis_url: str = Field(
          default="redis://localhost:6379",
          validation_alias=AliasChoices("RUNNER_REDIS_URL", "REDIS_URL"),
      )
      bridge_url: str = "http://localhost:8081"
      worker_id: str = ""
      lease_ttl_s: int = 60
      tool_timeout_s: int = 30
      model_config = {"env_file": ".env", "populate_by_name": True}

      @property
      def effective_worker_id(self) -> str:
          return self.worker_id or socket.gethostname()


  def load_config() -> Config:
      return Config()
  ```

- [ ] **Step 8: Run runner config tests to verify they pass**

  ```bash
  cd runner && python3 -m pytest tests/test_config.py -v
  ```
  Expected: `3 passed`

- [ ] **Step 9: Update inference/config.py**

  The inference service uses pydantic-settings. Add `AliasChoices` for `INFERENCE_REDIS_URL`:

  Current `inference/config.py` has `redis_url: str = "redis://localhost:6379"`. Change that field to:
  ```python
  from pydantic import Field, AliasChoices

  class Config(BaseSettings):
      redis_url: str = Field(
          default="redis://localhost:6379",
          validation_alias=AliasChoices("INFERENCE_REDIS_URL", "REDIS_URL"),
      )
      # ... rest of fields unchanged
      model_config = {"env_file": ".env", "populate_by_name": True}
  ```

  Keep all other fields (`provider`, `model`, `max_tokens`, `consumer_id`, `anthropic_api_key`, `google_api_key`, `ollama_base_url`) unchanged.

- [ ] **Step 10: Update notification/config.py**

  Same pattern — add `AliasChoices` for `NOTIFICATION_REDIS_URL`:
  ```python
  from __future__ import annotations
  import socket
  from pydantic import Field, AliasChoices
  from pydantic_settings import BaseSettings


  class Config(BaseSettings):
      redis_url: str = Field(
          default="redis://localhost:6379",
          validation_alias=AliasChoices("NOTIFICATION_REDIS_URL", "REDIS_URL"),
      )
      ntfy_base_url: str = "https://ntfy.sh"
      ntfy_topic: str = "belgrade-os"
      notification_driver: str = "ntfy"
      worker_id: str = ""
      model_config = {"env_file": ".env", "populate_by_name": True}

      @property
      def effective_worker_id(self) -> str:
          return self.worker_id or socket.gethostname()


  def load_config() -> Config:
      return Config()
  ```

- [ ] **Step 11: Update vault_service — check and update its config**

  Check if `vault_service/config.py` exists and has `redis_url`. If it does, apply the same `AliasChoices` pattern with `VAULT_REDIS_URL`:
  ```python
  redis_url: str = Field(
      default="redis://localhost:6379",
      validation_alias=AliasChoices("VAULT_REDIS_URL", "REDIS_URL"),
  )
  ```
  Add `model_config = {"env_file": ".env", "populate_by_name": True}`.

  If `vault_service/config.py` does not exist (vault uses `os.getenv` directly in `main.py`), find where `REDIS_URL` is read and change it to:
  ```python
  REDIS_URL = os.getenv("VAULT_REDIS_URL") or os.getenv("BEG_OS_REDIS_URL", "redis://localhost:6379")
  ```

- [ ] **Step 12: Write failing Rust test for BRIDGE_REDIS_URL**

  Open `bridge/src/config.rs`. In the `#[cfg(test)]` block, add after the existing `test_redis_url_from_env` test:
  ```rust
  #[test]
  fn test_bridge_redis_url_takes_precedence() {
      let cfg = Config::from_map(lookup(&[
          ("BRIDGE_REDIS_URL", "redis://bridge:pw@localhost:6379"),
          ("REDIS_URL", "redis://generic:6379"),
      ]));
      assert_eq!(cfg.redis_url, "redis://bridge:pw@localhost:6379");
  }

  #[test]
  fn test_bridge_redis_url_falls_back_to_redis_url() {
      let cfg = Config::from_map(lookup(&[("REDIS_URL", "redis://fallback:6379")]));
      assert_eq!(cfg.redis_url, "redis://fallback:6379");
  }
  ```

- [ ] **Step 13: Run bridge test to verify it fails**

  ```bash
  cd bridge && cargo test test_bridge_redis_url -- --nocapture
  ```
  Expected: `FAILED` — `BRIDGE_REDIS_URL` not yet read.

- [ ] **Step 14: Update bridge/src/config.rs**

  In the `from_map` function, change the `redis_url` assignment from:
  ```rust
  redis_url: lookup("REDIS_URL")
      .unwrap_or_else(|| "redis://localhost:6379".to_string()),
  ```
  To:
  ```rust
  redis_url: lookup("BRIDGE_REDIS_URL")
      .or_else(|| lookup("REDIS_URL"))
      .unwrap_or_else(|| "redis://localhost:6379".to_string()),
  ```

- [ ] **Step 15: Run bridge tests to verify they pass**

  ```bash
  cd bridge && cargo test -- --nocapture 2>&1 | tail -10
  ```
  Expected: `test result: ok. N passed`

- [ ] **Step 16: Update platform_controller/main.py Redis URL**

  Find:
  ```python
  REDIS_URL = os.getenv("BEG_OS_REDIS_URL", "redis://localhost:6379")
  ```
  Replace with:
  ```python
  REDIS_URL = os.getenv("CONTROLLER_REDIS_URL") or os.getenv("BEG_OS_REDIS_URL", "redis://localhost:6379")
  ```

- [ ] **Step 17: Run all service tests**

  ```bash
  cd gateway && go test ./... -v 2>&1 | tail -5
  cd runner && python3 -m pytest tests/ -v 2>&1 | tail -5
  cd bridge && cargo test 2>&1 | tail -5
  cd notification && python3 -m pytest tests/ -v 2>&1 | tail -5
  ```
  Expected: all green.

- [ ] **Step 18: Commit**

  ```bash
  git add gateway/config.go gateway/config_test.go runner/config.py runner/tests/test_config.py inference/config.py notification/config.py bridge/src/config.rs platform_controller/main.py
  git commit -m "feat(config): per-service Redis credential env vars"
  ```

---

## Task 4: Gateway trust enforcement

Stamp each Task proto with `TRUSTED` or `UNTRUSTED` based on whether the JWT `sub` is in the `TRUSTED_USER_IDS` whitelist.

**Files:**
- Create: `gateway/auth/trust.go`
- Create: `gateway/auth/trust_test.go`
- Modify: `gateway/handler.go`
- Modify: `gateway/main.go`

- [ ] **Step 1: Write the failing test**

  Create `gateway/auth/trust_test.go`:
  ```go
  package auth

  import (
  	"testing"
  )

  func TestParseTrustedUsers_Empty(t *testing.T) {
  	s := ParseTrustedUsers("")
  	if len(s) != 0 {
  		t.Fatalf("expected empty set, got %v", s)
  	}
  }

  func TestParseTrustedUsers_Single(t *testing.T) {
  	s := ParseTrustedUsers("user@example.com")
  	if !s.Contains("user@example.com") {
  		t.Fatal("expected user@example.com to be trusted")
  	}
  }

  func TestParseTrustedUsers_Multiple(t *testing.T) {
  	s := ParseTrustedUsers("alice@example.com, bob@example.com ,charlie@example.com")
  	for _, id := range []string{"alice@example.com", "bob@example.com", "charlie@example.com"} {
  		if !s.Contains(id) {
  			t.Fatalf("expected %s to be trusted", id)
  		}
  	}
  }

  func TestParseTrustedUsers_WhitespaceTrimmed(t *testing.T) {
  	s := ParseTrustedUsers("  alice@example.com  ")
  	if !s.Contains("alice@example.com") {
  		t.Fatal("whitespace should be trimmed from user IDs")
  	}
  }

  func TestContains_Untrusted(t *testing.T) {
  	s := ParseTrustedUsers("alice@example.com")
  	if s.Contains("mallory@example.com") {
  		t.Fatal("mallory should not be trusted")
  	}
  }
  ```

- [ ] **Step 2: Run the test to verify it fails**

  ```bash
  cd gateway && go test ./auth/... -run "TestParseTrustedUsers|TestContains" -v
  ```
  Expected: `FAIL` — `ParseTrustedUsers` and `TrustedSet` not defined.

- [ ] **Step 3: Implement gateway/auth/trust.go**

  Create `gateway/auth/trust.go`:
  ```go
  package auth

  import (
  	"os"
  	"strings"
  )

  // TrustedSet is a set of user IDs that are permitted to execute tools in trusted mode.
  type TrustedSet map[string]struct{}

  // ParseTrustedUsers parses a comma-separated list of user IDs into a TrustedSet.
  // Whitespace around each entry is trimmed. Empty entries are ignored.
  func ParseTrustedUsers(raw string) TrustedSet {
  	s := make(TrustedSet)
  	for _, part := range strings.Split(raw, ",") {
  		if id := strings.TrimSpace(part); id != "" {
  			s[id] = struct{}{}
  		}
  	}
  	return s
  }

  // LoadTrustedUsers reads TRUSTED_USER_IDS from the environment and returns the parsed set.
  func LoadTrustedUsers() TrustedSet {
  	return ParseTrustedUsers(os.Getenv("TRUSTED_USER_IDS"))
  }

  // Contains reports whether userID is in the trusted set.
  func (t TrustedSet) Contains(userID string) bool {
  	_, ok := t[userID]
  	return ok
  }
  ```

- [ ] **Step 4: Run the test to verify it passes**

  ```bash
  cd gateway && go test ./auth/... -run "TestParseTrustedUsers|TestContains" -v
  ```
  Expected: `5 passed`

- [ ] **Step 5: Update gateway/handler.go to stamp ExecutionMode**

  Current `handler.go` has `taskRequest` with three fields and `Handler` with three fields. Make these changes:

  1. Add `ExecutionMode` to `taskRequest`
  2. Add `trustedUsers auth.TrustedSet` to `Handler`
  3. Update `NewHandler` to accept `trustedUsers`
  4. In `CreateTask`, resolve mode and set it on the Task proto

  Replace the full file with:
  ```go
  package main

  import (
  	"encoding/json"
  	"net/http"
  	"time"

  	"github.com/google/uuid"

  	"belgrade-os/gateway/auth"
  	belgrade "belgrade-os/gateway/gen"
  	"belgrade-os/gateway/redis"
  )

  type taskRequest struct {
  	Prompt        string `json:"prompt"`
  	AppID         string `json:"app_id"`
  	Stream        bool   `json:"stream"`
  	ExecutionMode string `json:"execution_mode"`
  }

  type taskResponse struct {
  	TaskID  string `json:"task_id"`
  	TraceID string `json:"trace_id"`
  }

  type Handler struct {
  	auth         *auth.JWKSCache
  	redis        *redis.RedisClient
  	audience     string
  	trustedUsers auth.TrustedSet
  }

  func NewHandler(jwks *auth.JWKSCache, redis *redis.RedisClient, audience string, trusted auth.TrustedSet) *Handler {
  	return &Handler{auth: jwks, redis: redis, audience: audience, trustedUsers: trusted}
  }

  func (h *Handler) CreateTask(w http.ResponseWriter, r *http.Request) {
  	tokenStr := r.Header.Get("Cf-Access-Jwt-Assertion")
  	if tokenStr == "" {
  		http.Error(w, "missing Cf-Access-Jwt-Assertion header", http.StatusUnauthorized)
  		return
  	}

  	claims, err := auth.ValidateToken(tokenStr, h.auth, h.audience)
  	if err != nil {
  		http.Error(w, "unauthorized", http.StatusUnauthorized)
  		return
  	}

  	r.Body = http.MaxBytesReader(w, r.Body, 64*1024)
  	var req taskRequest
  	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
  		http.Error(w, "invalid JSON", http.StatusBadRequest)
  		return
  	}

  	if req.Prompt == "" {
  		http.Error(w, "prompt is required", http.StatusBadRequest)
  		return
  	}

  	var execMode belgrade.ExecutionMode
  	if h.trustedUsers.Contains(claims.UserID) {
  		execMode = belgrade.ExecutionMode_TRUSTED
  	} else {
  		execMode = belgrade.ExecutionMode_UNTRUSTED
  	}

  	taskID := uuid.NewString()
  	traceID := uuid.NewString()

  	task := &belgrade.Task{
  		TaskId:        taskID,
  		UserId:        claims.UserID,
  		Prompt:        req.Prompt,
  		CreatedAtMs:   time.Now().UnixMilli(),
  		TraceId:       traceID,
  		ExecutionMode: execMode,
  	}

  	// Subscribe before publish so no ThoughtEvents are missed on the streaming path.
  	var evtCh <-chan *belgrade.ThoughtEvent
  	if req.Stream {
  		evtCh, err = h.redis.SubscribeSSE(r.Context(), taskID)
  		if err != nil {
  			http.Error(w, "failed to set up stream", http.StatusInternalServerError)
  			return
  		}
  	}

  	if err := h.redis.PublishTask(r.Context(), task); err != nil {
  		http.Error(w, "failed to queue task", http.StatusInternalServerError)
  		return
  	}

  	if req.Stream {
  		streamSSE(w, r, evtCh)
  		return
  	}

  	w.Header().Set("Content-Type", "application/json")
  	w.WriteHeader(http.StatusAccepted)
  	json.NewEncoder(w).Encode(taskResponse{TaskID: taskID, TraceID: traceID})
  }
  ```

- [ ] **Step 6: Update gateway/main.go to pass trusted users**

  Find the line:
  ```go
  h := NewHandler(cache, rClient, cfg.CFAudience)
  ```
  Replace with:
  ```go
  h := NewHandler(cache, rClient, cfg.CFAudience, auth.LoadTrustedUsers())
  ```

  The `auth` import is already present in `main.go`.

- [ ] **Step 7: Run gateway tests to verify they compile and pass**

  ```bash
  cd gateway && go test ./... -v 2>&1 | tail -20
  ```
  Expected: all existing tests pass. The handler tests call `NewHandler` — check if `handler_test.go` needs updating.

  Open `gateway/handler_test.go`. Any `NewHandler(...)` call there now needs a fourth argument. Look for the call and add `auth.TrustedSet{}` as the fourth argument. For example:
  ```go
  h := NewHandler(cache, rClient, "test-audience", auth.TrustedSet{})
  ```

  After fixing any `NewHandler` call in tests, run again:
  ```bash
  cd gateway && go test ./... -v 2>&1 | tail -10
  ```
  Expected: `ok`

- [ ] **Step 8: Add a test for trust routing in handler_test.go**

  In `gateway/handler_test.go`, add after the existing tests:
  ```go
  func TestCreateTask_TrustedUserGetsExecutionModeTrusted(t *testing.T) {
  	// This test verifies the execution mode is stamped correctly.
  	// It uses the existing test infrastructure without Redis.
  	trusted := auth.ParseTrustedUsers("alice@example.com")
  	h := NewHandler(nil, nil, "test-audience", trusted)
  	if h.trustedUsers.Contains("alice@example.com") != true {
  		t.Fatal("alice should be trusted")
  	}
  	if h.trustedUsers.Contains("mallory@example.com") != false {
  		t.Fatal("mallory should not be trusted")
  	}
  }
  ```

  ```bash
  cd gateway && go test ./... -run "TestCreateTask_Trusted" -v
  ```
  Expected: `PASS`

- [ ] **Step 9: Commit**

  ```bash
  git add gateway/auth/trust.go gateway/auth/trust_test.go gateway/handler.go gateway/main.go gateway/handler_test.go
  git commit -m "feat(gateway): trust enforcement — ExecutionMode stamp on Task"
  ```

---

## Task 5: Platform Controller ephemeral runner

Build the component that runs untrusted code in air-gapped Docker containers.

**Files:**
- Create: `platform_controller/ephemeral_runner.py`
- Create: `platform_controller/tests/test_ephemeral_runner.py`

- [ ] **Step 1: Write the failing tests**

  Create `platform_controller/tests/test_ephemeral_runner.py`:
  ```python
  import asyncio
  import json
  import os
  import shutil
  from pathlib import Path
  from unittest.mock import AsyncMock, MagicMock, patch
  import pytest

  from ephemeral_runner import EphemeralRunner, OutputValidationError


  SECCOMP = "/config/seccomp-untrusted.json"
  APPS_ROOT = Path("/apps")


  def make_runner(**kwargs):
      defaults = {"seccomp_profile": SECCOMP, "apps_root": APPS_ROOT}
      defaults.update(kwargs)
      return EphemeralRunner(**defaults)


  def _make_proc(returncode=0, stdout=b"", stderr=b""):
      proc = AsyncMock()
      proc.returncode = returncode
      proc.communicate = AsyncMock(return_value=(stdout, stderr))
      return proc


  @pytest.mark.asyncio
  async def test_run_success(tmp_path):
      runner = EphemeralRunner(seccomp_profile=SECCOMP, apps_root=tmp_path)
      output = {"result": "ok", "data": 42}

      async def fake_subprocess(*args, **kwargs):
          # Write output.json to the work dir that the runner creates
          work_dir = tmp_path.parent / f"beg-test-task"
          # Find the actual work dir from the mounted volume arg
          for arg in args:
              if str(arg).startswith("/tmp/beg-"):
                  work_dir = Path(str(arg).split(":/workspace")[0])
                  break
          (work_dir / "output.json").write_text(json.dumps(output))
          return _make_proc(returncode=0)

      with patch("asyncio.create_subprocess_exec", side_effect=fake_subprocess):
          result = await runner.run("test-task", "myapp", {"tool_name": "do_thing", "input_json": "{}"})

      assert result == output


  @pytest.mark.asyncio
  async def test_run_timeout(tmp_path):
      runner = EphemeralRunner(seccomp_profile=SECCOMP, apps_root=tmp_path)

      async def slow_subprocess(*args, **kwargs):
          proc = AsyncMock()
          async def hang():
              await asyncio.sleep(999)
              return b"", b""
          proc.communicate = hang
          return proc

      with patch("asyncio.create_subprocess_exec", side_effect=slow_subprocess):
          with pytest.raises(asyncio.TimeoutError):
              await runner.run("test-task", "myapp", {})


  @pytest.mark.asyncio
  async def test_container_nonzero_exit_raises(tmp_path):
      runner = EphemeralRunner(seccomp_profile=SECCOMP, apps_root=tmp_path)

      with patch("asyncio.create_subprocess_exec", return_value=_make_proc(returncode=1, stderr=b"crash")):
          with pytest.raises(RuntimeError, match="Container exited 1"):
              await runner.run("test-task", "myapp", {})


  @pytest.mark.asyncio
  async def test_output_missing_raises(tmp_path):
      runner = EphemeralRunner(seccomp_profile=SECCOMP, apps_root=tmp_path)

      with patch("asyncio.create_subprocess_exec", return_value=_make_proc(returncode=0)):
          with pytest.raises(OutputValidationError, match="output.json not created"):
              await runner.run("test-task", "myapp", {})


  @pytest.mark.asyncio
  async def test_output_too_large_raises(tmp_path):
      runner = EphemeralRunner(seccomp_profile=SECCOMP, apps_root=tmp_path)

      async def write_large(*args, **kwargs):
          for arg in args:
              if ":/workspace" in str(arg):
                  work_dir = Path(str(arg).split(":/workspace")[0])
                  (work_dir / "output.json").write_bytes(b"x" * 1_100_000)
                  break
          return _make_proc(returncode=0)

      with patch("asyncio.create_subprocess_exec", side_effect=write_large):
          with pytest.raises(OutputValidationError, match="too large"):
              await runner.run("test-task", "myapp", {})


  @pytest.mark.asyncio
  async def test_output_invalid_json_raises(tmp_path):
      runner = EphemeralRunner(seccomp_profile=SECCOMP, apps_root=tmp_path)

      async def write_bad_json(*args, **kwargs):
          for arg in args:
              if ":/workspace" in str(arg):
                  work_dir = Path(str(arg).split(":/workspace")[0])
                  (work_dir / "output.json").write_text("not json!!!")
                  break
          return _make_proc(returncode=0)

      with patch("asyncio.create_subprocess_exec", side_effect=write_bad_json):
          with pytest.raises(OutputValidationError, match="not valid JSON"):
              await runner.run("test-task", "myapp", {})


  @pytest.mark.asyncio
  async def test_work_dir_cleaned_up_on_success(tmp_path):
      runner = EphemeralRunner(seccomp_profile=SECCOMP, apps_root=tmp_path)
      captured_work_dir: list[Path] = []

      async def fake_subprocess(*args, **kwargs):
          for arg in args:
              if ":/workspace" in str(arg):
                  work_dir = Path(str(arg).split(":/workspace")[0])
                  captured_work_dir.append(work_dir)
                  (work_dir / "output.json").write_text('{"ok": true}')
                  break
          return _make_proc(returncode=0)

      with patch("asyncio.create_subprocess_exec", side_effect=fake_subprocess):
          await runner.run("cleanup-task", "myapp", {})

      assert len(captured_work_dir) == 1
      assert not captured_work_dir[0].exists(), "work dir should be removed after run"


  @pytest.mark.asyncio
  async def test_work_dir_cleaned_up_on_failure(tmp_path):
      runner = EphemeralRunner(seccomp_profile=SECCOMP, apps_root=tmp_path)
      captured_work_dir: list[Path] = []

      async def fail_subprocess(*args, **kwargs):
          for arg in args:
              if ":/workspace" in str(arg):
                  captured_work_dir.append(Path(str(arg).split(":/workspace")[0]))
                  break
          return _make_proc(returncode=1, stderr=b"crash")

      with patch("asyncio.create_subprocess_exec", side_effect=fail_subprocess):
          with pytest.raises(RuntimeError):
              await runner.run("cleanup-fail-task", "myapp", {})

      assert len(captured_work_dir) == 1
      assert not captured_work_dir[0].exists(), "work dir should be removed even on failure"
  ```

- [ ] **Step 2: Run tests to verify they fail**

  ```bash
  cd platform_controller && python3 -m pytest tests/test_ephemeral_runner.py -v
  ```
  Expected: `ERROR` — `ephemeral_runner` module not found.

- [ ] **Step 3: Implement platform_controller/ephemeral_runner.py**

  Create `platform_controller/ephemeral_runner.py`:
  ```python
  from __future__ import annotations

  import asyncio
  import json
  import logging
  import shutil
  from pathlib import Path

  logger = logging.getLogger(__name__)

  _MAX_OUTPUT_BYTES = 1_000_000  # 1 MB
  _TIMEOUT_S = 30.0


  class OutputValidationError(Exception):
      pass


  class EphemeralRunner:
      def __init__(self, seccomp_profile: str, apps_root: Path) -> None:
          self.seccomp_profile = seccomp_profile
          self.apps_root = Path(apps_root)

      async def run(self, task_id: str, app_id: str, input_data: dict) -> dict:
          work_dir = Path(f"/tmp/beg-{task_id}")
          work_dir.mkdir(parents=True, exist_ok=True)
          try:
              (work_dir / "input.json").write_text(json.dumps(input_data))
              await asyncio.wait_for(
                  self._run_container(task_id, app_id, work_dir),
                  timeout=_TIMEOUT_S,
              )
              return self._read_output(work_dir)
          finally:
              shutil.rmtree(work_dir, ignore_errors=True)

      async def _run_container(self, task_id: str, app_id: str, work_dir: Path) -> None:
          image = f"beg-os-{app_id}:latest"
          app_dir = self.apps_root / app_id
          proc = await asyncio.create_subprocess_exec(
              "docker", "run",
              "--rm",
              "--network", "none",
              "--security-opt", f"seccomp={self.seccomp_profile}",
              "--read-only",
              "--user", "1000:1000",
              "--tmpfs", "/tmp:size=32m,noexec,nosuid",
              "-v", f"{work_dir}:/workspace:rw",
              "-v", f"{app_dir}:/app:ro",
              image,
              "python3", "/app/main.py",
              stdout=asyncio.subprocess.PIPE,
              stderr=asyncio.subprocess.PIPE,
          )
          stdout, stderr = await proc.communicate()
          if proc.returncode != 0:
              raise RuntimeError(
                  f"Container exited {proc.returncode}: {stderr.decode(errors='replace')[:500]}"
              )
          logger.debug("container stdout task_id=%s: %s", task_id, stdout.decode(errors="replace")[:200])

      def _read_output(self, work_dir: Path) -> dict:
          output_file = work_dir / "output.json"
          if not output_file.exists():
              raise OutputValidationError("output.json not created by container")
          content = output_file.read_bytes()
          if len(content) > _MAX_OUTPUT_BYTES:
              raise OutputValidationError(f"output.json too large: {len(content)} bytes (max {_MAX_OUTPUT_BYTES})")
          try:
              data = json.loads(content)
          except json.JSONDecodeError as exc:
              raise OutputValidationError(f"output.json not valid JSON: {exc}") from exc
          if not isinstance(data, dict):
              raise OutputValidationError(f"output.json must be a JSON object, got {type(data).__name__}")
          return data
  ```

- [ ] **Step 4: Run tests to verify they pass**

  ```bash
  cd platform_controller && python3 -m pytest tests/test_ephemeral_runner.py -v
  ```

  Note: The `test_run_success` and size/cleanup tests depend on the mock writing to the work dir. The work dir path is constructed as `/tmp/beg-{task_id}`. The mock in those tests extracts the dir path from the volume mount arg `"-v", f"{work_dir}:/workspace:rw"`. If the mock pattern matching doesn't work, adjust the test's path extraction to match the actual arg format.

  Expected: `8 passed` (7 tests listed + timeout test). If some tests are flaky due to path extraction, adjust the string split to match `":/workspace"` exactly.

- [ ] **Step 5: Add pytest-asyncio to platform_controller requirements**

  Open `platform_controller/requirements-dev.txt` (or `requirements.txt` if no dev file). Add:
  ```
  pytest-asyncio>=0.23
  ```

  Then install:
  ```bash
  pip install pytest-asyncio
  ```

- [ ] **Step 6: Add pytest asyncio mode configuration**

  Check if `platform_controller/pytest.ini` exists. Add or create with:
  ```ini
  [pytest]
  asyncio_mode = auto
  ```

- [ ] **Step 7: Commit**

  ```bash
  git add platform_controller/ephemeral_runner.py platform_controller/tests/test_ephemeral_runner.py platform_controller/pytest.ini
  git commit -m "feat(controller): ephemeral runner — air-gapped Docker container execution"
  ```

---

## Task 6: Platform Controller stream consumer + hybrid routing

Wire the new `tasks:untrusted_calls` consumer into the Platform Controller so it can receive ToolCalls routed by the Inference Controller and execute them via `EphemeralRunner`.

**Files:**
- Modify: `platform_controller/main.py`
- Create: `platform_controller/tests/test_routing.py`

- [ ] **Step 1: Write the failing tests**

  Create `platform_controller/tests/test_routing.py`:
  ```python
  import asyncio
  import json
  from unittest.mock import AsyncMock, MagicMock, patch
  import pytest

  # We test the routing logic in isolation by importing the function we'll add.
  # The function reads from tasks:untrusted_calls and calls EphemeralRunner.run().
  from ephemeral_runner import EphemeralRunner, OutputValidationError


  def _make_tool_call_proto(task_id="t1", call_id="c1", tool_name="shopping:add_item",
                             input_json="{}", user_id="user1"):
      """Build a minimal ToolCall proto bytes for testing."""
      from gen import belgrade_os_pb2
      call = belgrade_os_pb2.ToolCall()
      call.call_id = call_id
      call.task_id = task_id
      call.tool_name = tool_name
      call.input_json = input_json
      call.user_id = user_id
      call.execution_mode = belgrade_os_pb2.ExecutionMode.Value("UNTRUSTED")
      return call.SerializeToString()


  @pytest.mark.asyncio
  async def test_process_untrusted_call_success(tmp_path):
      """EphemeralRunner.run() called with correct args; result published to tool_results."""
      from platform_controller import main as ctrl_main

      runner = AsyncMock(spec=EphemeralRunner)
      runner.run.return_value = {"output": "done"}

      redis_mock = AsyncMock()

      call_bytes = _make_tool_call_proto()
      await ctrl_main.process_untrusted_call(
          call_bytes, runner, redis_mock
      )

      runner.run.assert_called_once()
      args = runner.run.call_args[1] if runner.run.call_args[1] else runner.run.call_args[0]
      # Should have published a ToolResult to tasks:tool_results
      redis_mock.xadd.assert_called_once()


  @pytest.mark.asyncio
  async def test_process_untrusted_call_failure_publishes_error(tmp_path):
      """When EphemeralRunner raises, a failed ToolResult is published."""
      from platform_controller import main as ctrl_main

      runner = AsyncMock(spec=EphemeralRunner)
      runner.run.side_effect = RuntimeError("container crashed")

      redis_mock = AsyncMock()

      call_bytes = _make_tool_call_proto()
      await ctrl_main.process_untrusted_call(call_bytes, runner, redis_mock)

      redis_mock.xadd.assert_called_once()
      # Verify the published result has success=False
      call_args = redis_mock.xadd.call_args
      data = call_args[0][1] if call_args[0] else call_args[1]
      from gen import belgrade_os_pb2
      result = belgrade_os_pb2.ToolResult()
      result.ParseFromString(data[b"data"] if b"data" in data else list(data.values())[0])
      assert result.success is False
      assert "container crashed" in result.error


  @pytest.mark.asyncio
  async def test_process_untrusted_call_timeout_publishes_error(tmp_path):
      """Timeout from EphemeralRunner results in a failed ToolResult."""
      from platform_controller import main as ctrl_main

      runner = AsyncMock(spec=EphemeralRunner)
      runner.run.side_effect = asyncio.TimeoutError()

      redis_mock = AsyncMock()

      call_bytes = _make_tool_call_proto()
      await ctrl_main.process_untrusted_call(call_bytes, runner, redis_mock)

      redis_mock.xadd.assert_called_once()
      call_args = redis_mock.xadd.call_args
      data = call_args[0][1] if call_args[0] else call_args[1]
      from gen import belgrade_os_pb2
      result = belgrade_os_pb2.ToolResult()
      result.ParseFromString(list(data.values())[0])
      assert result.success is False
      assert "timeout" in result.error.lower()
  ```

- [ ] **Step 2: Run tests to verify they fail**

  ```bash
  cd platform_controller && python3 -m pytest tests/test_routing.py -v
  ```
  Expected: `ImportError` or `AttributeError` — `process_untrusted_call` not yet defined.

- [ ] **Step 3: Add imports and TRUSTED_USER_IDS env var to main.py**

  At the top of `platform_controller/main.py`, find the import block and add:
  ```python
  import time
  from pathlib import Path
  from ephemeral_runner import EphemeralRunner, OutputValidationError
  ```

  After the existing `CONTROLLER_TOKEN` line, add:
  ```python
  TRUSTED_USER_IDS = set(
      uid.strip() for uid in os.getenv("TRUSTED_USER_IDS", "").split(",") if uid.strip()
  )
  SECCOMP_PROFILE = os.getenv("SECCOMP_PROFILE", "/config/seccomp-untrusted.json")
  APPS_ROOT = Path(os.getenv("APPS_ROOT", str(Path(__file__).parent.parent / "apps")))

  _ephemeral_runner = EphemeralRunner(seccomp_profile=SECCOMP_PROFILE, apps_root=APPS_ROOT)
  ```

- [ ] **Step 4: Add process_untrusted_call function to main.py**

  Add this function to `platform_controller/main.py` (before the `app = FastAPI(...)` line):

  ```python
  async def process_untrusted_call(
      call_bytes: bytes,
      runner: EphemeralRunner,
      redis_client,
  ) -> None:
      from gen import belgrade_os_pb2
      call = belgrade_os_pb2.ToolCall()
      call.ParseFromString(call_bytes)

      start_ms = int(time.time() * 1000)
      result = belgrade_os_pb2.ToolResult()
      result.call_id = call.call_id
      result.task_id = call.task_id
      result.user_id = call.user_id
      result.tenant_id = call.tenant_id

      try:
          output = await runner.run(
              task_id=call.task_id,
              app_id=call.tool_name.split(":")[0] if ":" in call.tool_name else call.tool_name,
              input_data={"tool_name": call.tool_name, "input_json": call.input_json},
          )
          result.success = True
          result.output_json = json.dumps(output)
      except asyncio.TimeoutError:
          result.success = False
          result.error = f"execution timeout after {int(EphemeralRunner.__init__.__defaults__[0] if False else 30)}s"
          logger.error("ephemeral timeout task_id=%s call_id=%s tool=%s", call.task_id, call.call_id, call.tool_name)
      except (OutputValidationError, RuntimeError) as exc:
          result.success = False
          result.error = str(exc)
          logger.error("ephemeral error task_id=%s call_id=%s: %s", call.task_id, call.call_id, exc)

      result.duration_ms = int(time.time() * 1000) - start_ms
      await redis_client.xadd(
          "tasks:tool_results",
          {"data": result.SerializeToString(), "task_id": call.task_id},
      )
      logger.info(
          "untrusted call done task_id=%s call_id=%s tool=%s success=%s duration_ms=%d",
          call.task_id, call.call_id, call.tool_name, result.success, result.duration_ms,
      )
  ```

  The `result.error` line for `TimeoutError` is slightly awkward — simplify to:
  ```python
  result.error = "execution timed out after 30s"
  ```

- [ ] **Step 5: Add the untrusted consumer loop**

  Add this function to `platform_controller/main.py`:

  ```python
  async def _untrusted_consumer_loop(redis_url: str) -> None:
      import redis.asyncio as aioredis
      import redis.exceptions

      STREAM = "tasks:untrusted_calls"
      GROUP = "untrusted-runners"
      CONSUMER = "platform-controller"

      rdb = aioredis.from_url(redis_url, decode_responses=False)
      try:
          await rdb.xgroup_create(STREAM, GROUP, id="0", mkstream=True)
      except Exception:
          pass  # BUSYGROUP is expected on restart

      logger.info("untrusted consumer started stream=%s group=%s", STREAM, GROUP)
      while True:
          try:
              results = await rdb.xreadgroup(
                  groupname=GROUP,
                  consumername=CONSUMER,
                  streams={STREAM: ">"},
                  count=1,
                  block=2000,
              )
              if not results:
                  continue
              _stream, messages = results[0]
              for msg_id, fields in messages:
                  call_bytes = fields.get(b"data")
                  if call_bytes is None:
                      await rdb.xack(STREAM, GROUP, msg_id)
                      continue
                  try:
                      await process_untrusted_call(call_bytes, _ephemeral_runner, rdb)
                  except Exception:
                      logger.exception("unhandled error in untrusted consumer msg=%s", msg_id)
                  finally:
                      await rdb.xack(STREAM, GROUP, msg_id)
          except redis.exceptions.ConnectionError:
              logger.error("untrusted consumer lost Redis connection, retrying in 5s")
              await asyncio.sleep(5)
  ```

- [ ] **Step 6: Start the consumer on app startup**

  In the `startup_event` function, after the existing startup steps, add:

  ```python
  # 4. Start untrusted calls consumer
  asyncio.create_task(_untrusted_consumer_loop(REDIS_URL))
  ```

  The full updated `startup_event` end should look like:
  ```python
      async with SessionLocal() as session:
          result = await session.execute(text("SELECT id, user_id, tenant_id, cron, tool_name, params FROM shared.schedules"))
          for row in result.all():
              entry = ScheduleEntry(
                  id=row[0], user_id=row[1], tenant_id=row[2],
                  cron=row[3], tool_name=row[4], params=row[5]
              )
              await scheduler_manager.add_schedule(entry)

      # 4. Start untrusted calls consumer
      asyncio.create_task(_untrusted_consumer_loop(REDIS_URL))
  ```

- [ ] **Step 7: Run the routing tests**

  ```bash
  cd platform_controller && python3 -m pytest tests/test_routing.py -v
  ```

  If you see `ImportError: cannot import name 'process_untrusted_call' from 'platform_controller.main'`, the test import path is wrong. Update `test_routing.py` to import directly:
  ```python
  import sys
  sys.path.insert(0, str(Path(__file__).parent.parent))
  from main import process_untrusted_call
  ```

  Expected: `3 passed`

- [ ] **Step 8: Run all platform_controller tests**

  ```bash
  cd platform_controller && python3 -m pytest tests/ -v 2>&1 | tail -20
  ```
  Expected: all tests pass (existing + new).

- [ ] **Step 9: Run all service tests for final verification**

  ```bash
  cd gateway && go test ./... -v 2>&1 | grep -E "PASS|FAIL|ok"
  cd bridge && cargo test 2>&1 | grep -E "test result|FAILED"
  cd runner && python3 -m pytest tests/ -v 2>&1 | grep -E "passed|failed"
  cd platform_controller && python3 -m pytest tests/ -v 2>&1 | grep -E "passed|failed"
  ```
  Expected: all green.

- [ ] **Step 10: Commit**

  ```bash
  git add platform_controller/main.py platform_controller/tests/test_routing.py
  git commit -m "feat(controller): untrusted call consumer — hybrid routing via EphemeralRunner"
  ```

---

## Scope boundary

**This plan covers:**
- Proto `ExecutionMode` propagation from Gateway → Task
- Redis ACL isolation (per-service scoped credentials)
- Seccomp confinement profile + runner base image
- Gateway trust stamp (TRUSTED/UNTRUSTED per user whitelist)
- Platform Controller ephemeral container execution
- Platform Controller `tasks:untrusted_calls` consumer

**Not in this plan (follow-up):**
- Inference Controller changes to route ToolCalls to `tasks:untrusted_calls` based on `Task.execution_mode` — this requires changes to `inference/main.py` and the tool-call dispatch logic
- Per-app Docker images (`apps/shopping/Dockerfile`, etc.)
- App-side file-based I/O protocol (apps currently use HTTP, not input/output files)
