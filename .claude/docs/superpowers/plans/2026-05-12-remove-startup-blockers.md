# Remove Startup Blockers Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `make dev && make start` bring the full Belgrade OS stack online from a clean checkout, with no manual steps and no undocumented networking assumptions.

**Architecture:** Four independent fixes: (1) docker-compose starts all infra services and exposes the host to cloudflared; (2) `.env` generation produces every URL services need; (3) a `start.sh` / `stop.sh` pair starts/stops all application services in dependency order with PID tracking; (4) `AppSupervisor` gains a watchdog loop that auto-restarts crashed apps.

**Tech Stack:** bash, docker-compose v3.9, Go (gateway binary), Rust (bridge binary), Python 3.12 (all other services), pytest, asyncio.

---

## File Map

| Action | Path | Purpose |
|--------|------|---------|
| Modify | `Makefile` | Add `db docker-socket-proxy` to `dev` target; add `start` / `stop` targets |
| Modify | `docker-compose.yml` | Add `extra_hosts` to `tunnel` service so cloudflared can reach host gateway |
| Modify | `setup.sh` | Add `BEG_OS_BRIDGE_URL`, `BRIDGE_URL`, `BEG_OS_DB_URL` to `generate_env()` |
| Modify | `.env.example` | Document every var the system needs |
| Create | `start.sh` | Starts all application services in dependency order; writes PIDs to `run/` |
| Create | `stop.sh` | Stops all services tracked in `run/` |
| Modify | `platform_controller/main.py` | Add `_watch_loop` to `AppSupervisor`; wire into `startup_event` |
| Modify | `platform_controller/tests/test_app_process.py` | Tests for watchdog behaviour |

---

## Task 1: Fix `make dev` and cloudflared host networking

**Files:**
- Modify: `Makefile`
- Modify: `docker-compose.yml`

`make dev` currently starts only `redis` and `tunnel`. `platform_controller` connects to Postgres on startup and will fail if `db` isn't running. `docker-socket-proxy` is required by `platform_controller` for the untrusted container runner. The `tunnel` (cloudflared) service is on an isolated Docker network and cannot reach the gateway process running on the host — adding `extra_hosts` maps `host.docker.internal` to the host's Docker gateway so the Cloudflare tunnel ingress URL `http://host.docker.internal:8080` resolves correctly.

- [ ] **Step 1: Update `make dev` target in Makefile**

  Find:
  ```makefile
  dev:
  	docker-compose up -d redis tunnel
  ```

  Replace with:
  ```makefile
  dev:
  	docker-compose up -d redis db docker-socket-proxy tunnel
  ```

- [ ] **Step 2: Add `extra_hosts` to the `tunnel` service in docker-compose.yml**

  Find the `tunnel:` service block. Add `extra_hosts` so `host.docker.internal` resolves to the Docker host gateway (required on Linux — macOS Docker Desktop provides this automatically):

  ```yaml
    tunnel:
      image: cloudflare/cloudflared:latest
      container_name: beg-os-tunnel
      restart: unless-stopped
      user: "65532:65532"
      read_only: true
      tmpfs:
        - /tmp:size=16m,noexec,nosuid
      cap_drop: [ALL]
      security_opt: ["no-new-privileges:true"]
      mem_limit: 128m
      cpus: 0.25
      pids_limit: 64
      extra_hosts:
        - "host.docker.internal:host-gateway"
      environment:
        - TUNNEL_TOKEN=${CF_TUNNEL_TOKEN}
      command: tunnel --no-autoupdate run
      networks:
        - edge_net
      healthcheck:
        test: ["CMD", "cloudflared", "tunnel", "ready"]
        interval: 30s
        timeout: 10s
        retries: 3
  ```

  > **Operator note:** In the Cloudflare Zero Trust dashboard, set the tunnel ingress rule for `beg-os.fyi` to `http://host.docker.internal:8080`. This lets cloudflared forward traffic to the gateway running on the host.

- [ ] **Step 3: Verify docker-compose config is valid**

  ```bash
  cd /path/to/belgrade-os && docker-compose config --quiet
  ```

  Expected: exits 0, no output (no parse errors).

