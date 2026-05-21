# MCP Server Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Expose Belgrade OS app tools to external AI agents (ChatGPT, Claude) via a new MCP server that authenticates with Cloudflare Access service tokens and proxies tool calls through the existing Bridge.

**Architecture:** A new `mcp_server/` Python/FastAPI service sits behind the Cloudflare tunnel at `/mcp/*` and `/oauth/*`. Apps opt in by adding `"mcp": true` to `manifest.json`; Platform Controller injects `BEG_OS_MCP_ENABLED=true` into the app process; the SDK forwards that flag to the Bridge at registration time. The MCP server fetches `GET /v1/tools?mcp=true` from the Bridge for tool discovery and `POST /v1/execute` for tool execution.

**Tech Stack:** Python 3.11, FastAPI, PyJWT 2.x, httpx, pytest-asyncio; Rust/Axum (Bridge extension)

---

## File Map

| File | Change |
|---|---|
| `bridge/src/registry.rs` | Add `mcp: bool` + `mcp_hint: Option<String>` to `ToolRegistration` and `RegisteredTool`; add `list_mcp()` method |
| `bridge/src/router.rs` | Add `mcp: Option<bool>` to `RegisterRequest`; `mcp_hint` to `ToolDef` + `ToolResponse`; `Query<ToolsQuery>` on `handle_tools` |
| `sdk/belgrade_sdk/models.py` | Add `mcp_hint: Optional[str]` to `ToolDefinition`; `mcp: bool` to `RegisterRequest` |
| `sdk/belgrade_sdk/app.py` | Add `mcp_hint` param to `@app.tool()`; read `BEG_OS_MCP_ENABLED` when building `RegisterRequest` |
| `sdk/tests/test_mcp_registration.py` | New: SDK mcp flag + hint tests |
| `platform_controller/main.py` | Add `mcp: bool` to `_AppManifest`; inject `BEG_OS_MCP_ENABLED` in `AppProcess.start()` |
| `platform_controller/tests/test_app_process.py` | Add mcp env injection test |
| `mcp_server/requirements.txt` | New service dependencies |
| `mcp_server/oauth.py` | CF JWT validation, Belgrade JWT issuance/validation |
| `mcp_server/registry.py` | Bridge proxy: `list_mcp_tools()` and `call_tool()` |
| `mcp_server/main.py` | FastAPI app: `/oauth/token`, `/mcp` JSON-RPC handler |
| `mcp_server/tests/test_mcp.py` | OAuth + MCP protocol tests |

---

### Task 1: Bridge — mcp flag, mcp_hint, GET /v1/tools?mcp=true

**Files:**
- Modify: `bridge/src/registry.rs`
- Modify: `bridge/src/router.rs`

- [ ] **Step 1: Write failing tests**

Append to the `#[cfg(test)]` block at the bottom of `bridge/src/router.rs`, inside `mod tests { ... }`, after the existing tests:

```rust
    #[tokio::test]
    async fn test_mcp_filter_excludes_non_mcp_tools() {
        let registry = Arc::new(ToolRegistry::new());
        let register_body = serde_json::json!({
            "app_id": "shopping",
            "callback_url": "http://app:8000",
            "tools": [{"name": "shopping:add_item", "description": "Add item", "input_schema_json": "{}"}],
            "mcp": false
        });
        make_router(Arc::clone(&registry))
            .oneshot(
                Request::builder()
                    .method("POST")
                    .uri("/v1/register")
                    .header("content-type", "application/json")
                    .body(Body::from(register_body.to_string()))
                    .unwrap(),
            )
            .await
            .unwrap();

        let resp = make_router(Arc::clone(&registry))
            .oneshot(Request::builder().uri("/v1/tools?mcp=true").body(Body::empty()).unwrap())
            .await
            .unwrap();
        let body = resp.into_body().collect().await.unwrap().to_bytes();
        let tools: serde_json::Value = serde_json::from_slice(&body).unwrap();
        assert_eq!(tools, serde_json::json!([]));
    }

    #[tokio::test]
    async fn test_mcp_filter_includes_mcp_tools() {
        let registry = Arc::new(ToolRegistry::new());
        let register_body = serde_json::json!({
            "app_id": "shopping",
            "callback_url": "http://app:8000",
            "tools": [{"name": "shopping:add_item", "description": "Add item", "input_schema_json": "{}", "mcp_hint": "Use when buying"}],
            "mcp": true
        });
        make_router(Arc::clone(&registry))
            .oneshot(
                Request::builder()
                    .method("POST")
                    .uri("/v1/register")
                    .header("content-type", "application/json")
                    .body(Body::from(register_body.to_string()))
                    .unwrap(),
            )
            .await
            .unwrap();

        let resp = make_router(Arc::clone(&registry))
            .oneshot(Request::builder().uri("/v1/tools?mcp=true").body(Body::empty()).unwrap())
            .await
            .unwrap();
        let body = resp.into_body().collect().await.unwrap().to_bytes();
        let tools: serde_json::Value = serde_json::from_slice(&body).unwrap();
        assert_eq!(tools.as_array().unwrap().len(), 1);
        assert_eq!(tools[0]["name"], "shopping:add_item");
        assert_eq!(tools[0]["mcp_hint"], "Use when buying");
    }

    #[tokio::test]
    async fn test_mcp_filter_absent_returns_all_tools() {
        let registry = Arc::new(ToolRegistry::new());
        let register_body = serde_json::json!({
            "app_id": "shopping",
            "callback_url": "http://app:8000",
            "tools": [{"name": "shopping:add_item", "description": "Add item", "input_schema_json": "{}"}],
            "mcp": false
        });
        make_router(Arc::clone(&registry))
            .oneshot(
                Request::builder()
                    .method("POST")
                    .uri("/v1/register")
                    .header("content-type", "application/json")
                    .body(Body::from(register_body.to_string()))
                    .unwrap(),
            )
            .await
            .unwrap();

        let resp = make_router(Arc::clone(&registry))
            .oneshot(Request::builder().uri("/v1/tools").body(Body::empty()).unwrap())
            .await
            .unwrap();
        let body = resp.into_body().collect().await.unwrap().to_bytes();
        let tools: serde_json::Value = serde_json::from_slice(&body).unwrap();
        assert_eq!(tools.as_array().unwrap().len(), 1);
    }
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd bridge && cargo test test_mcp 2>&1 | tail -20
```
Expected: compile error — `mcp` field unknown on `RegisterRequest` / `handle_tools` doesn't accept query params.

- [ ] **Step 3: Update registry.rs**

In `bridge/src/registry.rs`, replace the two existing structs and update `register()` and `list()`:

```rust
#[derive(Clone, Debug, serde::Serialize, serde::Deserialize)]
pub struct RegisteredTool {
    pub name: String,
    pub description: String,
    pub input_schema_json: String,
    pub app_id: String,
    pub callback_url: String,
    pub mcp: bool,
    pub mcp_hint: Option<String>,
}

pub struct ToolRegistration {
    pub name: String,
    pub description: String,
    pub input_schema_json: String,
    pub mcp: bool,
    pub mcp_hint: Option<String>,
}
```

In `register()`, update the `RegisteredTool` construction (around line 56):

```rust
map.insert(t.name.clone(), RegisteredTool {
    name: t.name.clone(),
    description: t.description.clone(),
    input_schema_json: t.input_schema_json.clone(),
    app_id: app_id.to_string(),
    callback_url: callback_url.to_string(),
    mcp: t.mcp,
    mcp_hint: t.mcp_hint.clone(),
});
```

Add `list_mcp()` method after the existing `list()` method:

```rust
pub fn list_mcp(&self) -> Vec<RegisteredTool> {
    let map = self.tools.read().expect("lock poisoned");
    map.values().filter(|t| t.mcp).cloned().collect()
}
```

- [ ] **Step 4: Update router.rs**

In `bridge/src/router.rs`, update the `use axum` import line to add `Query`:

```rust
use axum::{extract::{Query, State}, http::StatusCode, response::{IntoResponse, Json}, routing::{get, post}, Router};
```

Update the `RegisterRequest`, `ToolDef`, and `ToolResponse` structs:

```rust
#[derive(Deserialize)]
pub struct RegisterRequest {
    pub app_id: String,
    pub callback_url: String,
    pub tools: Vec<ToolDef>,
    pub subscriptions: Option<Vec<String>>,
    pub mcp: Option<bool>,
}

#[derive(Deserialize)]
pub struct ToolDef {
    pub name: String,
    pub description: String,
    pub input_schema_json: String,
    pub mcp_hint: Option<String>,
}

#[derive(Serialize)]
pub struct ToolResponse {
    pub name: String,
    pub description: String,
    pub input_schema_json: String,
    pub app_id: String,
    pub mcp_hint: Option<String>,
}
```

Add the query param struct (anywhere before `handle_tools`):

```rust
#[derive(Deserialize)]
pub struct ToolsQuery {
    pub mcp: Option<bool>,
}
```

Replace `handle_tools`:

```rust
async fn handle_tools(
    State(state): State<AppState>,
    Query(params): Query<ToolsQuery>,
) -> Json<Vec<ToolResponse>> {
    let tools = if params.mcp == Some(true) {
        state.registry.list_mcp()
    } else {
        state.registry.list()
    };
    let response = tools
        .into_iter()
        .map(|t| ToolResponse {
            name: t.name,
            description: t.description,
            input_schema_json: t.input_schema_json,
            app_id: t.app_id,
            mcp_hint: t.mcp_hint,
        })
        .collect();
    Json(response)
}
```

In `handle_register`, update the `registrations` construction (inside the handler, after URL validation):

```rust
let mcp = req.mcp.unwrap_or(false);
let registrations: Vec<ToolRegistration> = req
    .tools
    .iter()
    .map(|t| ToolRegistration {
        name: t.name.clone(),
        description: t.description.clone(),
        input_schema_json: t.input_schema_json.clone(),
        mcp,
        mcp_hint: t.mcp_hint.clone(),
    })
    .collect();
```

In `bridge/src/store.rs`, update the `RegisteredTool` construction inside `RedisStore::register` (around line 134):

```rust
let tool = RegisteredTool {
    name: t.name.clone(),
    description: t.description.clone(),
    input_schema_json: t.input_schema_json.clone(),
    app_id: app_id.to_string(),
    callback_url: callback_url.to_string(),
    mcp: t.mcp,
    mcp_hint: t.mcp_hint.clone(),
};
```

Also update the existing registry tests in `registry.rs` that construct `ToolRegistration` directly to add the two new fields (search for `ToolRegistration {` in the file):

```rust
ToolRegistration {
    name: "shopping:tool".to_string(),
    description: "desc".to_string(),
    input_schema_json: "{}".to_string(),
    mcp: false,
    mcp_hint: None,
}
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
cd bridge && cargo test 2>&1 | tail -20
```
Expected: all tests pass including the 3 new `test_mcp_*` tests.

- [ ] **Step 6: Commit**

```bash
git add bridge/src/registry.rs bridge/src/router.rs bridge/src/store.rs
git commit -m "feat(bridge): mcp flag + hint on tool registration, GET /v1/tools?mcp=true filter"
```

---

### Task 2: SDK — mcp_hint and mcp flag in tool registration

**Files:**
- Modify: `sdk/belgrade_sdk/models.py`
- Modify: `sdk/belgrade_sdk/app.py`
- Create: `sdk/tests/test_mcp_registration.py`

- [ ] **Step 1: Write failing tests**

Create `sdk/tests/test_mcp_registration.py`:

```python
from __future__ import annotations
import os
import pytest
from unittest.mock import AsyncMock, patch
from belgrade_sdk.app import BelgradeApp
from belgrade_sdk.models import RegisterRequest, ToolDefinition


def test_mcp_flag_false_by_default():
    app = BelgradeApp(app_id="shopping", bridge_url="http://bridge:8081")
    assert app._mcp_enabled is False


def test_mcp_flag_true_when_env_set():
    with patch.dict(os.environ, {"BEG_OS_MCP_ENABLED": "true"}):
        app = BelgradeApp(app_id="shopping", bridge_url="http://bridge:8081")
    assert app._mcp_enabled is True


def test_mcp_flag_in_register_request():
    with patch.dict(os.environ, {"BEG_OS_MCP_ENABLED": "true"}):
        app = BelgradeApp(app_id="shopping", bridge_url="http://bridge:8081")

    @app.tool("add-item", description="Add item")
    def add_item(ctx, item: str):
        pass

    req = RegisterRequest(
        app_id=app.app_id,
        callback_url="http://localhost:9001",
        tools=app.tool_definitions,
        mcp=app._mcp_enabled,
    )
    assert req.mcp is True


def test_mcp_hint_in_tool_definition():
    app = BelgradeApp(app_id="shopping", bridge_url="http://bridge:8081")

    @app.tool("add-item", description="Add item", mcp_hint="Use when buying groceries")
    def add_item(ctx, item: str):
        pass

    defn = app.tool_definitions[0]
    assert defn.mcp_hint == "Use when buying groceries"


def test_no_mcp_hint_is_none():
    app = BelgradeApp(app_id="shopping", bridge_url="http://bridge:8081")

    @app.tool("add-item", description="Add item")
    def add_item(ctx, item: str):
        pass

    defn = app.tool_definitions[0]
    assert defn.mcp_hint is None
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd sdk && python3 -m pytest tests/test_mcp_registration.py -v
```
Expected: FAIL — `BelgradeApp` has no attribute `_mcp_enabled`, `ToolDefinition` has no `mcp_hint`.

- [ ] **Step 3: Update models.py**

