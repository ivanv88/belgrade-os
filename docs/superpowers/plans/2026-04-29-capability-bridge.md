# Capability Bridge Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the Capability Bridge — a Rust HTTP service that maintains a tool registry and dispatches `POST /v1/execute` requests from the Resource Runner to the correct app callback URL.

**Architecture:** Apps register their tools at startup via `POST /v1/register`, giving the bridge their tool definitions and a callback URL. When the runner sends a tool call, the bridge looks up the owning app and forwards the request to `{callback_url}/execute`. All endpoints live under `/v1/` for versioning; future endpoints (`/v1/tools`, `/v1/notifications/provider`, `/v1/manifests`, WS `/v1/ui/events`) each get their own namespace.

**Tech Stack:** Rust, Axum 0.7, Tokio 1, serde/serde_json, reqwest 0.12, prost 0.12 (existing), tracing, wiremock (tests)

---

## Endpoint Contract

| Endpoint | Consumer | Status | Purpose |
|---|---|---|---|
| `POST /v1/register` | Apps (platform) | **this plan** | Register tools + callback URL |
| `POST /v1/execute` | Resource Runner | **this plan** | Dispatch tool call to app |
| `GET /v1/tools` | Inference Controller | **this plan** | List all registered tools |
| `GET /v1/notifications/provider` | Gateway | **this plan** | ntfy.sh push config |
| `GET /v1/manifests` | (TBD) | future | App manifest metadata |
| WS `/v1/ui/events` | (TBD) | future | Real-time UI stream |

### `POST /v1/register` (app → bridge, at startup)

**Request:**
```json
{
  "app_id": "shopping",
  "callback_url": "http://platform:8000/apps/shopping",
  "tools": [
    {"name": "shopping:add_item", "description": "Add item to list", "input_schema_json": "{\"type\":\"object\"}"}
  ]
}
```
**Response:** `204 No Content`. Re-registering the same `app_id` replaces all previous tools for that app.

### `POST /v1/execute` (runner → bridge)

**Request:**
```json
{"call_id": "…", "task_id": "…", "tool_name": "shopping:add_item", "input_json": "{…}", "trace_id": "…"}
```
**Response 200:**
```json
{"call_id": "…", "task_id": "…", "success": true, "output_json": "{…}", "error": ""}
```
Unknown tool or any dispatch error → `success: false, error: "..."`. Bridge never returns non-200.

The bridge forwards to `{callback_url}/execute` with:
```json
{"tool_name": "shopping:add_item", "input_json": "{…}", "trace_id": "…"}
```
App responds with:
```json
{"success": true, "output_json": "{…}", "error": ""}
```

### `GET /v1/tools`

**Response 200:**
```json
[{"name": "shopping:add_item", "description": "…", "input_schema_json": "…", "app_id": "shopping"}]
```

### `GET /v1/notifications/provider`

**Response 200:**
```json
{"provider": "ntfy", "base_url": "https://ntfy.sh", "topic": "belgrade-os"}
```

---

## File Map

```
bridge/
├── Cargo.toml          — add axum, tokio, serde, serde_json, reqwest, tracing,
│                         tracing-subscriber; dev: wiremock, tower, http-body-util
├── src/
│   ├── lib.rs          — EXISTING proto bindings — do not touch
│   ├── config.rs       — Config: port (default 8081), ntfy_base_url, ntfy_topic
│   ├── registry.rs     — ToolRegistry (Arc<RwLock<HashMap>>): register, get, list
│   ├── router.rs       — AppState, all request/response types, all handler fns,
│   │                     create_router() — tests live here too
│   └── main.rs         — entry point: load Config, build registry + router, bind TCP
```

`router.rs` is intentionally one file while the bridge is small. When new endpoint groups are added, split to `handlers/` at that point.

---

## Task 1: Dependencies + config

**Files:**
- Modify: `bridge/Cargo.toml`
- Create: `bridge/src/config.rs`

- [ ] **Step 1: Write failing test**

Create the `#[cfg(test)]` block inside the (not yet created) `bridge/src/config.rs`. First create the file with just the test:

