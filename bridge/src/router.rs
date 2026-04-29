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

#[derive(Deserialize, Serialize, Clone)]
pub struct EventPayload {
    pub topic: String,
    pub payload: serde_json::Value,
    pub app_id: String,
    pub tenant_id: Option<String>,
    pub trace_id: String,
}

async fn handle_register(
    State(state): State<AppState>,
    Json(req): Json<RegisterRequest>,
) -> Result<StatusCode, (StatusCode, String)> {
    for t in &req.tools {
        if !t.name.starts_with(&format!("{}:", req.app_id)) {
            return Err((
                StatusCode::BAD_REQUEST,
                format!("tool name {:?} must be namespaced as '{}:<name>'", t.name, req.app_id),
            ));
        }
    }
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

    if let Some(subs) = req.subscriptions {
        state.registry.subscribe(&req.app_id, subs);
    }

    Ok(StatusCode::NO_CONTENT)
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

pub fn create_router(registry: Arc<ToolRegistry>, config: Arc<Config>) -> Router {
    let state = AppState { registry, config, http: reqwest::Client::new() };
    Router::new()
        .route("/v1/register", post(handle_register))
        .route("/v1/tools", get(handle_tools))
        .route("/v1/execute", post(handle_execute))
        .route("/v1/events/publish", post(handle_publish))
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

    #[tokio::test]
    async fn test_register_bad_tool_name_returns_400() {
        let registry = Arc::new(ToolRegistry::new());
        let app = create_router(Arc::clone(&registry), make_config());

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
        let app = create_router(Arc::clone(&registry), make_config());

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

        let app = create_router(Arc::clone(&registry), make_config());

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
    async fn test_publish_no_subscribers_is_accepted() {
        let registry = Arc::new(ToolRegistry::new());
        let app = create_router(Arc::clone(&registry), make_config());

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
}