In `sdk/belgrade_sdk/models.py`, update `ToolDefinition` and `RegisterRequest`:

```python
class ToolDefinition(BaseModel):
    name: str
    description: str
    input_schema_json: str
    mcp_hint: Optional[str] = None

class RegisterRequest(BaseModel):
    app_id: str
    callback_url: str
    tools: List[ToolDefinition]
    subscriptions: Optional[List[str]] = None
    mcp: bool = False
```

- [ ] **Step 4: Update app.py**

In `sdk/belgrade_sdk/app.py`, in `BelgradeApp.__init__`, add after the existing instance variables:

```python
self._mcp_enabled: bool = os.getenv("BEG_OS_MCP_ENABLED") == "true"
```

Update the `tool()` decorator method signature and body:

```python
def tool(self, name: str, description: str, mcp_hint: Optional[str] = None):
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
            mcp_hint=mcp_hint,
        ))
        return func
    return decorator
```

Update `register_with_bridge` to include the `mcp` flag:

```python
async def register_with_bridge(self):
    """Notifies the Capability Bridge about available tools and subscriptions."""
    req = RegisterRequest(
        app_id=self.app_id,
        callback_url=self.callback_url,
        tools=self.tool_definitions,
        subscriptions=list(self.event_handlers.keys()),
        mcp=self._mcp_enabled,
    )
    async with httpx.AsyncClient() as client:
        try:
            resp = await client.post(f"{self.bridge_url}/v1/register", json=req.model_dump())
            resp.raise_for_status()
            logger.info(f"Registered {len(self.tools)} tools with bridge")
        except Exception as e:
            logger.error(f"Failed to register with bridge: {e}")
```

Make sure `Optional` is imported at the top of `app.py` — check the existing imports and add if missing:
```python
from typing import Callable, Dict, List, Optional
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
cd sdk && python3 -m pytest tests/test_mcp_registration.py -v
```
Expected: 5 passed.

- [ ] **Step 6: Run full SDK test suite**

```bash
cd sdk && python3 -m pytest tests/ -v
```
Expected: all tests pass.

- [ ] **Step 7: Commit**

```bash
git add sdk/belgrade_sdk/models.py sdk/belgrade_sdk/app.py sdk/tests/test_mcp_registration.py
git commit -m "feat(sdk): mcp_hint on @app.tool(), mcp flag in RegisterRequest"
```

---

### Task 3: Platform Controller — manifest mcp field + BEG_OS_MCP_ENABLED injection

**Files:**
- Modify: `platform_controller/main.py`
- Modify: `platform_controller/tests/test_app_process.py`

- [ ] **Step 1: Write failing tests**

Append to `platform_controller/tests/test_app_process.py`:

```python
def test_start_injects_mcp_enabled_when_manifest_has_mcp_true(tmp_path):
    """BEG_OS_MCP_ENABLED=true injected when manifest has mcp: true."""
    from main import _AppManifest
    app = AppProcess(app_id="shopping", path=tmp_path, port=9001)
    captured_env = {}

    manifest_data = {"app_id": "shopping", "mcp": True}
    (tmp_path / "manifest.json").write_text(json.dumps(manifest_data))

    def fake_popen(cmd, env, **kwargs):
        captured_env.update(env)
        mock = MagicMock()
        mock.pid = 1234
        return mock

    with patch("main.subprocess.Popen", side_effect=fake_popen), \
         patch("main.open", MagicMock()):
        asyncio.run(app.start())

    assert captured_env.get("BEG_OS_MCP_ENABLED") == "true"


def test_start_does_not_inject_mcp_when_manifest_has_mcp_false(tmp_path):
    """BEG_OS_MCP_ENABLED not set when manifest has mcp: false."""
    app = AppProcess(app_id="shopping", path=tmp_path, port=9001)
    captured_env = {}

    manifest_data = {"app_id": "shopping", "mcp": False}
    (tmp_path / "manifest.json").write_text(json.dumps(manifest_data))

    def fake_popen(cmd, env, **kwargs):
        captured_env.update(env)
        mock = MagicMock()
        mock.pid = 1234
        return mock

    with patch("main.subprocess.Popen", side_effect=fake_popen), \
         patch("main.open", MagicMock()):
        asyncio.run(app.start())

    assert captured_env.get("BEG_OS_MCP_ENABLED") != "true"


def test_start_does_not_inject_mcp_when_no_manifest(tmp_path):
    """BEG_OS_MCP_ENABLED not set when manifest is absent."""
    app = AppProcess(app_id="shopping", path=tmp_path, port=9001)
    captured_env = {}

    def fake_popen(cmd, env, **kwargs):
        captured_env.update(env)
        mock = MagicMock()
        mock.pid = 1234
        return mock

    with patch("main.subprocess.Popen", side_effect=fake_popen), \
         patch("main.open", MagicMock()):
        asyncio.run(app.start())

    assert "BEG_OS_MCP_ENABLED" not in captured_env
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd platform_controller && python3 -m pytest tests/test_app_process.py -v -k "mcp"
```
Expected: FAIL — `_AppManifest` has no `mcp` field, `AppProcess.start()` doesn't inject `BEG_OS_MCP_ENABLED`.

