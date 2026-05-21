# Security Track A Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix two critical code bugs, harden the gateway UI handler against three path-security issues, add authentication to the platform controller admin API, restructure docker-compose into a 3-tier hardened network, and add host-level UFW rules and a thermal watchdog service.

**Architecture:** Tasks 1–4 are code fixes with tests; Tasks 5–7 are infrastructure with manual validation. Each task is independent — they can be reviewed and merged separately. The natural checkpoint is after Task 4 (all service code fixed) before moving to infra.

**Tech Stack:** Python 3.11, Go 1.22, Docker Compose v3.9, bash, systemd, iptables/UFW

---

## File Map

```
sdk/belgrade_sdk/app.py              — fix: initialize _db_engine/_redis_pool, pass to AppContext
sdk/tests/__init__.py                — create: make sdk/tests a package
sdk/tests/test_app.py                — create: verify AppContext receives engine/pool not URL strings

platform_controller/main.py          — fix: add `import json`; add CONTROLLER_API_TOKEN auth + app_id validation
platform_controller/tests/test_app_process.py  — modify: add _load_manifest unit tests
platform_controller/tests/test_auth.py         — create: test token auth dependency

gateway/ui/handler.go                — fix: absRoot at construction, filepath.Rel containment, os.Stat dir check
gateway/ui/handler_test.go           — modify: add containment and directory-listing tests

docker-compose.yml                   — replace: 3-tier networks, container hardening, Redis auth, docker-socket-proxy
setup.sh                             — modify: add .env generation with random passwords, UFW DOCKER-USER rules
watchdog.py                          — create: thermal polling, staged shutdown
watchdog.service                     — create: systemd unit
```

---

## Task 1: Fix SDK AppContext constructor mismatch (Critical)

**Files:**
- Modify: `sdk/belgrade_sdk/app.py`
- Create: `sdk/tests/__init__.py`
- Create: `sdk/tests/test_app.py`

**Context:** `BelgradeApp._setup_routes` builds `AppContext` with `db_url=` and `redis_url=` kwargs. `AppContext.__init__` expects `db_engine: Optional[AsyncEngine]` and `redis_pool: Optional[Redis]`. Python raises `TypeError` on every tool call, which is caught silently and returned as `success=False`. The `on_shutdown` handler references `self._db_engine` and `self._redis_pool` which are never assigned on `BelgradeApp`, raising `AttributeError` on every clean shutdown.

- [ ] **Step 1: Write the failing test**

Create `sdk/tests/__init__.py` (empty).

Create `sdk/tests/test_app.py`:

```python
from __future__ import annotations
import json
import pytest
from unittest.mock import MagicMock
from belgrade_sdk.app import BelgradeApp
from belgrade_sdk.context import AppContext


def _make_app_with_pools() -> BelgradeApp:
    """Return a BelgradeApp with mock engine and redis pool already set (simulating post-startup state)."""
    bapp = BelgradeApp("test_app")
    bapp._db_engine = MagicMock(name="mock_engine")
    bapp._redis_pool = MagicMock(name="mock_pool")
    return bapp


def test_execute_passes_engine_and_pool_to_appcontext():
    """AppContext constructed inside /execute must receive db_engine and redis_pool, not URL strings."""
    from fastapi.testclient import TestClient

    bapp = _make_app_with_pools()
    captured: list[AppContext] = []

    @bapp.tool("ping", "Ping tool")
    async def ping(ctx: AppContext, msg: str) -> dict:
        captured.append(ctx)
        return {"pong": msg}

    client = TestClient(bapp.app, raise_server_exceptions=True)
    resp = client.post("/execute", json={
        "tool_name": "test_app:ping",
        "input_json": json.dumps({"msg": "hello"}),
        "user_id": "u1",
        "tenant_id": "t1",
        "trace_id": "tr1",
    })

    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is True, f"Tool failed: {data.get('error')}"
    assert len(captured) == 1

    ctx = captured[0]
    assert ctx._db_engine is bapp._db_engine
    assert ctx._redis_pool is bapp._redis_pool
    assert not hasattr(ctx, "db_url"), "db_url must not be an attribute on AppContext"
    assert not hasattr(ctx, "redis_url"), "redis_url must not be an attribute on AppContext"


def test_events_passes_engine_and_pool_to_appcontext():
    """AppContext constructed inside /events must receive db_engine and redis_pool."""
    from fastapi.testclient import TestClient

    bapp = _make_app_with_pools()
    captured: list[AppContext] = []

    @bapp.on_event("test.topic")
    async def handle(ctx: AppContext, payload: dict) -> None:
        captured.append(ctx)

    client = TestClient(bapp.app, raise_server_exceptions=True)
    resp = client.post("/events", json={
        "topic": "test.topic",
        "payload": {"x": 1},
        "tenant_id": "t1",
        "trace_id": "tr2",
    })

    assert resp.status_code == 200
    assert len(captured) == 1
    ctx = captured[0]
    assert ctx._db_engine is bapp._db_engine
    assert ctx._redis_pool is bapp._redis_pool


def test_shutdown_safe_when_pools_are_none():
    """on_shutdown must not raise when _db_engine and _redis_pool are None (never initialized)."""
    import asyncio
    bapp = BelgradeApp("test_app")
    # _db_engine and _redis_pool must exist as attributes (even if None)
    assert hasattr(bapp, "_db_engine"), "_db_engine must be defined in __init__"
    assert hasattr(bapp, "_redis_pool"), "_redis_pool must be defined in __init__"

    async def simulate_shutdown():
        if bapp._db_engine:
            await bapp._db_engine.dispose()
        if bapp._redis_pool:
            await bapp._redis_pool.aclose()

    asyncio.run(simulate_shutdown())  # must not raise
```

- [ ] **Step 2: Run to confirm failure**

```bash
cd /Users/ivanvladisavljevic/Projects/belgrade-os/sdk && pip install -e . httpx[testclient] pytest pytest-asyncio 2>/dev/null && python3 -m pytest tests/test_app.py -v 2>&1 | head -30
```

Expected: `AttributeError: 'BelgradeApp' object has no attribute '_db_engine'` or `TypeError: unexpected keyword argument 'db_url'`.