```rust
// bridge/src/config.rs

pub struct Config {
    pub port: u16,
    pub ntfy_base_url: String,
    pub ntfy_topic: String,
}

impl Config {
    pub fn from_env() -> Self {
        todo!()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_defaults() {
        std::env::remove_var("PORT");
        std::env::remove_var("NTFY_BASE_URL");
        std::env::remove_var("NTFY_TOPIC");
        let cfg = Config::from_env();
        assert_eq!(cfg.port, 8081);
        assert_eq!(cfg.ntfy_base_url, "https://ntfy.sh");
        assert_eq!(cfg.ntfy_topic, "belgrade-os");
    }

    #[test]
    fn test_from_env() {
        std::env::set_var("PORT", "9090");
        std::env::set_var("NTFY_TOPIC", "my-topic");
        let cfg = Config::from_env();
        assert_eq!(cfg.port, 9090);
        assert_eq!(cfg.ntfy_topic, "my-topic");
        std::env::remove_var("PORT");
        std::env::remove_var("NTFY_TOPIC");
    }
}
```

Add `mod config;` to `bridge/src/lib.rs` — add it after the existing `pub mod belgrade_os` block:
```rust
pub mod config;
```

- [ ] **Step 2: Run to confirm tests fail**

```bash
cd /Users/ivanvladisavljevic/Projects/belgrade-os/.worktrees/foundation/bridge && cargo test config 2>&1
```
Expected: FAIL with `not yet implemented` (todo! panic)

- [ ] **Step 3: Update `bridge/Cargo.toml`**

Full replacement:
```toml
[package]
name = "bridge"
version = "0.1.0"
edition = "2021"

[dependencies]
axum = "0.7"
tokio = { version = "1", features = ["full"] }
serde = { version = "1", features = ["derive"] }
serde_json = "1"
reqwest = { version = "0.12", features = ["json"] }
tracing = "0.1"
tracing-subscriber = { version = "0.3", features = ["env-filter"] }
prost = "0.12"

[build-dependencies]
prost-build = "0.12"

[dev-dependencies]
wiremock = "0.6"
tower = { version = "0.4", features = ["util"] }
http-body-util = "0.1"
```

- [ ] **Step 4: Implement `Config::from_env`**

Replace the `todo!()` implementation:
```rust
impl Config {
    pub fn from_env() -> Self {
        Self {
            port: std::env::var("PORT")
                .unwrap_or_else(|_| "8081".to_string())
                .parse()
                .expect("PORT must be a number"),
            ntfy_base_url: std::env::var("NTFY_BASE_URL")
                .unwrap_or_else(|_| "https://ntfy.sh".to_string()),
            ntfy_topic: std::env::var("NTFY_TOPIC")
                .unwrap_or_else(|_| "belgrade-os".to_string()),
        }
    }
}
```

- [ ] **Step 5: Run to confirm tests pass**

```bash
cd /Users/ivanvladisavljevic/Projects/belgrade-os/.worktrees/foundation/bridge && cargo test config 2>&1
```
Expected:
```
test tests::test_defaults ... ok
test tests::test_from_env ... ok
```

- [ ] **Step 6: Confirm existing proto tests still pass**

```bash
cd /Users/ivanvladisavljevic/Projects/belgrade-os/.worktrees/foundation/bridge && cargo test 2>&1 | tail -5
```
Expected: `test result: ok. 10 passed` (8 proto + 2 config)

- [ ] **Step 7: Commit**

```bash
cd /Users/ivanvladisavljevic/Projects/belgrade-os/.worktrees/foundation && git add bridge/Cargo.toml bridge/src/config.rs bridge/src/lib.rs && git commit -m "feat(bridge): dependencies + config"
```

---

## Task 2: Tool registry

**Files:**
- Create: `bridge/src/registry.rs`
- Modify: `bridge/src/lib.rs` (add `pub mod registry;`)

- [ ] **Step 1: Write failing tests**

Create `bridge/src/registry.rs`:

```rust
// bridge/src/registry.rs
use std::collections::HashMap;
use std::sync::RwLock;

#[derive(Clone, Debug)]
pub struct RegisteredTool {
    pub name: String,
    pub description: String,
    pub input_schema_json: String,
    pub app_id: String,
    pub callback_url: String,
}

pub struct ToolRegistration {
    pub name: String,
    pub description: String,
    pub input_schema_json: String,
}

pub struct ToolRegistry {
    tools: RwLock<HashMap<String, RegisteredTool>>,
}

impl ToolRegistry {
    pub fn new() -> Self {
        todo!()
    }

    pub fn register(&self, _app_id: &str, _callback_url: &str, _tools: &[ToolRegistration]) {
        todo!()
    }

    pub fn get(&self, _tool_name: &str) -> Option<RegisteredTool> {
        todo!()
    }

    pub fn list(&self) -> Vec<RegisteredTool> {
        todo!()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn tool(name: &str) -> ToolRegistration {
        ToolRegistration {
            name: name.to_string(),
            description: "desc".to_string(),
            input_schema_json: "{}".to_string(),
        }
    }

    #[test]
    fn test_register_and_get() {
        let reg = ToolRegistry::new();
        reg.register("shopping", "http://app:8000", &[tool("shopping:add_item")]);
        let t = reg.get("shopping:add_item").unwrap();
        assert_eq!(t.name, "shopping:add_item");
        assert_eq!(t.app_id, "shopping");
        assert_eq!(t.callback_url, "http://app:8000");
    }

    #[test]
    fn test_get_unknown_returns_none() {
        let reg = ToolRegistry::new();
        assert!(reg.get("unknown:tool").is_none());
    }

    #[test]
    fn test_list_returns_all_tools() {
        let reg = ToolRegistry::new();
        reg.register("app1", "http://app1:8000", &[tool("app1:t1"), tool("app1:t2")]);
        assert_eq!(reg.list().len(), 2);
    }

    #[test]
    fn test_re_register_replaces_old_tools() {
        let reg = ToolRegistry::new();
        reg.register("shopping", "http://app:8000", &[tool("shopping:old")]);
        reg.register("shopping", "http://app:8000", &[tool("shopping:new")]);
        assert!(reg.get("shopping:old").is_none());
        assert!(reg.get("shopping:new").is_some());
    }

    #[test]
    fn test_re_register_does_not_affect_other_apps() {
        let reg = ToolRegistry::new();
        reg.register("app1", "http://app1:8000", &[tool("app1:t1")]);
        reg.register("app2", "http://app2:8000", &[tool("app2:t1")]);
        reg.register("app1", "http://app1:8000", &[tool("app1:t2")]);
        assert!(reg.get("app1:t1").is_none());
        assert!(reg.get("app1:t2").is_some());
        assert!(reg.get("app2:t1").is_some()); // app2 untouched
    }
}
```

Add `pub mod registry;` to `bridge/src/lib.rs` (after `pub mod config;`).

- [ ] **Step 2: Run to confirm tests fail**

```bash
cd /Users/ivanvladisavljevic/Projects/belgrade-os/.worktrees/foundation/bridge && cargo test registry 2>&1
```
Expected: FAIL with `not yet implemented`

- [ ] **Step 3: Implement ToolRegistry**

Replace the `todo!()` bodies in `bridge/src/registry.rs`:

```rust
impl ToolRegistry {
    pub fn new() -> Self {
        Self { tools: RwLock::new(HashMap::new()) }
    }

    pub fn register(&self, app_id: &str, callback_url: &str, tools: &[ToolRegistration]) {
        let mut map = self.tools.write().unwrap();
        map.retain(|_, v| v.app_id != app_id);
        for t in tools {
            map.insert(t.name.clone(), RegisteredTool {
                name: t.name.clone(),
                description: t.description.clone(),
                input_schema_json: t.input_schema_json.clone(),
                app_id: app_id.to_string(),
                callback_url: callback_url.to_string(),
            });
        }
    }

    pub fn get(&self, tool_name: &str) -> Option<RegisteredTool> {
        self.tools.read().unwrap().get(tool_name).cloned()
    }

    pub fn list(&self) -> Vec<RegisteredTool> {
        self.tools.read().unwrap().values().cloned().collect()
    }
}
```

- [ ] **Step 4: Run to confirm tests pass**

```bash
cd /Users/ivanvladisavljevic/Projects/belgrade-os/.worktrees/foundation/bridge && cargo test registry 2>&1
```
Expected: 5 tests pass.

- [ ] **Step 5: Confirm all tests still pass**

```bash
cd /Users/ivanvladisavljevic/Projects/belgrade-os/.worktrees/foundation/bridge && cargo test 2>&1 | tail -5
```
Expected: `test result: ok. 15 passed`

- [ ] **Step 6: Commit**

```bash
cd /Users/ivanvladisavljevic/Projects/belgrade-os/.worktrees/foundation && git add bridge/src/registry.rs bridge/src/lib.rs && git commit -m "feat(bridge): tool registry — register, get, list"
```

---

## Task 3: Router — register + tools + notifications endpoints

**Files:**
- Create: `bridge/src/router.rs`
- Modify: `bridge/src/lib.rs` (add `pub mod router;`)