- [ ] **Step 3: Add mcp field to _AppManifest**

In `platform_controller/main.py`, update `_AppManifest`:

```python
class _AppManifest(BaseModel):
    app_id: str
    name: Optional[str] = None
    ui: Optional[_AppUIManifest] = None
    related_apps: List[str] = []
    notifications: Optional[_NotificationsManifest] = None
    mcp: bool = False
```

- [ ] **Step 4: Inject BEG_OS_MCP_ENABLED in AppProcess.start()**

In `platform_controller/main.py`, in the `AppProcess.start()` method, after the line `env["BEG_OS_NOTIFICATION_DRIVER"] = notification_driver`, add:

```python
        if manifest and manifest.mcp:
            env["BEG_OS_MCP_ENABLED"] = "true"
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
cd platform_controller && python3 -m pytest tests/test_app_process.py -v -k "mcp"
```
Expected: 3 passed.

- [ ] **Step 6: Run full platform_controller test suite**

```bash
cd platform_controller && python3 -m pytest tests/ -v
```
Expected: all tests pass.

- [ ] **Step 7: Commit**

```bash
git add platform_controller/main.py platform_controller/tests/test_app_process.py
git commit -m "feat(platform_controller): manifest mcp field, inject BEG_OS_MCP_ENABLED"
```

---

### Task 4: MCP Server — project setup and OAuth

**Files:**
- Create: `mcp_server/requirements.txt`
- Create: `mcp_server/oauth.py`
- Create: `mcp_server/main.py` (OAuth endpoint only)
- Create: `mcp_server/tests/__init__.py`
- Create: `mcp_server/tests/test_mcp.py` (OAuth tests only)

- [ ] **Step 1: Write failing tests**

Create `mcp_server/tests/__init__.py` (empty).

Create `mcp_server/tests/test_mcp.py`:

```python
from __future__ import annotations
import os
import time
import pytest
from unittest.mock import patch
from fastapi.testclient import TestClient


def _make_client():
    with patch.dict(os.environ, {
        "BRIDGE_URL": "http://localhost:8081",
        "MCP_JWT_SECRET": "test-secret",
        "MCP_DEFAULT_USER_ID": "ivan",
        "MCP_DEFAULT_TENANT_ID": "default",
        "CF_TEAM_DOMAIN": "beg-os",
        "CF_MCP_AUDIENCE": "test-audience",
    }):
        import importlib
        import sys
        for mod in list(sys.modules.keys()):
            if mod.startswith("main") or mod.startswith("oauth") or mod.startswith("registry"):
                del sys.modules[mod]
        import main as m
        return TestClient(m.app), m


def test_token_missing_cf_jwt_returns_401():
    client, _ = _make_client()
    resp = client.post(
        "/oauth/token",
        data={"grant_type": "client_credentials"},
    )
    assert resp.status_code == 401


def test_token_wrong_grant_type_returns_400():
    client, _ = _make_client()
    resp = client.post(
        "/oauth/token",
        data={"grant_type": "password"},
        headers={"CF-Access-Jwt-Assertion": "fake-jwt"},
    )
    assert resp.status_code == 400


def test_token_invalid_cf_jwt_returns_401():
    client, _ = _make_client()
    with patch("oauth.validate_cf_jwt", side_effect=Exception("invalid")):
        resp = client.post(
            "/oauth/token",
            data={"grant_type": "client_credentials"},
            headers={"CF-Access-Jwt-Assertion": "bad-token"},
        )
    assert resp.status_code == 401


def test_token_valid_cf_jwt_returns_bearer_token():
    client, _ = _make_client()
    with patch("oauth.validate_cf_jwt", return_value={"sub": "service-token-abc"}):
        resp = client.post(
            "/oauth/token",
            data={"grant_type": "client_credentials"},
            headers={"CF-Access-Jwt-Assertion": "valid-jwt"},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["token_type"] == "bearer"
    assert "access_token" in body
    assert body["expires_in"] == 3600


def test_mcp_unauthenticated_returns_401():
    client, _ = _make_client()
    resp = client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "method": "initialize", "id": 1},
    )
    assert resp.status_code == 401
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd mcp_server && python3 -m pytest tests/test_mcp.py -v 2>&1 | tail -10
```
Expected: FAIL — `mcp_server/` doesn't exist yet, ModuleNotFoundError.