- [ ] **Step 3: Fix sdk/belgrade_sdk/app.py**

Replace the entire file:

```python
from __future__ import annotations
import os
import json
import logging
import inspect
from typing import Any, Callable, Dict, List, Optional
import httpx
import uvicorn
from fastapi import FastAPI, Request, HTTPException
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from redis.asyncio import Redis as AioRedis

from .models import RegisterRequest, ToolDefinition, ExecuteRequest, ExecuteResponse, EventPayload
from .context import AppContext
from . import defaults

logger = logging.getLogger(__name__)


class BelgradeApp:
    def __init__(self, app_id: str):
        self.app_id = app_id
        self.tools: Dict[str, Callable] = {}
        self.tool_definitions: List[ToolDefinition] = []
        self.event_handlers: Dict[str, List[Callable]] = {}

        self.bridge_url = defaults.BRIDGE_URL
        self.db_url = defaults.DB_URL
        self.callback_url = defaults.CALLBACK_URL
        self.redis_url = defaults.REDIS_URL
        self.notification_driver = os.getenv("BEG_OS_NOTIFICATION_DRIVER", defaults.DEFAULT_NOTIFICATION_DRIVER)

        # Initialized during on_startup — None until then.
        self._db_engine: Optional[AsyncEngine] = None
        self._redis_pool: Optional[AioRedis] = None

        self.app = FastAPI(title=f"Belgrade App: {app_id}")
        self._setup_routes()

    def tool(self, name: str, description: str):
        """Decorator to register a tool."""
        def decorator(func: Callable):
            sig = inspect.signature(func)
            schema = {"type": "object", "properties": {}}
            for param_name, param in sig.parameters.items():
                if param_name == "ctx":
                    continue
                schema["properties"][param_name] = {"type": "string"}

            full_name = f"{self.app_id}:{name}"
            self.tools[full_name] = func
            self.tool_definitions.append(ToolDefinition(
                name=full_name,
                description=description,
                input_schema_json=json.dumps(schema),
            ))
            return func
        return decorator

    def on_event(self, topic: str):
        """Decorator to register an event handler."""
        def decorator(func: Callable):
            self.event_handlers.setdefault(topic, []).append(func)
            return func
        return decorator

    def _build_context(
        self,
        user_id: Optional[str],
        tenant_id: Optional[str],
        trace_id: str,
    ) -> AppContext:
        return AppContext(
            app_id=self.app_id,
            user_id=user_id,
            tenant_id=tenant_id,
            trace_id=trace_id,
            bridge_url=self.bridge_url,
            db_engine=self._db_engine,
            redis_pool=self._redis_pool,
            notification_driver=self.notification_driver,
        )

    def _setup_routes(self):
        @self.app.post("/execute")
        async def execute(req: ExecuteRequest) -> ExecuteResponse:
            if req.tool_name not in self.tools:
                return ExecuteResponse(success=False, error=f"Tool {req.tool_name} not found")

            ctx = self._build_context(
                user_id=req.user_id if hasattr(req, "user_id") else None,
                tenant_id=req.tenant_id,
                trace_id=req.trace_id,
            )
            try:
                func = self.tools[req.tool_name]
                kwargs = json.loads(req.input_json)
                result = await func(ctx, **kwargs)
                return ExecuteResponse(success=True, output_json=json.dumps(result))
            except Exception as e:
                logger.exception("Tool execution failed")
                return ExecuteResponse(success=False, error=str(e))
            finally:
                await ctx.cleanup()

        @self.app.post("/events")
        async def handle_event(event: EventPayload):
            if event.topic in self.event_handlers:
                ctx = self._build_context(
                    user_id=None,
                    tenant_id=event.tenant_id,
                    trace_id=event.trace_id,
                )
                try:
                    for handler in self.event_handlers[event.topic]:
                        if inspect.iscoroutinefunction(handler):
                            await handler(ctx, event.payload)
                        else:
                            handler(ctx, event.payload)
                except Exception:
                    logger.exception(f"Event handler failed for topic {event.topic}")
                finally:
                    await ctx.cleanup()
            return {"status": "ok"}

        @self.app.get("/health")
        async def health():
            return {
                "status": "ok",
                "app_id": self.app_id,
                "tools": list(self.tools.keys()),
                "subscriptions": list(self.event_handlers.keys()),
            }

    async def register_with_bridge(self):
        """Notifies the Capability Bridge about available tools and subscriptions."""
        req = RegisterRequest(
            app_id=self.app_id,
            callback_url=self.callback_url,
            tools=self.tool_definitions,
            subscriptions=list(self.event_handlers.keys()),
        )
        async with httpx.AsyncClient() as client:
            try:
                resp = await client.post(f"{self.bridge_url}/v1/register", json=req.model_dump())
                resp.raise_for_status()
                logger.info(f"Registered {len(self.tools)} tools with bridge")
            except Exception as e:
                logger.error(f"Failed to register with bridge: {e}")

    def run(self, host: str = "0.0.0.0", port: int = 9000):
        """Starts the app server and registers tools."""
        @self.app.on_event("startup")
        async def on_startup():
            if self.db_url:
                self._db_engine = create_async_engine(self.db_url)
            self._redis_pool = AioRedis.from_url(self.redis_url, decode_responses=False)
            await self.register_with_bridge()

        @self.app.on_event("shutdown")
        async def on_shutdown():
            if self._db_engine:
                await self._db_engine.dispose()
            if self._redis_pool:
                await self._redis_pool.aclose()

        uvicorn.run(self.app, host=host, port=port)
```

- [ ] **Step 4: Run tests**

```bash
cd /Users/ivanvladisavljevic/Projects/belgrade-os/sdk && python3 -m pytest tests/test_app.py -v 2>&1
```

Expected: 3 tests pass.

- [ ] **Step 5: Commit**

```bash
git add sdk/belgrade_sdk/app.py sdk/tests/__init__.py sdk/tests/test_app.py
git commit -m "fix(sdk): initialize _db_engine/_redis_pool in BelgradeApp, pass engine/pool to AppContext"
```

---

## Task 2: Fix `json` import in platform_controller (Critical)

**Files:**
- Modify: `platform_controller/main.py` (line 1–14 imports block)
- Modify: `platform_controller/tests/test_app_process.py`