This task creates the router with three endpoints: `POST /v1/register`, `GET /v1/tools`, `GET /v1/notifications/provider`. The execute endpoint is added in Task 4.

- [ ] **Step 1: Write failing tests**

Create `bridge/src/router.rs` with skeleton types and tests:

```rust
// bridge/src/router.rs
use axum::{extract::State, http::StatusCode, response::Json, routing::{get, post}, Router};
use serde::{Deserialize, Serialize};
use std::sync::Arc;
use crate::{config::Config, registry::{ToolRegistration, ToolRegistry}};

#[derive(Clone)]
pub struct AppState {
    pub registry: Arc<ToolRegistry>,
    pub config: Arc<Config>,
    pub http: reqwest::Client,
}

#[derive(Deserialize)]
pub struct RegisterRequest {
    pub app_id: String,
    pub callback_url: String,
    pub tools: Vec<ToolDef>,
}

#[derive(Deserialize)]
pub struct ToolDef {
    pub name: String,
    pub description: String,
    pub input_schema_json: String,
}

#[derive(Serialize)]
pub struct ToolResponse {
    pub name: String,
    pub description: String,
    pub input_schema_json: String,
    pub app_id: String,
}

#[derive(Deserialize)]
pub struct ExecuteRequest {
    pub call_id: String,
    pub task_id: String,
    pub tool_name: String,
    pub input_json: String,
    pub trace_id: String,
}

#[derive(Serialize, Deserialize)]
pub struct ExecuteResponse {
    pub call_id: String,
    pub task_id: String,
    pub success: bool,
    pub output_json: String,
    pub error: String,
}

#[derive(Serialize)]
pub struct NotificationsProviderResponse {
    pub provider: String,
    pub base_url: String,
    pub topic: String,
}

async fn handle_register(
    State(_state): State<AppState>,
    Json(_req): Json<RegisterRequest>,
) -> StatusCode {
    todo!()
}

async fn handle_tools(
    State(_state): State<AppState>,
) -> Json<Vec<ToolResponse>> {
    todo!()
}

async fn handle_execute(
    State(_state): State<AppState>,
    Json(_req): Json<ExecuteRequest>,
) -> Json<ExecuteResponse> {
    todo!()
}

async fn handle_notifications_provider(
    State(_state): State<AppState>,
) -> Json<NotificationsProviderResponse> {
    todo!()
}

pub fn create_router(registry: Arc<ToolRegistry>, config: Arc<Config>) -> Router {
    let state = AppState { registry, config, http: reqwest::Client::new() };
    Router::new()
        .route("/v1/register", post(handle_register))
        .route("/v1/tools", get(handle_tools))
        .route("/v1/execute", post(handle_execute))
        .route("/v1/notifications/provider", get(handle_notifications_provider))
        .with_state(state)
}

#[cfg(test)]
mod tests {
    use super::*;
    use axum::{body::Body, http::Request};
    use http_body_util::BodyExt;
    use tower::ServiceExt;

    fn make_config() -> Arc<Config> {
        Arc::new(Config {
            port: 8081,
            ntfy_base_url: "https://ntfy.sh".to_string(),
            ntfy_topic: "test-topic".to_string(),
        })
    }

    #[tokio::test]
    async fn test_tools_empty_on_start() {
        let registry = Arc::new(ToolRegistry::new());
        let app = create_router(Arc::clone(&registry), make_config());

        let resp = app
            .oneshot(Request::builder().uri("/v1/tools").body(Body::empty()).unwrap())
            .await
            .unwrap();
        assert_eq!(resp.status(), StatusCode::OK);
        let body = resp.into_body().collect().await.unwrap().to_bytes();
        let tools: serde_json::Value = serde_json::from_slice(&body).unwrap();
        assert_eq!(tools, serde_json::json!([]));
    }

    #[tokio::test]
    async fn test_register_returns_204() {
        let registry = Arc::new(ToolRegistry::new());
        let app = create_router(Arc::clone(&registry), make_config());

        let body = serde_json::json!({
            "app_id": "shopping",
            "callback_url": "http://app:8000",
            "tools": [{"name": "shopping:add_item", "description": "Add item", "input_schema_json": "{}"}]
        });
        let resp = app
            .oneshot(
                Request::builder()
                    .method("POST")
                    .uri("/v1/register")
                    .header("content-type", "application/json")
                    .body(Body::from(body.to_string()))
                    .unwrap(),
            )
            .await
            .unwrap();
        assert_eq!(resp.status(), StatusCode::NO_CONTENT);
    }

    #[tokio::test]
    async fn test_register_then_tools_lists_tool() {
        let registry = Arc::new(ToolRegistry::new());
        let config = make_config();

        let register_body = serde_json::json!({
            "app_id": "shopping",
            "callback_url": "http://app:8000",
            "tools": [{"name": "shopping:add_item", "description": "Add item", "input_schema_json": "{}"}]
        });
        create_router(Arc::clone(&registry), Arc::clone(&config))
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

        let list_resp = create_router(Arc::clone(&registry), Arc::clone(&config))
            .oneshot(Request::builder().uri("/v1/tools").body(Body::empty()).unwrap())
            .await
            .unwrap();
        let list_body = list_resp.into_body().collect().await.unwrap().to_bytes();
        let tools: Vec<serde_json::Value> = serde_json::from_slice(&list_body).unwrap();
        assert_eq!(tools.len(), 1);
        assert_eq!(tools[0]["name"], "shopping:add_item");
        assert_eq!(tools[0]["app_id"], "shopping");
    }

    #[tokio::test]
    async fn test_notifications_provider_returns_config() {
        let registry = Arc::new(ToolRegistry::new());
        let app = create_router(Arc::clone(&registry), make_config());

        let resp = app
            .oneshot(
                Request::builder()
                    .uri("/v1/notifications/provider")
                    .body(Body::empty())
                    .unwrap(),
            )
            .await
            .unwrap();
        assert_eq!(resp.status(), StatusCode::OK);
        let body = resp.into_body().collect().await.unwrap().to_bytes();
        let json: serde_json::Value = serde_json::from_slice(&body).unwrap();
        assert_eq!(json["provider"], "ntfy");
        assert_eq!(json["base_url"], "https://ntfy.sh");
        assert_eq!(json["topic"], "test-topic");
    }
}
```