- [ ] **Step 3: Create requirements.txt**

Create `mcp_server/requirements.txt`:

```
fastapi==0.115.0
uvicorn[standard]==0.30.0
PyJWT==2.8.0
httpx==0.27.0
pydantic==2.7.1
pytest==8.2.0
pytest-asyncio==0.23.7
```

- [ ] **Step 4: Create oauth.py**

Create `mcp_server/oauth.py`:

```python
from __future__ import annotations
import os
import jwt
from datetime import datetime, timedelta, timezone
from jwt import PyJWKClient

_CF_TEAM_DOMAIN = os.getenv("CF_TEAM_DOMAIN", "")
_CF_MCP_AUDIENCE = os.getenv("CF_MCP_AUDIENCE", "")
_MCP_JWT_SECRET = os.getenv("MCP_JWT_SECRET", "")

_jwks_client: PyJWKClient | None = None


def _get_jwks_client() -> PyJWKClient:
    global _jwks_client
    if _jwks_client is None:
        url = f"https://{_CF_TEAM_DOMAIN}.cloudflareaccess.com/cdn-cgi/access/certs"
        _jwks_client = PyJWKClient(url)
    return _jwks_client


def validate_cf_jwt(token: str) -> dict:
    """Validate a Cloudflare Access JWT. Raises on invalid token."""
    client = _get_jwks_client()
    signing_key = client.get_signing_key_from_jwt(token)
    return jwt.decode(
        token,
        signing_key.key,
        algorithms=["RS256"],
        audience=_CF_MCP_AUDIENCE,
    )


def issue_token(user_id: str, tenant_id: str) -> str:
    """Issue a short-lived Belgrade JWT for MCP sessions."""
    payload = {
        "user_id": user_id,
        "tenant_id": tenant_id,
        "exp": datetime.now(tz=timezone.utc) + timedelta(hours=1),
    }
    return jwt.encode(payload, _MCP_JWT_SECRET, algorithm="HS256")


def validate_token(token: str) -> dict:
    """Validate a Belgrade JWT. Returns claims dict. Raises on invalid."""
    return jwt.decode(token, _MCP_JWT_SECRET, algorithms=["HS256"])
```

- [ ] **Step 5: Create main.py (OAuth endpoint only)**

Create `mcp_server/main.py`:

```python
from __future__ import annotations
import os
from fastapi import FastAPI, HTTPException, Request, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
import oauth

app = FastAPI(title="Belgrade OS MCP Server")

_security = HTTPBearer(auto_error=False)

_MCP_DEFAULT_USER_ID = os.getenv("MCP_DEFAULT_USER_ID", "")
_MCP_DEFAULT_TENANT_ID = os.getenv("MCP_DEFAULT_TENANT_ID", "")


async def _require_auth(
    credentials: HTTPAuthorizationCredentials | None = Depends(_security),
) -> dict:
    if not credentials:
        raise HTTPException(status_code=401, detail="missing token")
    try:
        return oauth.validate_token(credentials.credentials)
    except Exception:
        raise HTTPException(status_code=401, detail="invalid token")


@app.post("/oauth/token")
async def token_endpoint(request: Request):
    form = await request.form()
    grant_type = form.get("grant_type")
    if grant_type != "client_credentials":
        raise HTTPException(status_code=400, detail="unsupported_grant_type")

    cf_jwt = request.headers.get("CF-Access-Jwt-Assertion")
    if not cf_jwt:
        raise HTTPException(status_code=401, detail="missing CF JWT")

    try:
        oauth.validate_cf_jwt(cf_jwt)
    except Exception:
        raise HTTPException(status_code=401, detail="invalid CF service token")

    token = oauth.issue_token(_MCP_DEFAULT_USER_ID, _MCP_DEFAULT_TENANT_ID)
    return {"access_token": token, "token_type": "bearer", "expires_in": 3600}


@app.post("/mcp")
async def mcp_handler(
    request: Request,
    claims: dict = Depends(_require_auth),
):
    # Placeholder — MCP protocol implemented in Task 5
    return {"jsonrpc": "2.0", "result": {}, "id": None}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("MCP_PORT", "8083")))
```

- [ ] **Step 6: Run tests to verify they pass**

```bash
cd mcp_server && pip install -r requirements.txt -q && python3 -m pytest tests/test_mcp.py -v -k "oauth or token or unauthenticated"
```
Expected: 5 passed.

