use std::collections::HashMap;
use async_trait::async_trait;
use crate::registry::{RegisteredTool, ToolRegistration};

// ─── Error ────────────────────────────────────────────────────────────────────

#[derive(Debug, thiserror::Error)]
pub enum StoreError {
    #[error("Redis error: {0}")]
    Redis(#[from] redis::RedisError),
    #[error("Pool error: {0}")]
    Pool(#[from] deadpool_redis::PoolError),
    #[error("Serialization error: {0}")]
    Serde(#[from] serde_json::Error),
}

// ─── HydratedState ────────────────────────────────────────────────────────────

/// All registry data loaded from the persistent store on startup.
#[derive(Default)]
pub struct HydratedState {
    pub tools: Vec<RegisteredTool>,
    pub callbacks: HashMap<String, String>,
    pub subscriptions: HashMap<String, Vec<String>>,
}

// ─── Store trait ──────────────────────────────────────────────────────────────

/// Abstracts the persistence layer behind the in-memory registry.
/// Implementations must be Send + Sync to be shared across Axum handlers.
#[async_trait]
pub trait Store: Send + Sync {
    /// Persist tool registration for an app. Re-registration replaces all
    /// previous tools for that app atomically.
    async fn register(
        &self,
        app_id: &str,
        callback_url: &str,
        tools: &[ToolRegistration],
    ) -> Result<(), StoreError>;

    /// Persist topic subscriptions for an app. Re-subscribing replaces the
    /// app's previous subscription set (authoritative replace, not append).
    async fn subscribe(&self, app_id: &str, topics: &[String]) -> Result<(), StoreError>;

    /// Remove all persisted state for an app (tools, callback, subscriptions).
    async fn unregister(&self, app_id: &str) -> Result<(), StoreError>;

    /// Load all persisted state. Called once at startup to hydrate the
    /// in-memory registry.
    async fn hydrate(&self) -> Result<HydratedState, StoreError>;
}

// ─── NoopStore ────────────────────────────────────────────────────────────────

/// No-op implementation used in tests and when persistence is disabled.
/// All writes succeed immediately without touching any external system.
pub struct NoopStore;

#[async_trait]
impl Store for NoopStore {
    async fn register(&self, _: &str, _: &str, _: &[ToolRegistration]) -> Result<(), StoreError> {
        Ok(())
    }
    async fn subscribe(&self, _: &str, _: &[String]) -> Result<(), StoreError> {
        Ok(())
    }
    async fn unregister(&self, _: &str) -> Result<(), StoreError> {
        Ok(())
    }
    async fn hydrate(&self) -> Result<HydratedState, StoreError> {
        Ok(HydratedState::default())
    }
}

// ─── RedisStore ───────────────────────────────────────────────────────────────

/// Write-through Redis persistence for the tool registry.
/// Uses a deadpool connection pool — connections are reused across requests.
pub struct RedisStore {
    pool: deadpool_redis::Pool,
    // Key prefix: empty in production, unique per test run for isolation.
    prefix: String,
}

impl RedisStore {
    pub fn new(pool: deadpool_redis::Pool) -> Self {
        Self { pool, prefix: String::new() }
    }

    fn tools_key(&self) -> String { format!("{}bridge:tools", self.prefix) }
    fn callbacks_key(&self) -> String { format!("{}bridge:callbacks", self.prefix) }
    fn app_tools_key(&self, app_id: &str) -> String { format!("{}bridge:app_tools:{}", self.prefix, app_id) }
    fn subs_key(&self, topic: &str) -> String { format!("{}bridge:subs:{}", self.prefix, topic) }
    fn app_subs_key(&self, app_id: &str) -> String { format!("{}bridge:app_subs:{}", self.prefix, app_id) }
    fn subs_pattern(&self) -> String { format!("{}bridge:subs:*", self.prefix) }
    fn subs_strip_prefix(&self) -> String { format!("{}bridge:subs:", self.prefix) }

    #[cfg(test)]
    pub fn new_for_test(pool: deadpool_redis::Pool) -> Self {
        let nanos = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap()
            .subsec_nanos();
        Self { pool, prefix: format!("test:{}:", nanos) }
    }
}

#[async_trait]
impl Store for RedisStore {
    async fn register(&self, _: &str, _: &str, _: &[ToolRegistration]) -> Result<(), StoreError> {
        todo!()
    }
    async fn subscribe(&self, _: &str, _: &[String]) -> Result<(), StoreError> {
        todo!()
    }
    async fn unregister(&self, _: &str) -> Result<(), StoreError> {
        todo!()
    }
    async fn hydrate(&self) -> Result<HydratedState, StoreError> {
        todo!()
    }
}

// ─── Tests ────────────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;

    #[tokio::test]
    async fn test_noop_store_register_succeeds() {
        let store = NoopStore;
        let result = store.register("app1", "http://app:8000", &[]).await;
        assert!(result.is_ok());
    }

    #[tokio::test]
    async fn test_noop_store_hydrate_returns_empty() {
        let store = NoopStore;
        let state = store.hydrate().await.unwrap();
        assert!(state.tools.is_empty());
        assert!(state.callbacks.is_empty());
        assert!(state.subscriptions.is_empty());
    }
}