**Context:** `_load_manifest()` calls `json.load(f)` but `json` is not in the import list. Python raises `NameError: name 'json' is not defined` which is caught by `except Exception: logger.warning(...)` and swallowed. The method always returns `{}`, so manifest-based notification driver injection has never worked.

- [ ] **Step 1: Write the failing test**

Add to `platform_controller/tests/test_app_process.py`:

```python
def test_load_manifest_reads_json_file(tmp_path):
    """_load_manifest must parse JSON from manifest.json — requires json to be imported."""
    import json as _json
    manifest_data = {"notifications": {"driver": "firebase"}, "version": "1.0"}
    (tmp_path / "manifest.json").write_text(_json.dumps(manifest_data))

    app = AppProcess(app_id="shopping", path=tmp_path, port=9001)
    result = app._load_manifest()

    assert result == manifest_data
    assert result["notifications"]["driver"] == "firebase"


def test_load_manifest_returns_empty_dict_when_absent(tmp_path):
    """_load_manifest returns {} when manifest.json does not exist."""
    app = AppProcess(app_id="shopping", path=tmp_path, port=9001)
    result = app._load_manifest()
    assert result == {}


def test_load_manifest_returns_empty_dict_on_invalid_json(tmp_path):
    """_load_manifest swallows parse errors and returns {}."""
    (tmp_path / "manifest.json").write_text("not valid json {{{")
    app = AppProcess(app_id="shopping", path=tmp_path, port=9001)
    result = app._load_manifest()
    assert result == {}
```

- [ ] **Step 2: Run to confirm failure**

```bash
cd /Users/ivanvladisavljevic/Projects/belgrade-os/platform_controller && python3 -m pytest tests/test_app_process.py::test_load_manifest_reads_json_file -v 2>&1 | tail -15
```

Expected: `AssertionError` — result is `{}` instead of the manifest dict (because `json.load` raises `NameError` and is silently swallowed).

- [ ] **Step 3: Add `import json` to platform_controller/main.py**

The imports block currently starts at line 1. Add `import json` after `import asyncio`:

```python
import asyncio
import json
import os
import signal
import subprocess
import logging
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy import text, Column, String, JSON, DateTime
from sqlalchemy.orm import declarative_base

from scheduler import SchedulerManager, ScheduleEntry, PermissionSyncManager
```

- [ ] **Step 4: Run all platform_controller tests**

```bash
cd /Users/ivanvladisavljevic/Projects/belgrade-os/platform_controller && python3 -m pytest tests/ -v 2>&1
```

Expected: all tests pass including the 3 new manifest tests.

- [ ] **Step 5: Commit**

```bash
git add platform_controller/main.py platform_controller/tests/test_app_process.py
git commit -m "fix(platform_controller): add missing json import — manifest injection now works"
```

---

## Task 3: Harden gateway UI handler

**Files:**
- Modify: `gateway/ui/handler.go`
- Modify: `gateway/ui/handler_test.go`

**Context:** Three security issues in `handler.go`:
1. `absRoot, _ := filepath.Abs(h.AppsRoot)` — error silently discarded; if `Getwd()` fails, `absRoot = ""` and `HasPrefix` passes for any path.
2. `strings.HasPrefix(absPath, absRoot)` — passes for sibling dirs (e.g. `absRoot="/srv/apps"` allows `/srv/apps-evil/...`).
3. `http.ServeFile` renders directory listings when the resolved path is a directory.

All three are fixed in this task.

- [ ] **Step 1: Write failing tests**

Add to `gateway/ui/handler_test.go`:

```go
func TestPathContainmentUsesRelNotPrefix(t *testing.T) {
	// Create two sibling directories: appsRoot and appsRoot-evil
	parent := t.TempDir()
	appsRoot := filepath.Join(parent, "apps")
	appsEvil := filepath.Join(parent, "apps-evil")
	os.MkdirAll(filepath.Join(appsRoot, "shopping/static/web"), 0755)
	os.MkdirAll(filepath.Join(appsEvil), 0755)
	os.WriteFile(filepath.Join(appsEvil, "secret.txt"), []byte("secret"), 0644)

	h := NewHandler(appsRoot, nil, "http://gateway")

	// Verify that strings.HasPrefix would have allowed this path (demonstrating the old bug)
	evilPath := filepath.Join(appsEvil, "secret.txt")
	if !strings.HasPrefix(evilPath, appsRoot) {
		t.Skip("sibling dir doesn't share prefix on this OS — test not applicable")
	}
	// Now verify our handler rejects it. We can't directly request appsEvil via HTTP
	// because the path components go through appID/bundleID/subPath — but we can
	// verify the absRoot field is set correctly at construction and that Rel detects escape.
	rel, err := filepath.Rel(h.absRoot, evilPath)
	if err != nil {
		t.Fatalf("filepath.Rel failed: %v", err)
	}
	if !strings.HasPrefix(rel, "..") {
		t.Errorf("filepath.Rel(%q, %q) = %q — expected to start with '..'", h.absRoot, evilPath, rel)
	}
}

func TestDirectoryRequestReturns404(t *testing.T) {
	tmpDir := t.TempDir()
	// Create a directory (no index.html) inside the app static dir
	os.MkdirAll(filepath.Join(tmpDir, "shopping/static/web/assets"), 0755)

	rClient, err := redis.NewRedisClient("redis://localhost:6379")
	if err != nil {
		t.Skip("Redis unavailable")
	}
	ctx := context.Background()
	rClient.RDB.HSet(ctx, "perms:user1", "shopping:web", "admin")
	defer rClient.RDB.Del(ctx, "perms:user1")

	h := NewHandler(tmpDir, rClient, "http://gateway")

	// Request a path that resolves to a directory (assets/ with no trailing file)
	req := httptest.NewRequest("GET", "/ui/shopping/web/assets", nil)
	req = req.WithContext(context.WithValue(req.Context(), auth.ClaimsKey, &auth.Claims{UserID: "user1"}))
	w := httptest.NewRecorder()
	h.ServeAsset(w, req)

	if w.Code != http.StatusNotFound {
		t.Errorf("directory request: expected 404, got %d", w.Code)
	}
}

func TestHandlerAbsRootComputedAtConstruction(t *testing.T) {
	tmpDir := t.TempDir()
	h := NewHandler(tmpDir, nil, "http://gateway")
	if h.absRoot == "" {
		t.Error("absRoot must not be empty after construction")
	}
	expected, _ := filepath.Abs(tmpDir)
	if h.absRoot != expected {
		t.Errorf("absRoot = %q, want %q", h.absRoot, expected)
	}
}
```