- [ ] **Step 7: Commit**

```bash
git add mcp_server/requirements.txt mcp_server/oauth.py mcp_server/main.py mcp_server/tests/__init__.py mcp_server/tests/test_mcp.py
git commit -m "feat(mcp_server): project setup, OAuth token endpoint, CF JWT validation"
```

---

### Task 5: MCP Server — MCP JSON-RPC protocol

**Files:**
- Create: `mcp_server/registry.py`
- Modify: `mcp_server/main.py` — replace placeholder `/mcp` handler with full JSON-RPC implementation
- Modify: `mcp_server/tests/test_mcp.py` — append MCP protocol tests

- [ ] **Step 1: Write failing tests**

Append to `mcp_server/tests/test_mcp.py`:

```python
import json
from unittest.mock import AsyncMock, patch


def _make_token(client, m) -> str:
    """Get a valid Bearer token for use in MCP tests."""
    with patch("oauth.validate_cf_jwt", return_value={"sub": "svc"}):
        resp = client.post(
            "/oauth/token",
            data={"grant_type": "client_credentials"},
            headers={"CF-Access-Jwt-Assertion": "valid"},
        )
    return resp.json()["access_token"]


def test_mcp_initialize_returns_capabilities():
    client, m = _make_client()
    token = _make_token(client, m)
    resp = client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "method": "initialize", "params": {}, "id": 1},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["result"]["protocolVersion"] == "2024-11-05"
    assert "tools" in body["result"]["capabilities"]


def test_mcp_tools_list_returns_bridge_tools():
    client, m = _make_client()
    token = _make_token(client, m)

    bridge_tools = [
        {
            "name": "shopping:add-item",
            "description": "Add item to list",
            "input_schema_json": json.dumps({"type": "object", "properties": {"item": {"type": "string"}}}),
            "app_id": "shopping",
            "mcp_hint": "Use when buying groceries",
        }
    ]

    with patch("registry.list_mcp_tools", new_callable=AsyncMock, return_value=bridge_tools):
        resp = client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "method": "tools/list", "params": {}, "id": 2},
            headers={"Authorization": f"Bearer {token}"},
        )
    assert resp.status_code == 200
    tools = resp.json()["result"]["tools"]
    assert len(tools) == 1
    assert tools[0]["name"] == "shopping:add-item"
    assert tools[0]["description"] == "Add item to list. Use when buying groceries."
    assert tools[0]["inputSchema"] == {"type": "object", "properties": {"item": {"type": "string"}}}


def test_mcp_tools_call_routes_to_bridge():
    client, m = _make_client()
    token = _make_token(client, m)

    bridge_tools = [
        {
            "name": "shopping:add-item",
            "description": "Add item",
            "input_schema_json": "{}",
            "app_id": "shopping",
            "mcp_hint": None,
        }
    ]
    bridge_result = {"success": True, "output_json": json.dumps({"added": "milk"}), "error": ""}

    with patch("registry.list_mcp_tools", new_callable=AsyncMock, return_value=bridge_tools), \
         patch("registry.call_tool", new_callable=AsyncMock, return_value=bridge_result) as mock_call:
        resp = client.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "method": "tools/call",
                "params": {"name": "shopping:add-item", "arguments": {"item": "milk"}},
                "id": 3,
            },
            headers={"Authorization": f"Bearer {token}"},
        )

    assert resp.status_code == 200
    content = resp.json()["result"]["content"]
    assert content[0]["type"] == "text"
    assert "milk" in content[0]["text"]

    mock_call.assert_called_once_with(
        "shopping:add-item",
        {"item": "milk"},
        "ivan",
        "default",
    )


def test_mcp_tools_call_unknown_tool_returns_error():
    client, m = _make_client()
    token = _make_token(client, m)

    with patch("registry.list_mcp_tools", new_callable=AsyncMock, return_value=[]):
        resp = client.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "method": "tools/call",
                "params": {"name": "shopping:unknown", "arguments": {}},
                "id": 4,
            },
            headers={"Authorization": f"Bearer {token}"},
        )

    assert resp.status_code == 200
    body = resp.json()
    assert "error" in body
    assert body["error"]["code"] == -32602


def test_mcp_unknown_method_returns_error():
    client, m = _make_client()
    token = _make_token(client, m)
    resp = client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "method": "unknown/method", "id": 5},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert "error" in body
    assert body["error"]["code"] == -32601
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd mcp_server && python3 -m pytest tests/test_mcp.py -v -k "initialize or tools_list or tools_call or unknown_method" 2>&1 | tail -15
```
Expected: FAIL — `/mcp` returns empty result placeholder, `registry` module doesn't exist.