Add `pub mod router;` to `bridge/src/lib.rs`.

- [ ] **Step 2: Run to confirm tests fail**

```bash
cd /Users/ivanvladisavljevic/Projects/belgrade-os/.worktrees/foundation/bridge && cargo test router 2>&1
```
Expected: compilation fails or `not yet implemented` panics.

- [ ] **Step 3: Implement handle_register, handle_tools, handle_notifications_provider**

Replace the three `todo!()` handler bodies in `bridge/src/router.rs` (leave `handle_execute` as `todo!()` for now):

```rust
async fn handle_register(
    State(state): State<AppState>,
    Json(req): Json<RegisterRequest>,
) -> StatusCode {
    let registrations: Vec<ToolRegistration> = req
        .tools
        .into_iter()
        .map(|t| ToolRegistration {
            name: t.name,
            description: t.description,
            input_schema_json: t.input_schema_json,
        })
        .collect();
    state.registry.register(&req.app_id, &req.callback_url, &registrations);
    StatusCode::NO_CONTENT
}

async fn handle_tools(
    State(state): State<AppState>,
) -> Json<Vec<ToolResponse>> {
    let tools = state
        .registry
        .list()
        .into_iter()
        .map(|t| ToolResponse {
            name: t.name,
            description: t.description,
            input_schema_json: t.input_schema_json,
            app_id: t.app_id,
        })
        .collect();
    Json(tools)
}

async fn handle_notifications_provider(
    State(state): State<AppState>,
) -> Json<NotificationsProviderResponse> {
    Json(NotificationsProviderResponse {
        provider: "ntfy".to_string(),
        base_url: state.config.ntfy_base_url.clone(),
        topic: state.config.ntfy_topic.clone(),
    })
}
```

- [ ] **Step 4: Run router tests (excluding execute)**

```bash
cd /Users/ivanvladisavljevic/Projects/belgrade-os/.worktrees/foundation/bridge && cargo test router::tests::test_tools_empty_on_start router::tests::test_register_returns_204 router::tests::test_register_then_tools_lists_tool router::tests::test_notifications_provider_returns_config 2>&1
```
Expected: 4 tests pass.

- [ ] **Step 5: Commit**

```bash
cd /Users/ivanvladisavljevic/Projects/belgrade-os/.worktrees/foundation && git add bridge/src/router.rs bridge/src/lib.rs && git commit -m "feat(bridge): register + tools + notifications endpoints"
```

---

## Task 4: Execute endpoint

**Files:**
- Modify: `bridge/src/router.rs` (implement `handle_execute`, add execute tests)

- [ ] **Step 1: Write failing execute tests**