- [ ] **Step 2: Run to confirm failure**

```bash
cd /Users/ivanvladisavljevic/Projects/belgrade-os/gateway && go test ./ui/... -run "TestPathContainment|TestDirectoryRequest|TestHandlerAbsRoot" -v 2>&1
```

Expected: compile error (`h.absRoot undefined`) confirming the field doesn't exist yet.

- [ ] **Step 3: Replace gateway/ui/handler.go**

```go
package ui

import (
	"fmt"
	"net/http"
	"os"
	"path/filepath"
	"strings"

	"belgrade-os/gateway/auth"
	"belgrade-os/gateway/redis"
)

type Handler struct {
	AppsRoot   string
	absRoot    string // resolved once at construction; never changes
	Redis      *redis.RedisClient
	GatewayURL string
}

func NewHandler(appsRoot string, redis *redis.RedisClient, gatewayURL string) *Handler {
	abs, err := filepath.Abs(appsRoot)
	if err != nil {
		// If the working directory is unresolvable, the process is in an
		// undefined state. Panic is preferable to silently serving any file.
		panic(fmt.Sprintf("ui: cannot resolve appsRoot %q: %v", appsRoot, err))
	}
	return &Handler{
		AppsRoot:   appsRoot,
		absRoot:    abs,
		Redis:      redis,
		GatewayURL: gatewayURL,
	}
}

func (h *Handler) ServeAsset(w http.ResponseWriter, r *http.Request) {
	claims, ok := r.Context().Value(auth.ClaimsKey).(*auth.Claims)
	if !ok {
		http.Error(w, "unauthorized", http.StatusUnauthorized)
		return
	}

	path := strings.TrimPrefix(r.URL.Path, "/ui/")
	if path == "" {
		http.NotFound(w, r)
		return
	}
	parts := strings.Split(path, "/")

	appID := parts[0]
	var bundleID string
	var subPath string

	if len(parts) < 2 || parts[1] == "" {
		bundleID = "web"
		subPath = "index.html"
	} else {
		bundleID = parts[1]
		subPath = strings.Join(parts[2:], "/")
		if subPath == "" {
			subPath = "index.html"
		}
	}

	if strings.Contains(appID, "..") || strings.Contains(bundleID, "..") || strings.Contains(subPath, "..") {
		http.Error(w, "invalid path", http.StatusBadRequest)
		return
	}

	role, err := h.Redis.GetPermission(r.Context(), claims.UserID, appID, bundleID)
	if err != nil {
		http.Error(w, "forbidden", http.StatusForbidden)
		return
	}

	filePath := filepath.Join(h.AppsRoot, appID, "static", bundleID, subPath)

	absPath, err := filepath.Abs(filePath)
	if err != nil {
		http.Error(w, "internal error", http.StatusInternalServerError)
		return
	}

	// Use filepath.Rel instead of strings.HasPrefix to prevent sibling-directory bypass.
	rel, err := filepath.Rel(h.absRoot, absPath)
	if err != nil || strings.HasPrefix(rel, "..") {
		http.Error(w, "forbidden", http.StatusForbidden)
		return
	}

	// Reject directories — http.ServeFile would render a listing.
	info, err := os.Stat(filePath)
	if err != nil || info.IsDir() {
		http.NotFound(w, r)
		return
	}

	if strings.HasSuffix(subPath, ".html") {
		h.serveHTMLWithConfig(w, r, filePath, appID, bundleID, claims.UserID, role)
		return
	}

	http.ServeFile(w, r, filePath)
}

func (h *Handler) serveHTMLWithConfig(w http.ResponseWriter, r *http.Request, filePath, appID, bundleID, userID, role string) {
	content, err := os.ReadFile(filePath)
	if err != nil {
		http.Error(w, "not found", http.StatusNotFound)
		return
	}

	configScript := fmt.Sprintf(`
<script id="belgrade-config">
  window.BELGRADE_CONFIG = {
    "gateway_url": %q,
    "app_id": %q,
    "bundle_id": %q,
    "user_id": %q,
    "role": %q
  };
</script>
`, h.GatewayURL, appID, bundleID, userID, role)

	html := string(content)
	if strings.Contains(html, "</head>") {
		html = strings.Replace(html, "</head>", configScript+"</head>", 1)
	} else if strings.Contains(html, "<body>") {
		html = strings.Replace(html, "<body>", "<body>"+configScript, 1)
	} else {
		html = configScript + html
	}

	w.Header().Set("Content-Type", "text/html")
	fmt.Fprint(w, html)
}
```

- [ ] **Step 4: Run all gateway UI tests**

```bash
cd /Users/ivanvladisavljevic/Projects/belgrade-os/gateway && go test ./ui/... -v 2>&1
```

Expected: all tests pass (Redis-dependent tests skip if Redis is unavailable).

- [ ] **Step 5: Run full gateway tests**

```bash
cd /Users/ivanvladisavljevic/Projects/belgrade-os/gateway && go test ./... -v 2>&1 | tail -20
```

Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add gateway/ui/handler.go gateway/ui/handler_test.go
git commit -m "fix(gateway/ui): resolve absRoot at construction, use filepath.Rel for containment, block directory listings"
```

---

## Task 4: Platform controller admin API authentication

**Files:**
- Modify: `platform_controller/main.py`
- Create: `platform_controller/tests/test_auth.py`

**Context:** `POST /apps/reload` (and all admin endpoints) bind to `0.0.0.0:8000` with no authentication. Any process on `core_net` (a compromised bridge or inference container) can call this endpoint. `app_id` is passed directly to `apps_root / app_id` without validation — a path like `../other_dir` could be used to start arbitrary Python files on the host. Fix: add `CONTROLLER_API_TOKEN` Bearer token check as a FastAPI dependency, and validate `app_id` against a strict regex before use.

- [ ] **Step 1: Write failing tests**

Create `platform_controller/tests/test_auth.py`:

```python
from __future__ import annotations
import os
import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch, AsyncMock, MagicMock