- [ ] **Step 3: Create registry.py**

Create `mcp_server/registry.py`:

```python
from __future__ import annotations
import json
import os
import uuid
import httpx

_BRIDGE_URL = os.getenv("BRIDGE_URL", "http://localhost:8081")


async def list_mcp_tools() -> list[dict]:
    """Fetch MCP-exposed tools from Bridge."""
    async with httpx.AsyncClient() as client:
        resp = await client.get(f"{_BRIDGE_URL}/v1/tools?mcp=true")
        resp.raise_for_status()
        return resp.json()


async def call_tool(
    tool_name: str,
    arguments: dict,
    user_id: str,
    tenant_id: str,
) -> dict:
    """Execute a tool via Bridge /v1/execute."""
    payload = {
        "call_id": str(uuid.uuid4()),
        "task_id": str(uuid.uuid4()),
        "trace_id": str(uuid.uuid4()),
        "tool_name": tool_name,
        "input_json": json.dumps(arguments),
        "user_id": user_id,
        "tenant_id": tenant_id,
    }
    async with httpx.AsyncClient() as client:
        resp = await client.post(f"{_BRIDGE_URL}/v1/execute", json=payload)
        resp.raise_for_status()
        return resp.json()
```

- [ ] **Step 4: Replace the /mcp placeholder in main.py**

In `mcp_server/main.py`, add the import for `registry` and `json` at the top, then replace the entire `mcp_handler` function:

Add at the top (after existing imports):
```python
import json
import registry
```

Replace `mcp_handler`:
```python
@app.post("/mcp")
async def mcp_handler(
    request: Request,
    claims: dict = Depends(_require_auth),
):
    body = await request.json()
    method = body.get("method", "")
    params = body.get("params") or {}
    req_id = body.get("id")

    def ok(result: dict) -> dict:
        return {"jsonrpc": "2.0", "result": result, "id": req_id}

    def err(code: int, message: str) -> dict:
        return {"jsonrpc": "2.0", "error": {"code": code, "message": message}, "id": req_id}

    if method == "initialize":
        return ok({
            "protocolVersion": "2024-11-05",
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "belgrade-os", "version": "1.0.0"},
        })

    if method == "tools/list":
        try:
            tools = await registry.list_mcp_tools()
        except Exception as exc:
            return err(-32603, f"bridge unavailable: {exc}")
        mcp_tools = []
        for t in tools:
            description = t["description"]
            if t.get("mcp_hint"):
                description = f"{description}. {t['mcp_hint']}".rstrip(".")
                description += "."
            mcp_tools.append({
                "name": t["name"],
                "description": description,
                "inputSchema": json.loads(t["input_schema_json"]),
            })
        return ok({"tools": mcp_tools})

    if method == "tools/call":
        tool_name = params.get("name", "")
        arguments = params.get("arguments") or {}
        try:
            mcp_tools = await registry.list_mcp_tools()
        except Exception as exc:
            return err(-32603, f"bridge unavailable: {exc}")
        exposed_names = {t["name"] for t in mcp_tools}
        if tool_name not in exposed_names:
            return err(-32602, f"tool not found: {tool_name}")
        try:
            result = await registry.call_tool(
                tool_name,
                arguments,
                claims.get("user_id", _MCP_DEFAULT_USER_ID),
                claims.get("tenant_id", _MCP_DEFAULT_TENANT_ID),
            )
        except Exception as exc:
            return err(-32603, f"tool execution failed: {exc}")
        if not result.get("success"):
            return err(-32603, result.get("error", "tool execution failed"))
        output = result.get("output_json", "{}")
        return ok({"content": [{"type": "text", "text": output}]})

    return err(-32601, f"method not found: {method}")
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
cd mcp_server && python3 -m pytest tests/test_mcp.py -v
```
Expected: all 10 tests pass.

- [ ] **Step 6: Commit**

```bash
git add mcp_server/registry.py mcp_server/main.py mcp_server/tests/test_mcp.py
git commit -m "feat(mcp_server): MCP JSON-RPC protocol — initialize, tools/list, tools/call"
```

---

### Final verification

- [ ] **Run all affected test suites**

```bash
cd bridge && cargo test 2>&1 | tail -5
cd sdk && python3 -m pytest tests/ -v 2>&1 | tail -5
cd platform_controller && python3 -m pytest tests/ -v 2>&1 | tail -5
cd mcp_server && python3 -m pytest tests/ -v 2>&1 | tail -5
```
Expected: all tests pass across all four services.
