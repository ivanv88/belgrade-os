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
        use std::sync::atomic::{AtomicU64, Ordering};
        static COUNTER: AtomicU64 = AtomicU64::new(0);
        let id = COUNTER.fetch_add(1, Ordering::Relaxed);
        let pid = std::process::id();
        Self { pool, prefix: format!("test:{}:{}:", pid, id) }
    }
}

#[async_trait]
impl Store for RedisStore {
    async fn register(
        &self,
        app_id: &str,
        callback_url: &str,
        tools: &[ToolRegistration],
    ) -> Result<(), StoreError> {
        let mut conn = self.pool.get().await?;
        let app_tools_key = self.app_tools_key(app_id);

        // Round-trip 1: get old tool names via reverse index.
        let old_names: Vec<String> = redis::cmd("SMEMBERS")
            .arg(&app_tools_key)
            .query_async(&mut *conn)
            .await?;

        // Round-trip 2: pipeline all writes.
        let mut pipe = redis::pipe();
        pipe.hset(self.callbacks_key(), app_id, callback_url).ignore();
        for name in &old_names {
            pipe.hdel(self.tools_key(), name).ignore();
        }
        pipe.del(&app_tools_key).ignore();
        for t in tools {
            let tool = RegisteredTool {
                name: t.name.clone(),
                description: t.description.clone(),
                input_schema_json: t.input_schema_json.clone(),
                app_id: app_id.to_string(),
                callback_url: callback_url.to_string(),
            };
            pipe.hset(self.tools_key(), &t.name, serde_json::to_string(&tool)?).ignore();
            pipe.sadd(&app_tools_key, &t.name).ignore();
        }
        pipe.query_async::<_, ()>(&mut *conn).await?;
        Ok(())
    }

    async fn subscribe(&self, app_id: &str, topics: &[String]) -> Result<(), StoreError> {
        let mut conn = self.pool.get().await?;
        let app_subs_key = self.app_subs_key(app_id);

        // Round-trip 1: get current subscription set for this app.
        let old_topics: Vec<String> = redis::cmd("SMEMBERS")
            .arg(&app_subs_key)
            .query_async(&mut *conn)
            .await?;

        // NOTE: subscribe() intentionally does not check whether app_id exists in
        // bridge:callbacks. Stale subscription keys for unknown apps are harmless —
        // registry.get_subscribers() filters them out at read time because they
        // have no callback entry. Apps should always call register() before subscribe().

        // Round-trip 2: pipeline authoritative replace.
        let mut pipe = redis::pipe();

        for topic in &old_topics {
            pipe.srem(self.subs_key(topic), app_id).ignore();
        }
        pipe.del(&app_subs_key).ignore();

        for topic in topics {
            // Redis Sets deduplicate naturally — SADD is idempotent.
            pipe.sadd(self.subs_key(topic), app_id).ignore();
            pipe.sadd(&app_subs_key, topic).ignore();
        }

        pipe.query_async::<_, ()>(&mut *conn).await?;
        Ok(())
    }

    async fn unregister(&self, app_id: &str) -> Result<(), StoreError> {
        let mut conn = self.pool.get().await?;
        let app_tools_key = self.app_tools_key(app_id);
        let app_subs_key = self.app_subs_key(app_id);

        // Round-trip 1: pipeline both reverse-index reads together.
        let (old_names, old_topics): (Vec<String>, Vec<String>) = redis::pipe()
            .smembers(&app_tools_key)
            .smembers(&app_subs_key)
            .query_async(&mut *conn)
            .await?;

        // Round-trip 2: pipeline all deletes.
        let mut pipe = redis::pipe();
        for name in &old_names {
            pipe.hdel(self.tools_key(), name).ignore();
        }
        pipe.del(&app_tools_key).ignore();
        pipe.hdel(self.callbacks_key(), app_id).ignore();
        for topic in &old_topics {
            pipe.srem(self.subs_key(topic), app_id).ignore();
        }
        pipe.del(&app_subs_key).ignore();
        pipe.query_async::<_, ()>(&mut *conn).await?;
        Ok(())
    }