def _get_client(token: str = "test-secret-token"):
    """Import app fresh with a test token set in env."""
    with patch.dict(os.environ, {"CONTROLLER_API_TOKEN": token}):
        # Re-import to pick up the env var at module level
        import importlib
        import main as m
        importlib.reload(m)
        return TestClient(m.app), m


def test_reload_without_token_returns_401():
    client, _ = _get_client("test-secret")
    resp = client.post("/apps/reload", json={"app_id": "shopping"})
    assert resp.status_code in (401, 403), f"Expected 401/403, got {resp.status_code}"


def test_reload_with_wrong_token_returns_403():
    client, _ = _get_client("test-secret")
    resp = client.post(
        "/apps/reload",
        json={"app_id": "shopping"},
        headers={"Authorization": "Bearer wrong-token"},
    )
    assert resp.status_code == 403


def test_reload_with_correct_token_is_accepted():
    client, m = _get_client("test-secret")
    with patch.object(m.app_supervisor, "start_app", new_callable=AsyncMock):
        resp = client.post(
            "/apps/reload",
            json={"app_id": "shopping"},
            headers={"Authorization": "Bearer test-secret"},
        )
    # 200 means auth passed (start_app is mocked so no process is actually started)
    assert resp.status_code == 200


def test_reload_rejects_path_traversal_app_id():
    client, m = _get_client("test-secret")
    resp = client.post(
        "/apps/reload",
        json={"app_id": "../etc/something"},
        headers={"Authorization": "Bearer test-secret"},
    )
    assert resp.status_code == 400


def test_reload_rejects_empty_app_id():
    client, m = _get_client("test-secret")
    resp = client.post(
        "/apps/reload",
        json={"app_id": ""},
        headers={"Authorization": "Bearer test-secret"},
    )
    assert resp.status_code == 400


def test_reload_accepts_valid_app_id():
    client, m = _get_client("test-secret")
    with patch.object(m.app_supervisor, "start_app", new_callable=AsyncMock):
        resp = client.post(
            "/apps/reload",
            json={"app_id": "my-app_01"},
            headers={"Authorization": "Bearer test-secret"},
        )
    assert resp.status_code == 200
```

- [ ] **Step 2: Run to confirm failure**

```bash
cd /Users/ivanvladisavljevic/Projects/belgrade-os/platform_controller && python3 -m pytest tests/test_auth.py::test_reload_without_token_returns_401 -v 2>&1 | tail -10
```

Expected: `AssertionError` — endpoint returns 200 (no auth check currently).

- [ ] **Step 3: Add auth + app_id validation to platform_controller/main.py**

Add these imports after the existing imports block:

```python
import re
import secrets
from fastapi import Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
```

Add these module-level definitions after the `REDIS_URL` line (before `class AppProcess`):

```python
CONTROLLER_TOKEN = os.getenv("CONTROLLER_API_TOKEN", "")
_bearer = HTTPBearer(auto_error=False)
_APP_ID_RE = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")


def _require_token(
    creds: Optional[HTTPAuthorizationCredentials] = Depends(_bearer),
) -> None:
    if not CONTROLLER_TOKEN:
        raise HTTPException(status_code=500, detail="CONTROLLER_API_TOKEN not set")
    if creds is None or not secrets.compare_digest(creds.credentials, CONTROLLER_TOKEN):
        raise HTTPException(status_code=403, detail="forbidden")
```

Replace the `reload_app` endpoint:

```python
@app.post("/apps/reload")
async def reload_app(action: AppAction, _: None = Depends(_require_token)):
    if not _APP_ID_RE.match(action.app_id):
        raise HTTPException(status_code=400, detail="invalid app_id: must match ^[a-zA-Z0-9_-]{1,64}$")
    await app_supervisor.start_app(action.app_id)
    return {"status": "reloaded", "app_id": action.app_id}
```

- [ ] **Step 4: Run auth tests**

```bash
cd /Users/ivanvladisavljevic/Projects/belgrade-os/platform_controller && python3 -m pytest tests/test_auth.py -v 2>&1
```

Expected: 6 tests pass.

- [ ] **Step 5: Run full platform_controller tests**

```bash
cd /Users/ivanvladisavljevic/Projects/belgrade-os/platform_controller && python3 -m pytest tests/ -v 2>&1
```

Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add platform_controller/main.py platform_controller/tests/test_auth.py
git commit -m "fix(platform_controller): add CONTROLLER_API_TOKEN bearer auth + app_id regex validation on /apps/reload"
```

---

## Task 5: Docker 3-tier network topology + container hardening

**Files:**
- Replace: `docker-compose.yml`

**Context:** Current compose uses a flat `beg-os-network` (bridge), exposes `redis:6379` and `db:5432` directly to the host, runs all containers as root with no resource limits or capability restrictions, and has no Redis password. Replace with a 3-tier network (`edge_net` DMZ / `core_net` internal / `compute_net` internal), add `docker-socket-proxy` for container management, harden all containers.

Note: Application services (gateway, bridge, inference, notification, vault_service, platform_controller) require Dockerfiles which are not yet written. This task hardens the infrastructure services and wires up the network topology. Application service Dockerfiles are a follow-up task.

- [ ] **Step 1: Add REDIS_PASSWORD and CONTROLLER_API_TOKEN to .env.example**

Create `.env.example` if it doesn't exist. Verify `docker-compose.yml` will read these:

```bash
cat /Users/ivanvladisavljevic/Projects/belgrade-os/.env.example 2>/dev/null || echo "# Belgrade OS environment variables
CF_TUNNEL_TOKEN=
DB_PASSWORD=changeme
REDIS_PASSWORD=changeme
CONTROLLER_API_TOKEN=changeme
DB_USER=laurent
" > /Users/ivanvladisavljevic/Projects/belgrade-os/.env.example
```

- [ ] **Step 2: Validate current docker-compose parses**

```bash
cd /Users/ivanvladisavljevic/Projects/belgrade-os && docker compose config --quiet 2>&1 | head -5
```

Expected: exits 0 (or shows current config without errors).