Add these tests to the `#[cfg(test)] mod tests` block at the bottom of `bridge/src/router.rs`:

```rust
    // --- execute tests (add inside the existing mod tests block) ---

    #[tokio::test]
    async fn test_execute_unknown_tool_returns_error() {
        let registry = Arc::new(ToolRegistry::new());
        let app = create_router(Arc::clone(&registry), make_config());

        let body = serde_json::json!({
            "call_id": "c1", "task_id": "t1",
            "tool_name": "unknown:tool",
            "input_json": "{}", "trace_id": "tr1"
        });
        let resp = app
            .oneshot(
                Request::builder()
                    .method("POST")
                    .uri("/v1/execute")
                    .header("content-type", "application/json")
                    .body(Body::from(body.to_string()))
                    .unwrap(),
            )
            .await
            .unwrap();
        assert_eq!(resp.status(), StatusCode::OK);
        let bytes = resp.into_body().collect().await.unwrap().to_bytes();
        let json: serde_json::Value = serde_json::from_slice(&bytes).unwrap();
        assert_eq!(json["success"], false);
        assert!(json["error"].as_str().unwrap().contains("tool not found"));
        assert_eq!(json["call_id"], "c1");
        assert_eq!(json["task_id"], "t1");
    }

    #[tokio::test]
    async fn test_execute_dispatches_to_callback_and_returns_result() {
        use wiremock::{matchers::{method, path}, Mock, MockServer, ResponseTemplate};

        let mock_server = MockServer::start().await;
        Mock::given(method("POST"))
            .and(path("/execute"))
            .respond_with(
                ResponseTemplate::new(200).set_body_json(serde_json::json!({
                    "success": true,
                    "output_json": "{\"added\":true}",
                    "error": ""
                })),
            )
            .mount(&mock_server)
            .await;

        let registry = Arc::new(ToolRegistry::new());
        registry.register(
            "shopping",
            &mock_server.uri(),
            &[ToolRegistration {
                name: "shopping:add_item".to_string(),
                description: "Add item".to_string(),
                input_schema_json: "{}".to_string(),
            }],
        );
        let app = create_router(Arc::clone(&registry), make_config());

        let body = serde_json::json!({
            "call_id": "c1", "task_id": "t1",
            "tool_name": "shopping:add_item",
            "input_json": "{\"item\":\"milk\"}", "trace_id": "tr1"
        });
        let resp = app
            .oneshot(
                Request::builder()
                    .method("POST")
                    .uri("/v1/execute")
                    .header("content-type", "application/json")
                    .body(Body::from(body.to_string()))
                    .unwrap(),
            )
            .await
            .unwrap();
        assert_eq!(resp.status(), StatusCode::OK);
        let bytes = resp.into_body().collect().await.unwrap().to_bytes();
        let json: serde_json::Value = serde_json::from_slice(&bytes).unwrap();
        assert!(json["success"].as_bool().unwrap());
        assert_eq!(json["call_id"], "c1");
        assert_eq!(json["task_id"], "t1");
        assert_eq!(json["output_json"], "{\"added\":true}");
    }

    #[tokio::test]
    async fn test_execute_callback_connection_error_returns_failed_result() {
        let registry = Arc::new(ToolRegistry::new());
        // port 1 — nothing listens there
        registry.register(
            "shopping",
            "http://127.0.0.1:1",
            &[ToolRegistration {
                name: "shopping:add_item".to_string(),
                description: "Add item".to_string(),
                input_schema_json: "{}".to_string(),
            }],
        );
        let app = create_router(Arc::clone(&registry), make_config());

        let body = serde_json::json!({
            "call_id": "c1", "task_id": "t1",
            "tool_name": "shopping:add_item",
            "input_json": "{}", "trace_id": "tr1"
        });
        let resp = app
            .oneshot(
                Request::builder()
                    .method("POST")
                    .uri("/v1/execute")
                    .header("content-type", "application/json")
                    .body(Body::from(body.to_string()))
                    .unwrap(),
            )
            .await
            .unwrap();
        assert_eq!(resp.status(), StatusCode::OK);
        let bytes = resp.into_body().collect().await.unwrap().to_bytes();
        let json: serde_json::Value = serde_json::from_slice(&bytes).unwrap();
        assert_eq!(json["success"], false);
        assert!(json["error"].as_str().unwrap().contains("dispatch error"));
        assert_eq!(json["call_id"], "c1");
        assert_eq!(json["task_id"], "t1");
    }

    #[tokio::test]
    async fn test_execute_forwards_correct_payload_to_callback() {
        use wiremock::{matchers::{body_json, method, path}, Mock, MockServer, ResponseTemplate};

        let mock_server = MockServer::start().await;
        Mock::given(method("POST"))
            .and(path("/execute"))
            .and(body_json(serde_json::json!({
                "tool_name": "shopping:add_item",
                "input_json": "{\"item\":\"milk\"}",
                "trace_id": "tr1"
            })))
            .respond_with(
                ResponseTemplate::new(200).set_body_json(serde_json::json!({
                    "success": true, "output_json": "{}", "error": ""
                })),
            )
            .mount(&mock_server)
            .await;

        let registry = Arc::new(ToolRegistry::new());
        registry.register(
            "shopping",
            &mock_server.uri(),
            &[ToolRegistration {
                name: "shopping:add_item".to_string(),
                description: "".to_string(),
                input_schema_json: "{}".to_string(),
            }],
        );
        let app = create_router(Arc::clone(&registry), make_config());

        let body = serde_json::json!({
            "call_id": "c1", "task_id": "t1",
            "tool_name": "shopping:add_item",
            "input_json": "{\"item\":\"milk\"}", "trace_id": "tr1"
        });
        let resp = app
            .oneshot(
                Request::builder()
                    .method("POST")
                    .uri("/v1/execute")
                    .header("content-type", "application/json")
                    .body(Body::from(body.to_string()))
                    .unwrap(),
            )
            .await
            .unwrap();
        // If wiremock's body_json matcher didn't match, it returns 404 (unmatched)
        assert_eq!(resp.status(), StatusCode::OK);
        let bytes = resp.into_body().collect().await.unwrap().to_bytes();
        let json: serde_json::Value = serde_json::from_slice(&bytes).unwrap();
        assert!(json["success"].as_bool().unwrap(), "wiremock body matcher failed — wrong payload sent");
    }
```

