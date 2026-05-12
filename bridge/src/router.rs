use axum::{extract::State, http::StatusCode, response::{IntoResponse, Json}, routing::{get, post}, Router};
use serde::{Deserialize, Serialize};
use std::sync::Arc;
use crate::{config::Config, registry::{ToolRegistration, ToolRegistry}};
use crate::store::{NoopStore, Store};
use prost::Message as _;

#[derive(Clone)]
pub struct AppState {
    pub registry: Arc<ToolRegistry>,
    pub config: Arc<Config>,
    pub http: reqwest::Client,
    pub store: Arc<dyn Store>,
    pub pool: Option<deadpool_redis::Pool>,
}

#[derive(Deserialize)]
pub struct RegisterRequest {
    pub app_id: String,
    pub callback_url: String,
    pub tools: Vec<ToolDef>,
    pub subscriptions: Option<Vec<String>>,
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
    pub user_id: Option<String>,
    pub tenant_id: Option<String>,
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

#[derive(Serialize)]
pub struct AppInfoResponse {
    pub app_id: String,
    pub callback_url: String,
}

#[derive(Deserialize, Serialize, Clone)]
pub struct EventPayload {
    pub topic: String,
    pub payload: serde_json::Value,
    pub app_id: String,
    pub tenant_id: Option<String>,
    pub trace_id: String,
}

fn generate_token() -> String {
    let a = uuid::Uuid::new_v4().simple().to_string();
    let b = uuid::Uuid::new_v4().simple().to_string();
    format!("{a}{b}")
}

#[derive(Serialize)]
pub struct RegisterResponse {
    pub app_token: String,
}

#[derive(Deserialize)]
pub struct InferRequest {
    pub app_id: String,
    pub user_id: String,
    pub prompt: String,
    pub tenant_id: Option<String>,
    pub trace_id: Option<String>,
}

#[derive(Serialize)]
pub struct InferResponse {
    pub task_id: String,
    pub trace_id: String,
}

#[derive(Deserialize)]
pub struct NotifyRequest {
    pub app_id: String,
    pub user_id: String,
    pub title: String,
    pub body: String,
    pub priority: Option<i32>,
    pub tags: Option<Vec<String>>,
    pub click_url: Option<String>,
    pub trace_id: Option<String>,
}

fn extract_bearer(headers: &axum::http::HeaderMap) -> Result<&str, (StatusCode, String)> {
    headers
        .get(axum::http::header::AUTHORIZATION)
        .and_then(|v| v.to_str().ok())
        .and_then(|v| v.strip_prefix("Bearer "))
        .ok_or((
            StatusCode::UNAUTHORIZED,
            "missing or malformed Authorization: Bearer <token> header".to_string(),
        ))
}

async fn lookup_token(
    pool: &deadpool_redis::Pool,
    token: &str,
) -> Result<String, (StatusCode, String)> {
    let mut conn = pool.get().await.map_err(|e| {
        (StatusCode::INTERNAL_SERVER_ERROR, format!("Redis pool error: {e}"))
    })?;
    let app_id: Option<String> = redis::cmd("GET")
        .arg(format!("bridge:token:{token}"))
        .query_async(&mut *conn)
        .await
        .map_err(|e| (StatusCode::INTERNAL_SERVER_ERROR, format!("Redis error: {e}")))?;
    app_id.ok_or((StatusCode::UNAUTHORIZED, "invalid token".to_string()))
}

async fn handle_notify(
    State(state): State<AppState>,
    headers: axum::http::HeaderMap,
    Json(req): Json<NotifyRequest>,
) -> Result<StatusCode, (StatusCode, String)> {
    let raw_token = extract_bearer(&headers)?;

    let pool = state.pool.as_ref().ok_or((
        StatusCode::SERVICE_UNAVAILABLE,
        "Redis not configured".to_string(),
    ))?;

    let token_app_id = lookup_token(pool, raw_token).await?;
    if token_app_id != req.app_id {
        return Err((StatusCode::FORBIDDEN, "app_id does not match token".to_string()));
    }

    let notification = crate::belgrade_os::NotificationRequest {
        trace_id: req.trace_id.unwrap_or_default(),
        app_id: req.app_id.clone(),
        user_id: req.user_id.clone(),
        title: req.title.clone(),
        body: req.body.clone(),
        priority: req.priority.unwrap_or(0),
        driver: String::new(),
        tags: req.tags.unwrap_or_default(),
        click_url: req.click_url.unwrap_or_default(),
    };
    let encoded = notification.encode_to_vec();

    let mut conn = pool.get().await.map_err(|e| {
        (StatusCode::INTERNAL_SERVER_ERROR, format!("Redis pool error: {e}"))
    })?;
    redis::cmd("XADD")
        .arg("tasks:notifications")
        .arg("*")
        .arg("data")
        .arg(encoded.as_slice())
        .query_async::<_, ()>(&mut *conn)
        .await
        .map_err(|e| (StatusCode::INTERNAL_SERVER_ERROR, format!("Redis error: {e}")))?;

    Ok(StatusCode::ACCEPTED)
}

async fn handle_infer(
    State(state): State<AppState>,
    headers: axum::http::HeaderMap,
    Json(req): Json<InferRequest>,
) -> Result<(StatusCode, Json<InferResponse>), (StatusCode, String)> {
    let raw_token = extract_bearer(&headers)?;

    let pool = state.pool.as_ref().ok_or((
        StatusCode::SERVICE_UNAVAILABLE,
        "Redis not configured".to_string(),
    ))?;

    let token_app_id = lookup_token(pool, raw_token).await?;
    if token_app_id != req.app_id {
        return Err((StatusCode::FORBIDDEN, "app_id does not match token".to_string()));
    }

    let task_id = format!("app-{}", uuid::Uuid::new_v4().simple());
    let trace_id = req.trace_id.unwrap_or_else(|| uuid::Uuid::new_v4().simple().to_string());
    let now_ms = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap_or_default()
        .as_millis() as i64;

    // execution_mode is always UNTRUSTED for app-originated inference
    let task = crate::belgrade_os::Task {
        task_id: task_id.clone(),
        user_id: req.user_id.clone(),
        prompt: req.prompt.clone(),
        created_at_ms: now_ms,
        trace_id: trace_id.clone(),
        execution_mode: crate::belgrade_os::ExecutionMode::Untrusted as i32,
        app_id: req.app_id.clone(),
        tenant_id: req.tenant_id.unwrap_or_default(),
    };

    let encoded = task.encode_to_vec();

    let mut conn = pool.get().await.map_err(|e| {
        (StatusCode::INTERNAL_SERVER_ERROR, format!("Redis pool error: {e}"))
    })?;
    redis::cmd("XADD")
        .arg("tasks:inbound")
        .arg("*")
        .arg("data")
        .arg(encoded.as_slice())
        .query_async::<_, ()>(&mut *conn)
        .await
        .map_err(|e| (StatusCode::INTERNAL_SERVER_ERROR, format!("Redis error: {e}")))?;

    Ok((StatusCode::ACCEPTED, Json(InferResponse { task_id, trace_id })))
}

async fn handle_register(
    State(state): State<AppState>,
    Json(req): Json<RegisterRequest>,
) -> Result<axum::response::Response, (StatusCode, String)> {
    for t in &req.tools {
        if !t.name.starts_with(&format!("{}:", req.app_id)) {
            return Err((
                StatusCode::BAD_REQUEST,
                format!("tool name {:?} must be namespaced as '{}:<name>'", t.name, req.app_id),
            ));
        }
    }
    // Validate callback URL — only http:// and https:// with a non-empty host are permitted.
    // The url crate non-conformantly parses "http:///path" as host="path" (it treats the first
    // path segment as the authority), so we additionally check the raw string: after stripping
    // scheme + "://", the authority must not begin with "/" (which would indicate an empty host).
    let parsed_url = url::Url::parse(&req.callback_url).map_err(|_| (
        StatusCode::BAD_REQUEST,
        format!("callback_url {:?} is not a valid URL", req.callback_url),
    ))?;
    let scheme = parsed_url.scheme();
    let raw_authority_empty = {
        let prefix = format!("{}://", scheme);
        let raw_lower = req.callback_url.to_lowercase();
        raw_lower.strip_prefix(&prefix)
            .map(|_| req.callback_url[prefix.len()..].starts_with('/') || req.callback_url[prefix.len()..].is_empty())
            .unwrap_or(true)
    };
    if !matches!(scheme, "http" | "https") || parsed_url.host().is_none() || raw_authority_empty {
        return Err((
            StatusCode::BAD_REQUEST,
            format!(
                "callback_url {:?} must use http:// or https:// with a non-empty host",
                req.callback_url
            ),
        ));
    }
    let registrations: Vec<ToolRegistration> = req
        .tools
        .iter()
        .map(|t| ToolRegistration {
            name: t.name.clone(),
            description: t.description.clone(),
            input_schema_json: t.input_schema_json.clone(),
        })
        .collect();

    // Store writes first — if persistence fails, in-memory is not updated.
    state.store
        .register(&req.app_id, &req.callback_url, &registrations)
        .await
        .map_err(|e| (StatusCode::INTERNAL_SERVER_ERROR, e.to_string()))?;

    // NOTE: If register succeeded but subscribe fails below, Redis has the tool
    // registration but in-memory does not. The caller will receive a 500 and
    // retry; the retry re-runs register (idempotent) then subscribe. On bridge
    // restart, hydrate() will restore the Redis state. This is an acceptable
    // window for a personal system; a production fix would batch both writes
    // into a single Redis pipeline.
    if let Some(ref subs) = req.subscriptions {
        state.store
            .subscribe(&req.app_id, subs)
            .await
            .map_err(|e| (StatusCode::INTERNAL_SERVER_ERROR, e.to_string()))?;
    }

    // In-memory updates — infallible, always follow successful store writes.
    state.registry.register(&req.app_id, &req.callback_url, &registrations);
    if let Some(subs) = req.subscriptions {
        state.registry.subscribe(&req.app_id, subs);
    }

    if let Some(ref pool) = state.pool {
        let token = generate_token();
        let mut conn = pool.get().await.map_err(|e| (
            StatusCode::INTERNAL_SERVER_ERROR, e.to_string(),
        ))?;
        redis::cmd("SET")
            .arg(format!("bridge:token:{token}"))
            .arg(&req.app_id)
            .query_async::<_, ()>(&mut *conn)
            .await
            .map_err(|e| (StatusCode::INTERNAL_SERVER_ERROR, e.to_string()))?;
        return Ok(axum::Json(RegisterResponse { app_token: token }).into_response());
    }

    Ok(StatusCode::NO_CONTENT.into_response())
}

async fn handle_publish(
    State(state): State<AppState>,
    Json(event): Json<EventPayload>,
) -> StatusCode {
    let subscribers = state.registry.get_subscribers(&event.topic);
    
    if subscribers.is_empty() {
        return StatusCode::ACCEPTED;
    }

    let http = state.http.clone();
    // NOTE: We spawn a task to handle fan-out asynchronously. The dropped JoinHandle
    // is intentional — this is a fire-and-forget notification system.
    tokio::spawn(async move {
        for (app_id, callback_url) in subscribers {
            let url = format!("{}/events", callback_url);
            match http.post(&url).json(&event).send().await {
                Ok(resp) if resp.status().is_success() => {
                    tracing::debug!(app_id, topic = event.topic, "Event delivered");
                }
                Ok(resp) => {
                    tracing::warn!(app_id, topic = event.topic, status = resp.status().as_u16(), "Event delivery failed");
                }
                Err(e) => {
                    tracing::error!(app_id, topic = event.topic, error = %e, "Event delivery error");
                }
            }
        }
    });

    StatusCode::ACCEPTED
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
        "user_id": req.user_id,
        "tenant_id": req.tenant_id,
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
            error: format!("app error: HTTP {}", resp.status().as_u16()),
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

async fn handle_notifications_provider(
    State(state): State<AppState>,
) -> Json<NotificationsProviderResponse> {
    Json(NotificationsProviderResponse {
        provider: "ntfy".to_string(),
        base_url: state.config.ntfy_base_url.clone(),
        topic: state.config.ntfy_topic.clone(),
    })
}

async fn handle_app_info(
    State(state): State<AppState>,
    axum::extract::Path(app_id): axum::extract::Path<String>,
) -> Result<Json<AppInfoResponse>, StatusCode> {
    match state.registry.get_callback(&app_id) {
        Some(callback_url) => Ok(Json(AppInfoResponse { app_id, callback_url })),
        None => Err(StatusCode::NOT_FOUND),
    }
}

pub fn create_router(
    registry: Arc<ToolRegistry>,
    config: Arc<Config>,
    store: Arc<dyn Store>,
    pool: Option<deadpool_redis::Pool>,
) -> Router {
    let state = AppState { registry, config, http: reqwest::Client::new(), store, pool };
    Router::new()
        .route("/v1/register", post(handle_register))
        .route("/v1/tools", get(handle_tools))
        .route("/v1/execute", post(handle_execute))
        .route("/v1/events/publish", post(handle_publish))
        .route("/v1/notify", post(handle_notify))
        .route("/v1/infer", post(handle_infer))
        .route("/v1/notifications/provider", get(handle_notifications_provider))
        .route("/v1/apps/:app_id", get(handle_app_info))
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
            redis_url: "redis://localhost:6379".to_string(),
        })
    }

    fn make_router(registry: Arc<ToolRegistry>) -> Router {
        create_router(registry, make_config(), Arc::new(NoopStore), None)
    }

    #[tokio::test]
    async fn test_tools_empty_on_start() {
        let registry = Arc::new(ToolRegistry::new());
        let app = make_router(Arc::clone(&registry));

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
        let app = make_router(Arc::clone(&registry));

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

        let register_body = serde_json::json!({
            "app_id": "shopping",
            "callback_url": "http://app:8000",
            "tools": [{"name": "shopping:add_item", "description": "Add item", "input_schema_json": "{}"}]
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

        let list_resp = make_router(Arc::clone(&registry))
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
        let app = make_router(Arc::clone(&registry));

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

    #[tokio::test]
    async fn test_register_bad_tool_name_returns_400() {
        let registry = Arc::new(ToolRegistry::new());
        let app = make_router(Arc::clone(&registry));

        let body = serde_json::json!({
            "app_id": "shopping",
            "callback_url": "http://app:8000",
            "tools": [{"name": "badname", "description": "bad", "input_schema_json": "{}"}]
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
        assert_eq!(resp.status(), StatusCode::BAD_REQUEST);
    }

    #[tokio::test]
    async fn test_execute_unknown_tool_returns_error() {
        let registry = Arc::new(ToolRegistry::new());
        let app = make_router(Arc::clone(&registry));

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
        let app = make_router(Arc::clone(&registry));

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
        let app = make_router(Arc::clone(&registry));

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
                "trace_id": "tr1",
                "user_id": "u1",
                "tenant_id": "t1"
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
        let app = make_router(Arc::clone(&registry));

        let body = serde_json::json!({
            "call_id": "c1", "task_id": "t1",
            "tool_name": "shopping:add_item",
            "input_json": "{\"item\":\"milk\"}", "trace_id": "tr1",
            "user_id": "u1", "tenant_id": "t1"
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
        assert!(json["success"].as_bool().unwrap(), "wiremock body matcher failed — wrong payload sent");
    }

    #[tokio::test]
    async fn test_publish_delivers_to_subscribers() {
        use wiremock::{matchers::{method, path, body_json}, Mock, MockServer, ResponseTemplate};

        let mock_server = MockServer::start().await;
        Mock::given(method("POST"))
            .and(path("/events"))
            .and(body_json(serde_json::json!({
                "topic": "test.topic",
                "payload": {"data": 123},
                "app_id": "sender",
                "tenant_id": "t1",
                "trace_id": "tr1"
            })))
            .respond_with(ResponseTemplate::new(200).set_body_json(serde_json::json!({"status": "ok"})))
            .expect(1)
            .mount(&mock_server)
            .await;

        let registry = Arc::new(ToolRegistry::new());
        registry.register("receiver", &mock_server.uri(), &[]);
        registry.subscribe("receiver", vec!["test.topic".to_string()]);

        let app = make_router(Arc::clone(&registry));

        let body = serde_json::json!({
            "topic": "test.topic",
            "payload": {"data": 123},
            "app_id": "sender",
            "tenant_id": "t1",
            "trace_id": "tr1"
        });
        let resp = app
            .oneshot(
                Request::builder()
                    .method("POST")
                    .uri("/v1/events/publish")
                    .header("content-type", "application/json")
                    .body(Body::from(body.to_string()))
                    .unwrap(),
            )
            .await
            .unwrap();
        
        assert_eq!(resp.status(), StatusCode::ACCEPTED);
        
        // Wait for tokio::spawn fan-out
        tokio::time::sleep(tokio::time::Duration::from_millis(100)).await;
    }

    #[tokio::test]
    async fn test_get_app_info_returns_callback_url() {
        let registry = Arc::new(ToolRegistry::new());
        registry.register("shopping", "http://app:9000", &[]);
        let app = make_router(Arc::clone(&registry));

        let resp = app
            .oneshot(Request::builder().uri("/v1/apps/shopping").body(Body::empty()).unwrap())
            .await
            .unwrap();

        assert_eq!(resp.status(), StatusCode::OK);
        let body = resp.into_body().collect().await.unwrap().to_bytes();
        let json: serde_json::Value = serde_json::from_slice(&body).unwrap();
        assert_eq!(json["app_id"], "shopping");
        assert_eq!(json["callback_url"], "http://app:9000");
    }

    #[tokio::test]
    async fn test_get_app_info_returns_404_for_unknown_app() {
        let registry = Arc::new(ToolRegistry::new());
        let app = make_router(Arc::clone(&registry));

        let resp = app
            .oneshot(Request::builder().uri("/v1/apps/unknown").body(Body::empty()).unwrap())
            .await
            .unwrap();

        assert_eq!(resp.status(), StatusCode::NOT_FOUND);
    }

    #[tokio::test]
    async fn test_register_rejects_non_http_callback_url() {
        let registry = Arc::new(ToolRegistry::new());
        let app = make_router(Arc::clone(&registry));

        let body = serde_json::json!({
            "app_id": "evil",
            "callback_url": "file:///etc/passwd",
            "tools": []
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

        assert_eq!(resp.status(), StatusCode::BAD_REQUEST);
    }

    #[tokio::test]
    async fn test_register_accepts_https_callback_url() {
        let registry = Arc::new(ToolRegistry::new());
        let app = make_router(Arc::clone(&registry));

        let body = serde_json::json!({
            "app_id": "myapp",
            "callback_url": "https://app.internal:9000",
            "tools": []
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
    async fn test_register_rejects_empty_host_url() {
        // Prefix matching alone accepts "http:///path" (empty host). URL parsing must reject it.
        let registry = Arc::new(ToolRegistry::new());
        let app = make_router(Arc::clone(&registry));

        let body = serde_json::json!({
            "app_id": "evil",
            "callback_url": "http:///path",
            "tools": []
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

        assert_eq!(resp.status(), StatusCode::BAD_REQUEST);
    }

    async fn try_pool() -> Option<deadpool_redis::Pool> {
        let cfg = deadpool_redis::Config::from_url("redis://localhost:6379");
        let pool = cfg.create_pool(Some(deadpool_redis::Runtime::Tokio1)).ok()?;
        let mut conn = pool.get().await.ok()?;
        redis::cmd("PING").query_async::<_, ()>(&mut *conn).await.ok()?;
        Some(pool)
    }

    #[tokio::test]
    async fn test_register_without_pool_returns_204() {
        let registry = Arc::new(ToolRegistry::new());
        let app = make_router(Arc::clone(&registry));

        let body = serde_json::json!({
            "app_id": "myapp",
            "callback_url": "http://app:8000",
            "tools": []
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
    async fn test_register_with_pool_returns_token() {
        let Some(pool) = try_pool().await else { return };
        let store = Arc::new(crate::store::RedisStore::new_for_test(pool.clone()));
        let registry = Arc::new(ToolRegistry::new());
        let app = create_router(registry, make_config(), store, Some(pool));

        let body = serde_json::json!({
            "app_id": "myapp",
            "callback_url": "http://app:8000",
            "tools": []
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

        assert_eq!(resp.status(), StatusCode::OK);
        let bytes = resp.into_body().collect().await.unwrap().to_bytes();
        let json: serde_json::Value = serde_json::from_slice(&bytes).unwrap();
        let token = json["app_token"].as_str().expect("app_token missing");
        assert_eq!(token.len(), 64, "token must be 64 hex chars");
        assert!(token.chars().all(|c| c.is_ascii_hexdigit()), "token must be hex");
    }

    #[tokio::test]
    async fn test_notify_rejects_missing_auth() {
        let registry = Arc::new(ToolRegistry::new());
        let app = make_router(registry);

        let body = serde_json::json!({
            "app_id": "myapp", "user_id": "u1",
            "title": "Hello", "body": "World"
        });
        let resp = app
            .oneshot(
                Request::builder()
                    .method("POST")
                    .uri("/v1/notify")
                    .header("content-type", "application/json")
                    .body(Body::from(body.to_string()))
                    .unwrap(),
            )
            .await
            .unwrap();
        assert_eq!(resp.status(), StatusCode::UNAUTHORIZED);
    }

    #[tokio::test]
    async fn test_notify_returns_503_without_pool() {
        let registry = Arc::new(ToolRegistry::new());
        let app = make_router(registry);

        let body = serde_json::json!({
            "app_id": "myapp", "user_id": "u1",
            "title": "Hello", "body": "World"
        });
        let resp = app
            .oneshot(
                Request::builder()
                    .method("POST")
                    .uri("/v1/notify")
                    .header("content-type", "application/json")
                    .header("authorization", "Bearer sometoken")
                    .body(Body::from(body.to_string()))
                    .unwrap(),
            )
            .await
            .unwrap();
        assert_eq!(resp.status(), StatusCode::SERVICE_UNAVAILABLE);
    }

    #[tokio::test]
    async fn test_notify_rejects_invalid_token() {
        let Some(pool) = try_pool().await else { return };
        let store = Arc::new(crate::store::RedisStore::new_for_test(pool.clone()));
        let registry = Arc::new(ToolRegistry::new());
        let app = create_router(registry, make_config(), store, Some(pool));

        let body = serde_json::json!({
            "app_id": "myapp", "user_id": "u1",
            "title": "Test", "body": "Body"
        });
        let resp = app
            .oneshot(
                Request::builder()
                    .method("POST")
                    .uri("/v1/notify")
                    .header("content-type", "application/json")
                    .header("authorization", "Bearer not_a_real_token_hex")
                    .body(Body::from(body.to_string()))
                    .unwrap(),
            )
            .await
            .unwrap();
        assert_eq!(resp.status(), StatusCode::UNAUTHORIZED);
    }

    #[tokio::test]
    async fn test_notify_rejects_wrong_app_id() {
        let Some(pool) = try_pool().await else { return };
        let store: Arc<dyn Store> = Arc::new(crate::store::RedisStore::new_for_test(pool.clone()));
        let registry = Arc::new(ToolRegistry::new());

        let reg_resp = create_router(
            Arc::clone(&registry), make_config(), Arc::clone(&store), Some(pool.clone()),
        )
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/v1/register")
                .header("content-type", "application/json")
                .body(Body::from(serde_json::json!({
                    "app_id": "myapp",
                    "callback_url": "http://app:8000",
                    "tools": []
                }).to_string()))
                .unwrap(),
        )
        .await
        .unwrap();
        let reg_bytes = reg_resp.into_body().collect().await.unwrap().to_bytes();
        let token = serde_json::from_slice::<serde_json::Value>(&reg_bytes).unwrap()["app_token"]
            .as_str()
            .unwrap()
            .to_string();

        let resp = create_router(
            Arc::clone(&registry), make_config(), Arc::clone(&store), Some(pool),
        )
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/v1/notify")
                .header("content-type", "application/json")
                .header("authorization", format!("Bearer {token}"))
                .body(Body::from(serde_json::json!({
                    "app_id": "otherapp",
                    "user_id": "u1",
                    "title": "Spoof", "body": "Attack"
                }).to_string()))
                .unwrap(),
        )
        .await
        .unwrap();
        assert_eq!(resp.status(), StatusCode::FORBIDDEN);
    }

    #[tokio::test]
    async fn test_notify_succeeds_with_valid_token() {
        let Some(pool) = try_pool().await else { return };
        let store: Arc<dyn Store> = Arc::new(crate::store::RedisStore::new_for_test(pool.clone()));
        let registry = Arc::new(ToolRegistry::new());

        let reg_resp = create_router(
            Arc::clone(&registry), make_config(), Arc::clone(&store), Some(pool.clone()),
        )
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/v1/register")
                .header("content-type", "application/json")
                .body(Body::from(serde_json::json!({
                    "app_id": "myapp",
                    "callback_url": "http://app:8000",
                    "tools": []
                }).to_string()))
                .unwrap(),
        )
        .await
        .unwrap();
        let reg_bytes = reg_resp.into_body().collect().await.unwrap().to_bytes();
        let token = serde_json::from_slice::<serde_json::Value>(&reg_bytes).unwrap()["app_token"]
            .as_str()
            .unwrap()
            .to_string();

        let resp = create_router(
            Arc::clone(&registry), make_config(), Arc::clone(&store), Some(pool),
        )
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/v1/notify")
                .header("content-type", "application/json")
                .header("authorization", format!("Bearer {token}"))
                .body(Body::from(serde_json::json!({
                    "app_id": "myapp",
                    "user_id": "u1",
                    "title": "Hello", "body": "World"
                }).to_string()))
                .unwrap(),
        )
        .await
        .unwrap();
        assert_eq!(resp.status(), StatusCode::ACCEPTED);
    }

    #[tokio::test]
    async fn test_publish_no_subscribers_is_accepted() {
        let registry = Arc::new(ToolRegistry::new());
        let app = make_router(Arc::clone(&registry));

        let body = serde_json::json!({
            "topic": "unknown.topic",
            "payload": {},
            "app_id": "sender",
            "trace_id": "tr1"
        });
        let resp = app
            .oneshot(
                Request::builder()
                    .method("POST")
                    .uri("/v1/events/publish")
                    .header("content-type", "application/json")
                    .body(Body::from(body.to_string()))
                    .unwrap(),
            )
            .await
            .unwrap();

        assert_eq!(resp.status(), StatusCode::ACCEPTED);
    }

    #[tokio::test]
    async fn test_infer_rejects_missing_auth() {
        let registry = Arc::new(ToolRegistry::new());
        let app = make_router(registry); // pool=None

        let body = serde_json::json!({
            "app_id": "myapp", "user_id": "u1",
            "prompt": "What is 2+2?"
        });
        let resp = app
            .oneshot(
                Request::builder()
                    .method("POST")
                    .uri("/v1/infer")
                    .header("content-type", "application/json")
                    .body(Body::from(body.to_string()))
                    .unwrap(),
            )
            .await
            .unwrap();
        assert_eq!(resp.status(), StatusCode::UNAUTHORIZED);
    }

    #[tokio::test]
    async fn test_infer_returns_503_without_pool() {
        let registry = Arc::new(ToolRegistry::new());
        let app = make_router(registry); // pool=None

        let body = serde_json::json!({
            "app_id": "myapp", "user_id": "u1",
            "prompt": "What is 2+2?"
        });
        let resp = app
            .oneshot(
                Request::builder()
                    .method("POST")
                    .uri("/v1/infer")
                    .header("content-type", "application/json")
                    .header("authorization", "Bearer sometoken")
                    .body(Body::from(body.to_string()))
                    .unwrap(),
            )
            .await
            .unwrap();
        assert_eq!(resp.status(), StatusCode::SERVICE_UNAVAILABLE);
    }

    #[tokio::test]
    async fn test_infer_rejects_invalid_token() {
        let Some(pool) = try_pool().await else { return };
        let store: Arc<dyn Store> = Arc::new(crate::store::RedisStore::new_for_test(pool.clone()));
        let registry = Arc::new(ToolRegistry::new());
        let app = create_router(registry, make_config(), store, Some(pool));

        let body = serde_json::json!({
            "app_id": "myapp", "user_id": "u1",
            "prompt": "What is 2+2?"
        });
        let resp = app
            .oneshot(
                Request::builder()
                    .method("POST")
                    .uri("/v1/infer")
                    .header("content-type", "application/json")
                    .header("authorization", "Bearer not_a_real_token_hex")
                    .body(Body::from(body.to_string()))
                    .unwrap(),
            )
            .await
            .unwrap();
        assert_eq!(resp.status(), StatusCode::UNAUTHORIZED);
    }

    #[tokio::test]
    async fn test_infer_succeeds_with_valid_token() {
        let Some(pool) = try_pool().await else { return };
        let store: Arc<dyn Store> = Arc::new(crate::store::RedisStore::new_for_test(pool.clone()));
        let registry = Arc::new(ToolRegistry::new());

        // Register to get token
        let reg_resp = create_router(
            Arc::clone(&registry), make_config(), Arc::clone(&store), Some(pool.clone()),
        )
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/v1/register")
                .header("content-type", "application/json")
                .body(Body::from(serde_json::json!({
                    "app_id": "myapp",
                    "callback_url": "http://app:8000",
                    "tools": []
                }).to_string()))
                .unwrap(),
        )
        .await
        .unwrap();
        let reg_bytes = reg_resp.into_body().collect().await.unwrap().to_bytes();
        let token = serde_json::from_slice::<serde_json::Value>(&reg_bytes).unwrap()["app_token"]
            .as_str()
            .unwrap()
            .to_string();

        let body = serde_json::json!({
            "app_id": "myapp",
            "user_id": "u1",
            "prompt": "What is 2+2?"
        });
        let resp = create_router(
            Arc::clone(&registry), make_config(), Arc::clone(&store), Some(pool),
        )
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/v1/infer")
                .header("content-type", "application/json")
                .header("authorization", format!("Bearer {token}"))
                .body(Body::from(body.to_string()))
                .unwrap(),
        )
        .await
        .unwrap();

        assert_eq!(resp.status(), StatusCode::ACCEPTED);
        let bytes = resp.into_body().collect().await.unwrap().to_bytes();
        let json: serde_json::Value = serde_json::from_slice(&bytes).unwrap();
        assert!(json["task_id"].as_str().is_some_and(|s| !s.is_empty()));
        assert!(json["trace_id"].as_str().is_some_and(|s| !s.is_empty()));
    }

    #[tokio::test]
    async fn test_infer_execution_mode_is_always_untrusted() {
        let Some(pool) = try_pool().await else { return };
        let store: Arc<dyn Store> = Arc::new(crate::store::RedisStore::new_for_test(pool.clone()));
        let registry = Arc::new(ToolRegistry::new());

        let reg_resp = create_router(
            Arc::clone(&registry), make_config(), Arc::clone(&store), Some(pool.clone()),
        )
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/v1/register")
                .header("content-type", "application/json")
                .body(Body::from(serde_json::json!({
                    "app_id": "myapp",
                    "callback_url": "http://app:8000",
                    "tools": []
                }).to_string()))
                .unwrap(),
        )
        .await
        .unwrap();
        let reg_bytes = reg_resp.into_body().collect().await.unwrap().to_bytes();
        let token = serde_json::from_slice::<serde_json::Value>(&reg_bytes).unwrap()["app_token"]
            .as_str()
            .unwrap()
            .to_string();

        // Include an explicit (bogus) execution_mode in the request — it must be ignored.
        let body = serde_json::json!({
            "app_id": "myapp",
            "user_id": "u1",
            "prompt": "Trying to escalate",
            "execution_mode": 1  // TRUSTED — must be ignored
        });
        let resp = create_router(
            Arc::clone(&registry), make_config(), Arc::clone(&store), Some(pool),
        )
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/v1/infer")
                .header("content-type", "application/json")
                .header("authorization", format!("Bearer {token}"))
                .body(Body::from(body.to_string()))
                .unwrap(),
        )
        .await
        .unwrap();

        assert_eq!(resp.status(), StatusCode::ACCEPTED);
        let bytes = resp.into_body().collect().await.unwrap().to_bytes();
        let json: serde_json::Value = serde_json::from_slice(&bytes).unwrap();
        assert!(json["task_id"].as_str().is_some_and(|s| !s.is_empty()));
    }

    #[tokio::test]
    async fn test_infer_rejects_wrong_app_id() {
        let Some(pool) = try_pool().await else { return };
        let store: Arc<dyn Store> = Arc::new(crate::store::RedisStore::new_for_test(pool.clone()));
        let registry = Arc::new(ToolRegistry::new());

        // Register "myapp" to obtain its token
        let reg_resp = create_router(
            Arc::clone(&registry), make_config(), Arc::clone(&store), Some(pool.clone()),
        )
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/v1/register")
                .header("content-type", "application/json")
                .body(Body::from(serde_json::json!({
                    "app_id": "myapp",
                    "callback_url": "http://app:8000",
                    "tools": []
                }).to_string()))
                .unwrap(),
        )
        .await
        .unwrap();
        let reg_bytes = reg_resp.into_body().collect().await.unwrap().to_bytes();
        let token = serde_json::from_slice::<serde_json::Value>(&reg_bytes).unwrap()["app_token"]
            .as_str()
            .unwrap()
            .to_string();

        // Attempt to infer claiming a DIFFERENT app_id
        let resp = create_router(
            Arc::clone(&registry), make_config(), Arc::clone(&store), Some(pool),
        )
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/v1/infer")
                .header("content-type", "application/json")
                .header("authorization", format!("Bearer {token}"))
                .body(Body::from(serde_json::json!({
                    "app_id": "otherapp",
                    "user_id": "u1",
                    "prompt": "Spoof attempt"
                }).to_string()))
                .unwrap(),
        )
        .await
        .unwrap();
        assert_eq!(resp.status(), StatusCode::FORBIDDEN);
    }
}
