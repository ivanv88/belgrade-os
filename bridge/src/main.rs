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