- [ ] **Step 2: Run to confirm execute tests fail**

```bash
cd /Users/ivanvladisavljevic/Projects/belgrade-os/.worktrees/foundation/bridge && cargo test router::tests::test_execute 2>&1
```
Expected: FAIL with `not yet implemented`

- [ ] **Step 3: Implement `handle_execute`**

Replace the `todo!()` body of `handle_execute` in `bridge/src/router.rs`:

```rust
async fn handle_execute(
    State(state): State<AppState>,
    Json(req): Json<ExecuteRequest>,
) -> Json<ExecuteResponse> {
    let tool = match state.registry.get(&req.tool_name) {
        Some(t) => t,
        None => {
            return Json(ExecuteResponse {
                call_id: req.call_id,
                task_id: req.task_id,
                success: false,
                output_json: String::new(),
                error: format!("tool not found: {}", req.tool_name),
            })
        }
    };

    let payload = serde_json::json!({
        "tool_name": req.tool_name,
        "input_json": req.input_json,
        "trace_id": req.trace_id,
    });

    let callback_url = format!("{}/execute", tool.callback_url);
    match state.http.post(&callback_url).json(&payload).send().await {
        Ok(resp) if resp.status().is_success() => {
            match resp.json::<serde_json::Value>().await {
                Ok(data) => Json(ExecuteResponse {
                    call_id: req.call_id,
                    task_id: req.task_id,
                    success: data["success"].as_bool().unwrap_or(false),
                    output_json: data["output_json"].as_str().unwrap_or("").to_string(),
                    error: data["error"].as_str().unwrap_or("").to_string(),
                }),
                Err(e) => Json(ExecuteResponse {
                    call_id: req.call_id,
                    task_id: req.task_id,
                    success: false,
                    output_json: String::new(),
                    error: format!("parse error: {e}"),
                }),
            }
        }
        Ok(resp) => Json(ExecuteResponse {
            call_id: req.call_id,
            task_id: req.task_id,
            success: false,
            output_json: String::new(),
            error: format!("app error: HTTP {}", resp.status()),
        }),
        Err(e) => Json(ExecuteResponse {
            call_id: req.call_id,
            task_id: req.task_id,
            success: false,
            output_json: String::new(),
            error: format!("dispatch error: {e}"),
        }),
    }
}
```

- [ ] **Step 4: Run execute tests to confirm they pass**

```bash
cd /Users/ivanvladisavljevic/Projects/belgrade-os/.worktrees/foundation/bridge && cargo test router::tests::test_execute 2>&1
```
Expected: 4 execute tests pass.