- [ ] **Step 3: Replace docker-compose.yml**

```yaml
# Belgrade OS — Hardened 3-Tier Compose
# Networks:
#   edge_net    — DMZ (internet-facing): cloudflared only
#   core_net    — internal: services that need Redis/DB/Docker proxy
#   compute_net — internal: AI inference workers
#
# Application services (gateway, bridge, etc.) require Dockerfiles
# and will be added as separate service definitions once built.

version: "3.9"

networks:
  edge_net:
    name: beg-os-edge
    driver: bridge
  core_net:
    name: beg-os-core
    driver: bridge
    internal: true
  compute_net:
    name: beg-os-compute
    driver: bridge
    internal: true

services:

  # ── Edge tier ─────────────────────────────────────────────────────────────

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

  # ── Core tier (internal) ──────────────────────────────────────────────────

  redis:
    image: redis:7-alpine
    container_name: beg-os-redis
    restart: always
    user: "999:999"
    read_only: true
    tmpfs:
      - /tmp:size=32m,noexec,nosuid
    cap_drop: [ALL]
    security_opt: ["no-new-privileges:true"]
    mem_limit: 256m
    cpus: 0.5
    pids_limit: 64
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
    networks:
      - core_net
      - compute_net

  db:
    image: postgres:15-alpine
    container_name: beg-os-db
    restart: always
    user: "70:70"
    read_only: true
    tmpfs:
      - /tmp:size=32m,noexec,nosuid
      - /var/run/postgresql:size=16m,noexec,nosuid
    cap_drop: [ALL]
    security_opt: ["no-new-privileges:true"]
    mem_limit: 512m
    cpus: 0.5
    pids_limit: 128
    environment:
      POSTGRES_USER: ${DB_USER:-laurent}
      POSTGRES_PASSWORD: ${DB_PASSWORD}
      POSTGRES_DB: belgrade_os
    volumes:
      - ./data/postgres:/var/lib/postgresql/data
    networks:
      - core_net

  # Tecnativa docker-socket-proxy: platform_controller uses this to manage
  # app containers instead of direct docker.sock access.
  # EXEC=0 prevents code execution in existing containers.
  # NETWORKS=0, VOLUMES=0 prevent creating network backdoors or deleting data volumes.
  docker-socket-proxy:
    image: tecnativa/docker-socket-proxy:latest
    container_name: beg-os-docker-proxy
    restart: always
    read_only: true
    cap_drop: [ALL]
    security_opt: ["no-new-privileges:true"]
    mem_limit: 64m
    cpus: 0.1
    pids_limit: 32
    environment:
      CONTAINERS: 1
      POST: 1
      DELETE: 1
      IMAGES: 0
      NETWORKS: 0
      VOLUMES: 0
      EXEC: 0
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock:ro
    networks:
      - core_net
```

- [ ] **Step 4: Validate the new compose file parses correctly**

```bash
cd /Users/ivanvladisavljevic/Projects/belgrade-os && REDIS_PASSWORD=test DB_PASSWORD=test CF_TUNNEL_TOKEN=test docker compose config --quiet 2>&1
```

Expected: exits 0, no errors.

- [ ] **Step 5: Verify Redis starts with auth**

```bash
cd /Users/ivanvladisavljevic/Projects/belgrade-os && REDIS_PASSWORD=testpass DB_PASSWORD=testpgpass CF_TUNNEL_TOKEN=skip docker compose up redis -d 2>&1 && sleep 3
# Test that unauthenticated access is rejected:
docker exec beg-os-redis redis-cli ping 2>&1
# Test that authenticated access works:
docker exec beg-os-redis redis-cli -a testpass ping 2>&1
docker compose down 2>&1 | tail -3
```

Expected: unauthenticated `ping` returns `NOAUTH Authentication required`, authenticated `ping` returns `PONG`.

- [ ] **Step 6: Commit**

```bash
git add docker-compose.yml .env.example
git commit -m "feat(infra): 3-tier network topology, container hardening, Redis auth, docker-socket-proxy"
```

---

## Task 6: setup.sh — .env generation and UFW DOCKER-USER hardening

**Files:**
- Modify: `setup.sh`

**Context:** Docker bypasses UFW by default — `docker run -p 6379:6379` opens port 6379 externally even if UFW denies it. The fix is to insert rules into the `DOCKER-USER` iptables chain (which Docker always consults before its own rules) to block traffic from external interfaces. Also: the setup script doesn't generate random secrets for `.env`.

- [ ] **Step 1: Verify current setup.sh doesn't generate .env**

```bash
grep -n "REDIS_PASSWORD\|openssl\|rand\|DOCKER-USER\|iptables" /Users/ivanvladisavljevic/Projects/belgrade-os/setup.sh
```

Expected: no matches (these features are absent).

- [ ] **Step 2: Add .env generation function**

In `setup.sh`, after the existing `check_binary` and `install_system_deps` functions (before the `echo "--- Checking System Prerequisites ---"` line), add:

```bash
# --- .env Generation ---
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
EOF
    chmod 600 .env
    echo "✅ .env created. Fill in CF_TUNNEL_TOKEN and then run: docker compose up -d"
}
```

- [ ] **Step 3: Call generate_env from the main flow**

Find the section near the end of `setup.sh` where services are started or `.env` is mentioned. Add a call to `generate_env` before any `docker compose` invocation:

```bash
# --- Generate .env ---
generate_env
```

- [ ] **Step 4: Add UFW DOCKER-USER hardening function (Linux only)**

After the `generate_env` function, add:

