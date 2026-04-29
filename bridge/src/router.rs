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

async fn handle_execute(
    State(_state): State<AppState>,
    Json(_req): Json<ExecuteRequest>,
) -> Json<ExecuteResponse> {
    todo!()
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