- [ ] **Step 4: Verify all four infra services start**

  ```bash
  make dev
  docker ps --format "table {{.Names}}\t{{.Status}}" | grep beg-os
  ```

  Expected: `beg-os-redis`, `beg-os-db`, `beg-os-docker-proxy`, `beg-os-tunnel` all show `Up`.

- [ ] **Step 5: Commit**

  ```bash
  git add Makefile docker-compose.yml
  git commit -m "fix(infra): make dev starts db + docker-proxy; tunnel gets extra_hosts for host gateway access"
  ```

---

## Task 2: Fix `.env` generation and `.env.example`

**Files:**
- Modify: `setup.sh`
- Modify: `.env.example`

`setup.sh generate_env()` creates the initial `.env` but is missing `BEG_OS_BRIDGE_URL` (used by apps, inference, runner, and platform_controller to locate the bridge), `BRIDGE_URL` (used by the gateway's appproxy handler), and `BEG_OS_DB_URL` (passed by platform_controller to each app process). Without these, services rely on compiled-in `localhost` defaults which silently break if anything moves. `.env.example` is also out of date — it shows only 5 vars, while the system now needs ~15.

- [ ] **Step 1: Add missing service URLs to `generate_env()` in setup.sh**

  Find the `generate_env()` function. The `cat > .env` heredoc currently ends with `CONTROLLER_API_TOKEN=...`. Extend it to include the new vars:

  ```bash
  generate_env() {
      if [ -f ".env" ]; then
          echo "✅ .env already exists — skipping generation."
          return
      fi

      if ! command -v openssl &> /dev/null; then
          echo "⚠️  openssl not found — cannot generate random secrets. Edit .env manually."
          cp .env.example .env 2>/dev/null || true
          return
      fi

      echo "Generating .env with random secrets..."
      cat > .env << EOF
  # Belgrade OS — auto-generated by setup.sh
  # Fill in CF_TUNNEL_TOKEN from the Cloudflare dashboard before starting.
  CF_TUNNEL_TOKEN=

  DB_USER=laurent
  DB_PASSWORD=$(openssl rand -hex 32)
  REDIS_PASSWORD=$(openssl rand -hex 32)
  CONTROLLER_API_TOKEN=$(openssl rand -hex 32)

  # Service discovery — override when services run in containers (use container names)
  BEG_OS_BRIDGE_URL=http://localhost:8081
  BRIDGE_URL=http://localhost:8081
  BEG_OS_DB_URL=postgresql+asyncpg://laurent:\${DB_PASSWORD}@localhost:5432/belgrade_os
  EOF
      chmod 600 .env
      echo "✅ .env created. Fill in CF_TUNNEL_TOKEN then run: make dev && make start"
  }
  ```

  **Note:** The `BEG_OS_DB_URL` line references `${DB_PASSWORD}` using a `\${}` escape so the heredoc expands `DB_PASSWORD` (already set in the same heredoc block) rather than deferring expansion — but since `DB_PASSWORD` is set on the line above in the same shell, this works. Verify manually after the step below.

- [ ] **Step 2: Verify `generate_env()` produces the correct vars**

  ```bash
  # Temporarily move .env out of the way to test generation
  mv .env .env.bak 2>/dev/null || true
  bash -c 'source setup.sh; generate_env'
  grep -E "BEG_OS_BRIDGE_URL|BRIDGE_URL|BEG_OS_DB_URL" .env
  mv .env.bak .env 2>/dev/null || true
  ```

  Expected output (values will differ):
  ```
  BEG_OS_BRIDGE_URL=http://localhost:8081
  BRIDGE_URL=http://localhost:8081
  BEG_OS_DB_URL=postgresql+asyncpg://laurent:...@localhost:5432/belgrade_os
  ```

- [ ] **Step 3: Update `.env.example` to document all vars**

  Replace the entire contents of `.env.example`:

  ```bash
  # Belgrade OS environment variables
  # Copy to .env and fill in values before running.
  # Run ./setup.sh to auto-generate secrets and Redis ACLs.

  # ── Cloudflare ─────────────────────────────────────────────────────────────
  CF_TUNNEL_TOKEN=            # from Cloudflare Zero Trust dashboard

  # ── Database ───────────────────────────────────────────────────────────────
  DB_USER=laurent
  DB_PASSWORD=changeme
  DATABASE_URL=postgresql+asyncpg://laurent:changeme@localhost:5432/belgrade_os

  # ── Redis (base password — per-service URLs generated by setup.sh) ────────
  REDIS_PASSWORD=changeme

  # ── Service discovery ──────────────────────────────────────────────────────
  # Keep as localhost:* for bare-process dev. Change to container names when containerised.
  BEG_OS_BRIDGE_URL=http://localhost:8081   # used by apps, inference, runner, platform_controller
  BRIDGE_URL=http://localhost:8081          # used by gateway appproxy
  BEG_OS_DB_URL=postgresql+asyncpg://laurent:changeme@localhost:5432/belgrade_os

  # ── Gateway ────────────────────────────────────────────────────────────────
  PORT=8080
  CF_TEAM_DOMAIN=             # e.g. yourteam.cloudflareaccess.com (omit in dev)
  CF_AUDIENCE=                # JWT audience tag (omit in dev)
  TRUSTED_USER_IDS=           # comma-separated user emails that get TRUSTED execution
  GATEWAY_URL=http://localhost:8080
  CONTROLLER_API_TOKEN=changeme

  # ── Per-service Redis URLs (generated by setup.sh generate_acl) ────────────
  # GATEWAY_REDIS_URL=redis://gateway:...@localhost:6379
  # INFERENCE_REDIS_URL=redis://inference:...@localhost:6379
  # RUNNER_REDIS_URL=redis://runner:...@localhost:6379
  # NOTIFICATION_REDIS_URL=redis://notification:...@localhost:6379
  # VAULT_REDIS_URL=redis://vault:...@localhost:6379
  # BRIDGE_REDIS_URL=redis://bridge:...@localhost:6379
  # CONTROLLER_REDIS_URL=redis://controller:...@localhost:6379
  # APP_REDIS_URL=redis://app:...@localhost:6379
  ```

- [ ] **Step 4: Commit**

  ```bash
  git add setup.sh .env.example
  git commit -m "fix(setup): generate BEG_OS_BRIDGE_URL + BRIDGE_URL + BEG_OS_DB_URL; update .env.example"
  ```

---

## Task 3: Add `start.sh` and `stop.sh`

**Files:**
- Create: `start.sh`
- Create: `stop.sh`
- Modify: `Makefile`
- Modify: `setup.sh` (final message only)

Services must start in dependency order: bridge and stateless workers first, platform_controller last (it starts app subprocesses). Each service gets its own log file in `logs/` and its PID written to `run/`. All services inherit the environment from `.env`.

Start order and rationale:
1. **bridge** — tool registry; inference, runner, and apps all connect to it on startup
2. **vault_service** — independent Redis consumer; no deps beyond Redis
3. **notification** — independent Redis consumer; no deps beyond Redis
4. **inference** — connects to bridge on startup to fetch tool list
5. **runner** — connects to bridge on startup to fetch tool list
6. **gateway** — connects to Redis and bridge (for appproxy); must be up before tunnel forwards traffic
7. **platform_controller** — last, because it starts app subprocesses which register with bridge

- [ ] **Step 1: Create `start.sh`**

  ```bash
  #!/usr/bin/env bash
  # Starts all Belgrade OS application services in dependency order.
  # Requires: make dev already running (redis, db, docker-socket-proxy, tunnel).
  # Usage: ./start.sh [--no-wait]

  set -euo pipefail

  ROOT="$(cd "$(dirname "$0")" && pwd)"
  cd "$ROOT"

  # Load .env
  if [ ! -f .env ]; then
      echo "❌ .env not found — run ./setup.sh first"
      exit 1
  fi
  set -a; source .env; set +a

  mkdir -p run logs

  # --- helpers ----------------------------------------------------------------

  pid_file() { echo "run/$1.pid"; }

  is_running() {
      local pf; pf="$(pid_file "$1")"
      [ -f "$pf" ] && kill -0 "$(cat "$pf")" 2>/dev/null
  }

  start_service() {
      local name=$1; shift
      if is_running "$name"; then
          echo "⚠️  $name already running (PID $(cat "$(pid_file "$name")"))"
          return
      fi
      echo -n "  Starting $name ... "
      "$@" >> "logs/$name.log" 2>&1 &
      echo $! > "$(pid_file "$name")"
      echo "PID $!"
  }

  wait_tcp() {
      local name=$1 host=$2 port=$3 retries=${4:-20}
      echo -n "  Waiting for $name on $host:$port "
      for i in $(seq 1 "$retries"); do
          if nc -z "$host" "$port" 2>/dev/null; then
              echo " ready"
              return
          fi
          echo -n "."
          sleep 0.5
      done
      echo " TIMEOUT — $name may not have started correctly"
  }

  # ---------------------------------------------------------------------------

  echo "🇷🇸 Belgrade OS — starting services"
  echo ""

  # 1. Bridge (Rust binary)
  echo "[1/7] Bridge"
  start_service bridge ./bridge/target/release/bridge
  wait_tcp bridge localhost 8081

  # 2. Vault service
  echo "[2/7] Vault service"
  start_service vault_service ./venv/bin/python3 vault_service/main.py

  # 3. Notification service
  echo "[3/7] Notification service"
  start_service notification ./venv/bin/python3 notification/main.py

  # 4. Inference worker
  echo "[4/7] Inference worker"
  start_service inference ./venv/bin/python3 inference/main.py

  # 5. Runner
  echo "[5/7] Runner"
  start_service runner ./venv/bin/python3 runner/main.py

  # 6. Gateway (Go binary)
  echo "[6/7] Gateway"
  start_service gateway ./gateway/gateway
  wait_tcp gateway localhost "${PORT:-8080}"

  # 7. Platform controller — last, because it starts app subprocesses
  echo "[7/7] Platform controller"
  start_service platform_controller ./venv/bin/python3 platform_controller/main.py
  wait_tcp platform_controller localhost 8000

  echo ""
  echo "✅ All services started. Logs in logs/  PIDs in run/"
  echo "   Stop with: make stop"
  ```

- [ ] **Step 2: Create `stop.sh`**

  ```bash
  #!/usr/bin/env bash
  # Stops all Belgrade OS application services tracked in run/*.pid

  set -euo pipefail

  ROOT="$(cd "$(dirname "$0")" && pwd)"
  cd "$ROOT"

  if [ ! -d run ] || [ -z "$(ls run/*.pid 2>/dev/null)" ]; then
      echo "No running services found in run/"
      exit 0
  fi

  echo "🇷🇸 Belgrade OS — stopping services"

  # Stop in reverse start order (platform_controller first so apps get SIGTERM before bridge dies)
  for name in platform_controller gateway runner inference notification vault_service bridge; do
      pf="run/$name.pid"
      if [ -f "$pf" ]; then
          pid=$(cat "$pf")
          if kill -0 "$pid" 2>/dev/null; then
              echo "  Stopping $name (PID $pid)..."
              kill "$pid"
          else
              echo "  $name (PID $pid) already dead"
          fi
          rm -f "$pf"
      fi
  done

  echo "✅ Done"
  ```

- [ ] **Step 3: Make both scripts executable**

  ```bash
  chmod +x start.sh stop.sh
  ```

- [ ] **Step 4: Add `start` and `stop` targets to Makefile**

  Find:
  ```makefile
  dev:
  	docker-compose up -d redis db docker-socket-proxy tunnel
  ```

  Add after it:
  ```makefile
  start: dev
  	./start.sh

  stop:
  	./stop.sh
  	docker-compose down
  ```

- [ ] **Step 5: Update the final message in setup.sh**

  Find the final `echo` block at the bottom of `setup.sh`:
  ```bash
  echo "1. Activate venv:   source venv/bin/activate"
  echo "2. Start infra:     make dev"
  echo "3. Seed perms:      python3 scripts/seed_permissions.py"
  echo "4. Start OS:        cd platform_controller && python3 main.py"
  ```

  Replace with:
  ```bash
  echo "1. Fill in CF_TUNNEL_TOKEN in .env"
  echo "2. Seed permissions: python3 scripts/seed_permissions.py"
  echo "3. Start everything: make start"
  echo "4. Stop everything:  make stop"
  ```

- [ ] **Step 6: Smoke-test `start.sh` locally**

  With infra already running (`make dev`), run:
  ```bash
  ./start.sh
  ```

  Expected: each service prints `PID <n>`, gateway and bridge pass the `wait_tcp` checks, platform_controller comes up last. Then:
  ```bash
  cat run/gateway.pid | xargs ps -p
  curl -s http://localhost:8080/ | head -5     # gateway responds (even 401 is fine)
  curl -s http://localhost:8081/v1/tools       # bridge responds with tool list
  curl -s http://localhost:8000/apps           # platform_controller responds
  ```

  Then:
  ```bash
  ./stop.sh
  ```

  Expected: all PIDs gone from `run/`, services no longer respond.

- [ ] **Step 7: Add `run/` to `.gitignore`**

  ```bash
  grep -q "^run/$" .gitignore || echo "run/" >> .gitignore
  ```

- [ ] **Step 8: Commit**

  ```bash
  git add start.sh stop.sh Makefile setup.sh .gitignore
  git commit -m "feat(ops): start.sh / stop.sh — ordered service startup with PID tracking; make start / make stop"
  ```

---

## Task 4: App process watchdog in `AppSupervisor`

**Files:**
- Modify: `platform_controller/main.py`
- Modify: `platform_controller/tests/test_app_process.py`

`AppSupervisor.start_app()` launches app subprocesses but never checks if they're still alive. A crashed app stays dead silently. The fix is a background `asyncio` task that polls each process every 10 seconds and calls `start_app()` again if the process has exited.

- [ ] **Step 1: Write failing tests**

  Add to `platform_controller/tests/test_app_process.py`:

  ```python
  import asyncio
  from unittest.mock import patch, MagicMock, AsyncMock
  from pathlib import Path
  from main import AppSupervisor


  def _make_supervisor(tmp_path: Path) -> AppSupervisor:
      sup = AppSupervisor(apps_root=tmp_path)
      # Create a minimal app so discover_and_start finds it
      app_dir = tmp_path / "myapp"
      app_dir.mkdir()
      (app_dir / "main.py").write_text("# stub")
      return sup


  def _mock_process(returncode=None):
      """returncode=None means still running; integer means exited."""
      mock = MagicMock()
      mock.pid = 9999
      mock.poll.return_value = returncode
      mock.returncode = returncode
      return mock


  def test_watchdog_restarts_dead_app(tmp_path):
      """If an app process exits, the watchdog must restart it within one tick."""
      sup = _make_supervisor(tmp_path)

      live_proc = _mock_process(returncode=None)   # starts alive
      dead_proc = _mock_process(returncode=1)      # will die

      start_call_count = 0

      async def run():
          nonlocal start_call_count

          # Patch start_app to track calls and inject our mock process
          original_start = sup.start_app

          async def mock_start(app_id):
              nonlocal start_call_count
              start_call_count += 1
              # First call: inject a process that will "die"
              proc = live_proc if start_call_count == 1 else _mock_process(returncode=None)
              with patch("main.subprocess.Popen", return_value=proc), \
                   patch("main.open", MagicMock()):
                  await original_start(app_id)
              if start_call_count == 1:
                  # After first start, make the process appear dead for watchdog check
                  sup.running_apps["myapp"].process = dead_proc

          sup.start_app = mock_start

          with patch("main.subprocess.Popen", return_value=live_proc), \
               patch("main.open", MagicMock()):
              await sup.discover_and_start()

          assert start_call_count == 1

          # Run one watchdog tick
          await sup._watch_tick()

          assert start_call_count == 2, "watchdog must restart dead app"

      asyncio.run(run())


  def test_watchdog_does_not_restart_live_app(tmp_path):
      """Running apps must not be restarted by the watchdog."""
      sup = _make_supervisor(tmp_path)
      live_proc = _mock_process(returncode=None)

      start_call_count = 0

      async def run():
          nonlocal start_call_count

          original_start = sup.start_app

          async def mock_start(app_id):
              nonlocal start_call_count
              start_call_count += 1
              with patch("main.subprocess.Popen", return_value=live_proc), \
                   patch("main.open", MagicMock()):
                  await original_start(app_id)

          sup.start_app = mock_start

          with patch("main.subprocess.Popen", return_value=live_proc), \
               patch("main.open", MagicMock()):
              await sup.discover_and_start()

          await sup._watch_tick()

          assert start_call_count == 1, "live app must not be restarted"

      asyncio.run(run())
  ```

- [ ] **Step 2: Run tests to confirm they fail**

  ```bash
  cd platform_controller && python -m pytest tests/test_app_process.py::test_watchdog_restarts_dead_app tests/test_app_process.py::test_watchdog_does_not_restart_live_app -v 2>&1 | tail -15
  ```

  Expected: `AttributeError: 'AppSupervisor' object has no attribute '_watch_tick'`

- [ ] **Step 3: Add `_watch_tick` and `watch` to `AppSupervisor` in `platform_controller/main.py`**

  In the `AppSupervisor` class, after `stop_app`:

  ```python
  async def _watch_tick(self) -> None:
      """Check all running apps; restart any that have exited."""
      dead = [
          app_id
          for app_id, proc in self.running_apps.items()
          if proc.process is not None and proc.process.poll() is not None
      ]
      for app_id in dead:
          logger.warning("app %s exited (rc=%s) — restarting", app_id,
                         self.running_apps[app_id].process.returncode)
          await self.start_app(app_id)

  async def watch(self, interval: int = 10) -> None:
      """Background loop: call _watch_tick every `interval` seconds."""
      while True:
          await asyncio.sleep(interval)
          await self._watch_tick()
  ```

- [ ] **Step 4: Wire `watch` into `startup_event` in `platform_controller/main.py`**

  In `startup_event`, after the line `asyncio.create_task(_untrusted_consumer_loop(REDIS_URL))`, add:

  ```python
  asyncio.create_task(app_supervisor.watch())
  ```

- [ ] **Step 5: Run tests to confirm they pass**

  ```bash
  cd platform_controller && python -m pytest tests/test_app_process.py -v
  ```

  Expected: all tests pass including the 2 new watchdog tests and the 6 existing ones.

- [ ] **Step 6: Commit**

  ```bash
  git add platform_controller/main.py platform_controller/tests/test_app_process.py
  git commit -m "feat(platform_controller): app watchdog — _watch_tick restarts crashed apps every 10s"
  ```

---

## Self-Review

### Spec coverage

| Blocker | Covered by |
|---|---|
| `make dev` doesn't start `db` or `docker-socket-proxy` | Task 1 |
| cloudflared can't reach host gateway | Task 1 (`extra_hosts`) + operator note for dashboard |
| `BEG_OS_BRIDGE_URL` missing from `.env` generation | Task 2 |
| `BRIDGE_URL` (gateway) missing from `.env` generation | Task 2 |
| `.env.example` out of date | Task 2 |
| No documented/enforced service start order | Task 3 |
| App crashes stay dead silently | Task 4 |

### Placeholder scan

No TBD, TODO, or "similar to" patterns. All code blocks are complete.

### Type / name consistency

- `_watch_tick` defined in Task 4 Step 3, called from test in Task 4 Step 1, wired in Task 4 Step 4 — consistent.
- `watch(interval=10)` started via `asyncio.create_task` — correct pattern, same as existing `_untrusted_consumer_loop`.
- `proc.process.poll()` — `AppProcess.process` is a `subprocess.Popen`; `.poll()` returns `None` if alive, int if exited — correct.
- `start.sh` uses `./venv/bin/python3` — consistent with the `venv` created by `setup.sh`.
- `wait_tcp` uses `nc -z` — standard on Linux; macOS has it too. No flags vary between platforms.