    async fn hydrate(&self) -> Result<HydratedState, StoreError> {
        let mut conn = self.pool.get().await?;

        let raw_tools: HashMap<String, String> = redis::cmd("HGETALL")
            .arg(self.tools_key())
            .query_async(&mut *conn)
            .await?;
        let tools: Vec<RegisteredTool> = raw_tools
            .values()
            .map(|v| serde_json::from_str(v))
            .collect::<Result<_, _>>()?;

        let callbacks: HashMap<String, String> = redis::cmd("HGETALL")
            .arg(self.callbacks_key())
            .query_async(&mut *conn)
            .await?;

        // NOTE: KEYS blocks Redis while scanning. Fine for a small personal system.
        // Production would use SCAN with a cursor.
        let topic_keys: Vec<String> = redis::cmd("KEYS")
            .arg(self.subs_pattern())
            .query_async(&mut *conn)
            .await?;

        let strip = self.subs_strip_prefix();
        let mut subscriptions: HashMap<String, Vec<String>> = HashMap::new();
        for key in &topic_keys {
            let topic = key.strip_prefix(&strip)
                .expect("KEYS result has wrong prefix — should be unreachable")
                .to_string();
            let members: Vec<String> = redis::cmd("SMEMBERS")
                .arg(key)
                .query_async(&mut *conn)
                .await?;
            if !members.is_empty() {
                subscriptions.insert(topic, members);
            }
        }

        Ok(HydratedState { tools, callbacks, subscriptions })
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

    // ── helpers ──────────────────────────────────────────────────────────────

    async fn try_pool() -> Option<deadpool_redis::Pool> {
        let cfg = deadpool_redis::Config::from_url("redis://localhost:6379");
        let pool = cfg.create_pool(Some(deadpool_redis::Runtime::Tokio1)).ok()?;
        let mut conn = pool.get().await.ok()?;
        redis::cmd("PING").query_async::<_, ()>(&mut *conn).await.ok()?;
        Some(pool)
    }

    fn reg(name: &str) -> ToolRegistration {
        ToolRegistration {
            name: name.to_string(),
            description: "desc".to_string(),
            input_schema_json: "{}".to_string(),
        }
    }

    // ── register tests ───────────────────────────────────────────────────────

    #[tokio::test]
    async fn test_redis_register_persists_tool() {
        let Some(pool) = try_pool().await else { return; };
        let store = RedisStore::new_for_test(pool);

        store.register("shopping", "http://app:8000", &[reg("shopping:add_item")])
            .await.unwrap();

        let state = store.hydrate().await.unwrap();
        assert_eq!(state.tools.len(), 1);
        assert_eq!(state.tools[0].name, "shopping:add_item");
        assert_eq!(state.tools[0].callback_url, "http://app:8000");
        assert_eq!(state.callbacks["shopping"], "http://app:8000");
    }

    #[tokio::test]
    async fn test_redis_register_replaces_old_tools() {
        let Some(pool) = try_pool().await else { return; };
        let store = RedisStore::new_for_test(pool);

        store.register("shopping", "http://app:8000", &[reg("shopping:old")])
            .await.unwrap();
        store.register("shopping", "http://app:8000", &[reg("shopping:new")])
            .await.unwrap();

        let state = store.hydrate().await.unwrap();
        let names: Vec<_> = state.tools.iter().map(|t| t.name.as_str()).collect();
        assert!(!names.contains(&"shopping:old"), "old tool should be gone");
        assert!(names.contains(&"shopping:new"));
    }

    #[tokio::test]
    async fn test_redis_register_does_not_affect_other_apps() {
        let Some(pool) = try_pool().await else { return; };
        let store = RedisStore::new_for_test(pool);

        store.register("app1", "http://app1:8000", &[reg("app1:t1")])
            .await.unwrap();
        store.register("app2", "http://app2:8000", &[reg("app2:t1")])
            .await.unwrap();
        store.register("app1", "http://app1:8000", &[reg("app1:t2")])
            .await.unwrap();

        let state = store.hydrate().await.unwrap();
        let names: Vec<_> = state.tools.iter().map(|t| t.name.as_str()).collect();
        assert!(!names.contains(&"app1:t1"));
        assert!(names.contains(&"app1:t2"));
        assert!(names.contains(&"app2:t1"), "app2 should be untouched");
    }

    // ── unregister tests ─────────────────────────────────────────────────────

    #[tokio::test]
    async fn test_redis_unregister_removes_all_app_state() {
        let Some(pool) = try_pool().await else { return; };
        let store = RedisStore::new_for_test(pool);

        store.register("shopping", "http://app:8000", &[reg("shopping:add_item")])
            .await.unwrap();
        store.unregister("shopping").await.unwrap();

        let state = store.hydrate().await.unwrap();
        assert!(state.tools.is_empty());
        assert!(!state.callbacks.contains_key("shopping"));
    }

    #[tokio::test]
    async fn test_redis_unregister_does_not_affect_other_apps() {
        let Some(pool) = try_pool().await else { return; };
        let store = RedisStore::new_for_test(pool);

        store.register("app1", "http://app1:8000", &[reg("app1:t1")])
            .await.unwrap();
        store.register("app2", "http://app2:8000", &[reg("app2:t1")])
            .await.unwrap();
        store.unregister("app1").await.unwrap();

        let state = store.hydrate().await.unwrap();
        let names: Vec<_> = state.tools.iter().map(|t| t.name.as_str()).collect();
        assert!(names.contains(&"app2:t1"), "app2 should be untouched");
        assert!(state.callbacks.contains_key("app2"));
    }

    #[tokio::test]
    async fn test_redis_register_updates_callback_url() {
        let Some(pool) = try_pool().await else { return; };
        let store = RedisStore::new_for_test(pool);

        store.register("shopping", "http://old:8000", &[reg("shopping:add_item")])
            .await.unwrap();
        store.register("shopping", "http://new:9000", &[reg("shopping:add_item")])
            .await.unwrap();

        let state = store.hydrate().await.unwrap();
        assert_eq!(state.callbacks["shopping"], "http://new:9000");
        assert_eq!(state.tools[0].callback_url, "http://new:9000");
    }

    #[tokio::test]
    async fn test_redis_unregister_nonexistent_app_is_noop() {
        let Some(pool) = try_pool().await else { return; };
        let store = RedisStore::new_for_test(pool);

        // Unregistering an app that was never registered should succeed silently.
        let result = store.unregister("ghost_app").await;
        assert!(result.is_ok());

        let state = store.hydrate().await.unwrap();
        assert!(state.tools.is_empty());
        assert!(state.callbacks.is_empty());
    }

    // ── subscribe ────────────────────────────────────────────────────────────

    #[tokio::test]
    async fn test_redis_subscribe_persists_topic() {
        let Some(pool) = try_pool().await else { return; };
        let store = RedisStore::new_for_test(pool);

        store.register("app1", "http://app1:8000", &[]).await.unwrap();
        store.subscribe("app1", &["price.update".to_string()]).await.unwrap();

        let state = store.hydrate().await.unwrap();
        let subs = state.subscriptions.get("price.update").expect("topic missing");
        assert!(subs.contains(&"app1".to_string()));
    }

    #[tokio::test]
    async fn test_redis_subscribe_is_authoritative_replace() {
        let Some(pool) = try_pool().await else { return; };
        let store = RedisStore::new_for_test(pool);

        store.register("app1", "http://app1:8000", &[]).await.unwrap();
        store.subscribe("app1", &["topic1".to_string(), "topic2".to_string()]).await.unwrap();
        // Re-subscribe with narrower set
        store.subscribe("app1", &["topic2".to_string()]).await.unwrap();

        let state = store.hydrate().await.unwrap();
        assert!(
            state.subscriptions.get("topic1").map(|v| v.is_empty()).unwrap_or(true),
            "app1 should no longer be subscribed to topic1"
        );
        let subs2 = state.subscriptions.get("topic2").expect("topic2 missing");
        assert!(subs2.contains(&"app1".to_string()));
    }

    #[tokio::test]
    async fn test_redis_subscribe_does_not_duplicate() {
        let Some(pool) = try_pool().await else { return; };
        let store = RedisStore::new_for_test(pool);

        store.register("app1", "http://app1:8000", &[]).await.unwrap();
        store.subscribe("app1", &["price.update".to_string()]).await.unwrap();
        store.subscribe("app1", &["price.update".to_string()]).await.unwrap();

        let state = store.hydrate().await.unwrap();
        let subs = state.subscriptions.get("price.update").unwrap();
        assert_eq!(subs.iter().filter(|s| *s == "app1").count(), 1);
    }

    #[tokio::test]
    async fn test_redis_subscribe_empty_topics_clears_all() {
        let Some(pool) = try_pool().await else { return; };
        let store = RedisStore::new_for_test(pool);

        store.register("app1", "http://app1:8000", &[]).await.unwrap();
        store.subscribe("app1", &["topic1".to_string(), "topic2".to_string()]).await.unwrap();
        store.subscribe("app1", &[]).await.unwrap(); // unsubscribe from everything

        let state = store.hydrate().await.unwrap();
        assert!(
            state.subscriptions.get("topic1").map(|v| v.is_empty()).unwrap_or(true),
            "topic1 should have no subscribers"
        );
        assert!(
            state.subscriptions.get("topic2").map(|v| v.is_empty()).unwrap_or(true),
            "topic2 should have no subscribers"
        );
    }

    #[tokio::test]
    async fn test_redis_unregister_removes_subscriptions() {
        let Some(pool) = try_pool().await else { return; };
        let store = RedisStore::new_for_test(pool);

        store.register("app1", "http://app1:8000", &[]).await.unwrap();
        store.subscribe("app1", &["price.update".to_string()]).await.unwrap();
        store.unregister("app1").await.unwrap();

        let state = store.hydrate().await.unwrap();
        assert!(
            state.subscriptions.get("price.update").map(|v| v.is_empty()).unwrap_or(true),
            "subscriptions should be cleaned up on unregister"
        );
    }

    // ── hydrate ──────────────────────────────────────────────────────────────

    #[tokio::test]
    async fn test_redis_hydrate_empty_store() {
        let Some(pool) = try_pool().await else { return; };
        let store = RedisStore::new_for_test(pool);

        let state = store.hydrate().await.unwrap();
        assert!(state.tools.is_empty());
        assert!(state.callbacks.is_empty());
        assert!(state.subscriptions.is_empty());
    }

    #[tokio::test]
    async fn test_redis_hydrate_full_state() {
        let Some(pool) = try_pool().await else { return; };
        let store = RedisStore::new_for_test(pool);

        store.register("shopping", "http://shopping:8000", &[reg("shopping:add_item")])
            .await.unwrap();
        store.subscribe("shopping", &["price.update".to_string()])
            .await.unwrap();

        let state = store.hydrate().await.unwrap();
        assert_eq!(state.tools.len(), 1);
        assert_eq!(state.callbacks["shopping"], "http://shopping:8000");
        assert!(state.subscriptions["price.update"].contains(&"shopping".to_string()));
    }
}