- [ ] **Step 5: Run full test suite**

```bash
cd /Users/ivanvladisavljevic/Projects/belgrade-os/.worktrees/foundation/bridge && cargo test 2>&1 | tail -5
```
Expected: `test result: ok. 23 passed` (8 proto + 2 config + 5 registry + 8 router)

- [ ] **Step 6: Commit**

```bash
cd /Users/ivanvladisavljevic/Projects/belgrade-os/.worktrees/foundation && git add bridge/src/router.rs && git commit -m "feat(bridge): POST /v1/execute — dispatch to app callback"
```

---

## Task 5: Main entry point

**Files:**
- Create: `bridge/src/main.rs`

`main.rs` loads config, creates the registry, builds the router, and binds the TCP listener. `lib.rs` is not touched — it remains the proto library.

- [ ] **Step 1: Create `bridge/src/main.rs`**

```rust
// bridge/src/main.rs
mod config;
mod registry;
mod router;

use std::sync::Arc;

#[tokio::main]
async fn main() {
    tracing_subscriber::fmt()
        .with_env_filter(
            tracing_subscriber::EnvFilter::try_from_default_env()
                .unwrap_or_else(|_| "info".into()),
        )
        .init();

    let cfg = Arc::new(config::Config::from_env());
    let registry = Arc::new(registry::ToolRegistry::new());
    let addr = format!("0.0.0.0:{}", cfg.port);

    let app = router::create_router(Arc::clone(&registry), Arc::clone(&cfg));

    tracing::info!(port = cfg.port, "capability bridge listening");
    let listener = tokio::net::TcpListener::bind(&addr)
        .await
        .expect("failed to bind");
    axum::serve(listener, app).await.expect("server error");
}
```

- [ ] **Step 2: Build to confirm it compiles**

```bash
cd /Users/ivanvladisavljevic/Projects/belgrade-os/.worktrees/foundation/bridge && cargo build 2>&1 | tail -5
```
Expected: `Finished` with no errors.

- [ ] **Step 3: Run full test suite**

```bash
cd /Users/ivanvladisavljevic/Projects/belgrade-os/.worktrees/foundation/bridge && cargo test 2>&1 | tail -5
```
Expected: all tests pass (build does not break existing tests).

- [ ] **Step 4: Smoke test the binary**

```bash
cd /Users/ivanvladisavljevic/Projects/belgrade-os/.worktrees/foundation/bridge && cargo run &
sleep 2
curl -s http://localhost:8081/v1/tools
kill %1
```
Expected: `[]` (empty JSON array).

- [ ] **Step 5: Commit**

```bash
cd /Users/ivanvladisavljevic/Projects/belgrade-os/.worktrees/foundation && git add bridge/src/main.rs && git commit -m "feat(bridge): main entry point — TCP bind + server startup"
```

---

## Self-Review

**1. Spec coverage:**
- [x] `POST /v1/register` — apps register tools + callback URL → 204
- [x] `POST /v1/execute` — look up tool → dispatch to callback → return result
- [x] Unknown tool → `success=false, error="tool not found: ..."`
- [x] Callback connection error → `success=false, error="dispatch error: ..."`
- [x] Callback HTTP non-2xx → `success=false, error="app error: HTTP ..."`
- [x] `GET /v1/tools` — return all registered tools as JSON array
- [x] `GET /v1/notifications/provider` — return ntfy config from env
- [x] Re-registration replaces all tools for that app_id (isolation)
- [x] All endpoints under `/v1/` for versioning
- [x] Extendability: adding a new endpoint = new route in `create_router` + handler fn; no structural changes needed
- [x] Config: PORT (default 8081), NTFY_BASE_URL, NTFY_TOPIC from env
- [x] Binary compiles and binds port

**2. Placeholder scan:** None.

**3. Type consistency:**
- `ToolRegistration` — defined in `registry.rs`, used in Task 3 handler and Task 4 tests ✓
- `RegisteredTool` — defined in `registry.rs`, returned by `get()`/`list()`, consumed in `handle_tools` and `handle_execute` ✓
- `create_router(Arc<ToolRegistry>, Arc<Config>) -> Router` — defined in Task 3, called in Task 5 `main.rs` ✓
- `ExecuteResponse` derives both `Serialize` and `Deserialize` — needed for wiremock `body_json` test ✓
- `AppState` — defined in Task 3, used in all handlers ✓