```bash
# --- UFW + DOCKER-USER hardening (Linux only) ---
harden_firewall() {
    if [ "${PACKAGE_MANAGER}" != "apt" ]; then
        echo "ℹ️  Firewall hardening is Linux-only. Skipping on macOS."
        return
    fi

    if ! command -v ufw &> /dev/null; then
        echo "⚠️  ufw not found — install it with: sudo apt install ufw"
        return
    fi

    echo "--- Hardening firewall ---"

    # Enable UFW with default-deny incoming
    sudo ufw --force enable
    sudo ufw default deny incoming
    sudo ufw default allow outgoing
    # Allow SSH so we don't lock ourselves out
    sudo ufw allow ssh

    # Docker bypasses UFW by writing iptables rules directly.
    # The DOCKER-USER chain is evaluated before Docker's own rules,
    # so rules here apply to all Docker-managed traffic.
    AFTER_RULES="/etc/ufw/after.rules"
    MARKER="# Belgrade OS DOCKER-USER rules"

    if grep -q "${MARKER}" "${AFTER_RULES}" 2>/dev/null; then
        echo "✅ DOCKER-USER rules already in ${AFTER_RULES}"
    else
        echo "Adding DOCKER-USER rules to ${AFTER_RULES}..."
        sudo tee -a "${AFTER_RULES}" > /dev/null << 'IPTABLES_EOF'

# Belgrade OS DOCKER-USER rules
# Block all external traffic from reaching Docker bridge networks directly.
# Only RELATED/ESTABLISHED connections (responses to outbound requests) are allowed.
*filter
:DOCKER-USER - [0:0]
-A DOCKER-USER -m state --state RELATED,ESTABLISHED -j ACCEPT
-A DOCKER-USER -i lo -j ACCEPT
-A DOCKER-USER -j DROP
COMMIT
IPTABLES_EOF
        sudo ufw reload
        echo "✅ DOCKER-USER rules applied and UFW reloaded."
    fi
}
```

- [ ] **Step 5: Call harden_firewall from the main flow**

Add after the `generate_env` call:

```bash
# --- Harden firewall ---
harden_firewall
```

- [ ] **Step 6: Test the .env generation on macOS (no-op firewall section)**

```bash
cd /Users/ivanvladisavljevic/Projects/belgrade-os
mv .env .env.bak 2>/dev/null || true
bash setup.sh 2>&1 | grep -E "\.env|Generating|DOCKER-USER|firewall|macOS"
# Verify .env was created with random values
grep "DB_PASSWORD\|REDIS_PASSWORD\|CONTROLLER_API_TOKEN" .env | head -3
# Restore original .env
mv .env.bak .env 2>/dev/null || true
```

Expected: `.env created` message, three lines with 64-character hex values, macOS firewall skip message.

- [ ] **Step 7: Commit**

```bash
git add setup.sh
git commit -m "feat(setup): generate random secrets in .env, add UFW DOCKER-USER firewall hardening"
```

---

## Task 7: Hardware watchdog service

**Files:**
- Create: `watchdog.py`
- Create: `watchdog.service`

**Context:** The Lenovo i5 running inference, bridge, and multiple app processes is a thermal risk. This standalone Python script polls `/sys/class/thermal/thermal_zone*/temp` (Linux thermal zone files, values in millidegrees Celsius) every 5 seconds and implements staged shutdown: warning at 80°C, kill heavy containers at 85°C, emergency `compose down` at 90°C. Runs as a systemd service outside Docker so container failures cannot disable it.

- [ ] **Step 1: Write the test**

Create `tests/test_watchdog.py` at the repo root:

```python
from __future__ import annotations
import sys
import types
import pytest
from unittest.mock import patch, MagicMock, call

# Import watchdog without executing main()
import importlib.util, pathlib
spec = importlib.util.spec_from_file_location(
    "watchdog", pathlib.Path(__file__).parent.parent / "watchdog.py"
)
watchdog = importlib.util.load_from_spec(spec)
spec.loader.exec_module(watchdog)


def test_read_temps_returns_list_of_ints(tmp_path):
    zone = tmp_path / "thermal_zone0"
    zone.mkdir()
    (zone / "temp").write_text("55000\n")

    with patch("glob.glob", return_value=[str(zone / "temp")]):
        temps = watchdog.read_temps()

    assert temps == [55000]


def test_read_temps_skips_unreadable_files(tmp_path):
    with patch("glob.glob", return_value=["/nonexistent/path/temp"]):
        temps = watchdog.read_temps()
    assert temps == []


def test_max_temp_returns_peak():
    assert watchdog.max_temp([50000, 72000, 68000]) == 72000


def test_max_temp_returns_zero_on_empty():
    assert watchdog.max_temp([]) == 0


def test_warn_threshold_triggers_ntfy(capsys):
    with patch("watchdog.notify_ntfy") as mock_notify, \
         patch("watchdog.read_temps", side_effect=[[80500], [80500], StopIteration]), \
         patch("time.sleep"):
        try:
            watchdog.main()
        except StopIteration:
            pass
    mock_notify.assert_called()
    first_call_msg = mock_notify.call_args_list[0].args[0]
    assert "80" in first_call_msg


def test_emergency_threshold_calls_compose_down():
    with patch("watchdog.notify_ntfy"), \
         patch("subprocess.run") as mock_run, \
         patch("watchdog.read_temps", return_value=[91000]), \
         patch("time.sleep"), \
         pytest.raises(SystemExit):
        watchdog.main()

    calls = [str(c) for c in mock_run.call_args_list]
    assert any("compose" in c and "down" in c for c in calls)
```

- [ ] **Step 2: Run to confirm failure**

```bash
cd /Users/ivanvladisavljevic/Projects/belgrade-os && python3 -m pytest tests/test_watchdog.py -v 2>&1 | head -15
```

Expected: `ModuleNotFoundError` or `FileNotFoundError` — `watchdog.py` doesn't exist yet.

- [ ] **Step 3: Create watchdog.py**

