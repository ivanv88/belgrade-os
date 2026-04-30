mod config;
mod registry;
mod router;
mod store;

use std::sync::Arc;
use store::{RedisStore, Store};

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

    // Build the Redis connection pool. deadpool validates the URL and
    // pre-warms connections lazily, so the first pool.get() will connect.
    let pool_cfg = deadpool_redis::Config::from_url(&cfg.redis_url);
    let pool = pool_cfg
        .create_pool(Some(deadpool_redis::Runtime::Tokio1))
        .expect("failed to create Redis connection pool");

    let redis_store = Arc::new(RedisStore::new(pool));

    // Hydrate in-memory registry from Redis before accepting traffic.
    // Fail fast if Redis is unreachable — a cold bridge would appear healthy
    // but silently drop all tools until apps re-register.
    tracing::info!("hydrating registry from Redis");
    let hydrated = redis_store
        .hydrate()
        .await
        .expect("failed to hydrate registry from Redis — is Redis running?");

    let tool_count = hydrated.tools.len();
    let app_count = hydrated.callbacks.len();
    registry.hydrate(hydrated);
    tracing::info!(tools = tool_count, apps = app_count, "registry hydrated");

    let store: Arc<dyn Store> = redis_store;
    let addr = format!("0.0.0.0:{}", cfg.port);
    let app = router::create_router(Arc::clone(&registry), Arc::clone(&cfg), store);

    tracing::info!(port = cfg.port, "capability bridge listening");
    let listener = tokio::net::TcpListener::bind(&addr)
        .await
        .expect("failed to bind");
    axum::serve(listener, app).await.expect("server error");
}