```python
#!/usr/bin/env python3
"""Belgrade OS hardware watchdog — monitors CPU temperature, initiates staged shutdown."""
from __future__ import annotations
import glob
import logging
import os
import subprocess
import sys
import time

POLL_INTERVAL = 5        # seconds between thermal reads
WARN_TEMP    = 80_000    # millidegrees C (80°C) — log + notify
STAGE1_TEMP  = 85_000    # millidegrees C (85°C) — kill runner + inference containers
EMERGENCY_TEMP = 90_000  # millidegrees C (90°C) — docker compose down + exit

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    stream=sys.stdout,
)
log = logging.getLogger("watchdog")


def read_temps() -> list[int]:
    """Read all thermal zone temperatures. Returns millidegrees Celsius."""
    temps = []
    for path in glob.glob("/sys/class/thermal/thermal_zone*/temp"):
        try:
            with open(path) as f:
                temps.append(int(f.read().strip()))
        except (OSError, ValueError):
            pass
    return temps


def max_temp(temps: list[int]) -> int:
    return max(temps) if temps else 0


def notify_ntfy(message: str, priority: str = "default") -> None:
    url = os.getenv("WATCHDOG_NTFY_URL", "")
    if not url:
        return
    try:
        subprocess.run(
            ["curl", "-s", "-X", "POST", url, "-H", f"Priority: {priority}", "-d", message],
            timeout=5,
            check=False,
        )
    except Exception:
        pass


def kill_containers(label_value: str) -> None:
    """Kill all containers with label beg-os.role=<label_value>."""
    try:
        result = subprocess.run(
            ["docker", "ps", "-q", "--filter", f"label=beg-os.role={label_value}"],
            capture_output=True, text=True, timeout=10,
        )
        for cid in result.stdout.strip().split():
            if cid:
                subprocess.run(["docker", "kill", cid], timeout=10, check=False)
                log.info("killed container %s (role=%s)", cid, label_value)
    except Exception as e:
        log.error("Failed to kill containers role=%s: %s", label_value, e)


def main() -> None:
    log.info("Belgrade OS watchdog starting (poll=%ds)", POLL_INTERVAL)
    warned = False
    stage1_triggered = False

    while True:
        temps = read_temps()
        peak = max_temp(temps)

        if peak >= EMERGENCY_TEMP:
            msg = f"EMERGENCY: {peak // 1000}°C — shutting down Belgrade OS"
            log.critical(msg)
            notify_ntfy(msg, priority="urgent")
            subprocess.run(["docker", "compose", "down"], timeout=60, check=False)
            sys.exit(1)

        if peak >= STAGE1_TEMP:
            if not stage1_triggered:
                msg = f"WARNING: {peak // 1000}°C — stopping heavy workloads"
                log.warning(msg)
                notify_ntfy(msg, priority="high")
                kill_containers("runner")
                kill_containers("inference")
                stage1_triggered = True
        else:
            stage1_triggered = False

        if peak >= WARN_TEMP and not warned:
            msg = f"Notice: Belgrade OS CPU at {peak // 1000}°C"
            log.warning(msg)
            notify_ntfy(msg)
            warned = True
        elif peak < WARN_TEMP:
            warned = False

        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run watchdog tests**

```bash
cd /Users/ivanvladisavljevic/Projects/belgrade-os && python3 -m pytest tests/test_watchdog.py -v 2>&1
```

Expected: all 7 tests pass.

- [ ] **Step 5: Create watchdog.service**

```ini
[Unit]
Description=Belgrade OS Hardware Watchdog
Documentation=https://github.com/ivanvladisavljevic/belgrade-os
# Start after Docker so kill_containers() can reach the daemon
After=docker.service
Requires=docker.service

[Service]
Type=simple
# Run as root — required to read /sys/class/thermal and call docker
User=root
ExecStart=/usr/bin/python3 /opt/belgrade-os/watchdog.py
Restart=always
RestartSec=5
# Give 10s to finish cleanup on SIGTERM before SIGKILL
TimeoutStopSec=10
StandardOutput=journal
StandardError=journal
SyslogIdentifier=beg-os-watchdog
Environment=WATCHDOG_NTFY_URL=

[Install]
WantedBy=multi-user.target
```

- [ ] **Step 6: Commit**

```bash
git add watchdog.py watchdog.service tests/test_watchdog.py
git commit -m "feat(watchdog): thermal watchdog with staged shutdown at 80/85/90°C + systemd unit"
```

- [ ] **Step 7: Deployment instructions (manual, not automated)**

On the production host:

```bash
# Copy watchdog to standard location
sudo mkdir -p /opt/belgrade-os
sudo cp watchdog.py /opt/belgrade-os/watchdog.py
sudo chmod 755 /opt/belgrade-os/watchdog.py

# Install and enable the systemd unit
sudo cp watchdog.service /etc/systemd/system/beg-os-watchdog.service
sudo systemctl daemon-reload
sudo systemctl enable --now beg-os-watchdog.service

# Verify it's running and reading temperatures
sudo systemctl status beg-os-watchdog.service
journalctl -u beg-os-watchdog.service -f
```

---

## Self-Review

**1. Spec coverage**

| Requirement | Task |
|---|---|
| AppContext constructor mismatch (Critical #1) | Task 1 |
| `json` not imported in platform_controller (Critical #2) | Task 2 |
| `absRoot` computed at construction, error handled | Task 3 |
| `filepath.Rel` replaces `strings.HasPrefix` | Task 3 |
| `os.Stat` rejects directories before `http.ServeFile` | Task 3 |
| Platform controller admin API bearer token auth | Task 4 |
| `app_id` regex validation on `/apps/reload` | Task 4 |
| 3-tier Docker network (edge/core/compute) | Task 5 |
| Redis `--requirepass` | Task 5 |
| Redis rename dangerous commands | Task 5 |
| `cap_drop: ALL`, `no-new-privileges`, `read_only`, resource limits | Task 5 |
| docker-socket-proxy (EXEC=0, NETWORKS=0, VOLUMES=0) | Task 5 |
| Random secret generation in `.env` | Task 6 |
| UFW `DOCKER-USER` chain rules | Task 6 |
| Thermal watchdog 80/85/90°C staged shutdown | Task 7 |
| systemd unit file | Task 7 |

No gaps found.

**2. Placeholder scan:** None found.

**3. Type consistency**

- `BelgradeApp._db_engine: Optional[AsyncEngine]` — defined Task 1, referenced in `_build_context`, guards in shutdown. ✓
- `BelgradeApp._redis_pool: Optional[AioRedis]` — defined Task 1, passed to `AppContext(redis_pool=...)`. `AppContext.__init__` parameter is `redis_pool: Optional[Redis]` where `Redis = redis.asyncio.Redis = AioRedis`. ✓
- `_require_token` depends on `Optional[HTTPAuthorizationCredentials]` — imported from `fastapi.security`. ✓
- `_APP_ID_RE` used in `reload_app` — defined at module level. ✓
- `watchdog.max_temp`, `watchdog.read_temps`, `watchdog.notify_ntfy` — all defined and tested. ✓
