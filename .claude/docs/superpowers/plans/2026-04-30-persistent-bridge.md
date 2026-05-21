# Persistent Bridge (Stage 1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Migrate the Capability Bridge's tool registry from pure in-memory to a write-through cache backed by Redis, giving the bridge full restart recovery without sacrificing read performance.

**Architecture:** The existing `ToolRegistry` (`RwLock<HashMap>`) stays as the serving layer — all reads come from it at zero network cost. A new `Store` trait abstracts persistence; `RedisStore` implements it using `deadpool-redis` with connection pooling. Every write (`register`, `subscribe`, `unregister`) persists to Redis first, then updates in-memory. On startup, the bridge hydrates the in-memory registry from Redis before accepting traffic. Existing router tests wire to a `NoopStore` so they need no Redis and stay fast.

**Pattern:** Write-through cache. This is the standard pattern used in production service registries (Consul, Kubernetes API server, Stripe's internal routing layer). L1 = in-memory HashMap (microsecond reads), L2 = Redis (startup hydration + durability).

**Tech Stack:** Rust, `deadpool-redis 0.15`, `redis 0.25`, `async-trait 0.1`, `thiserror 1`, `serde` (already present)

---

## Redis Data Model

Five key types, forming a pair of reverse indexes that enable efficient atomic re-registration:

| Key | Type | Contents |
|---|---|---|
| `bridge:tools` | Hash | `tool_name` → `RegisteredTool` JSON |
| `bridge:callbacks` | Hash | `app_id` → `callback_url` |
| `bridge:app_tools:{app_id}` | Set | tool names owned by this app (reverse index) |
| `bridge:subs:{topic}` | Set | app_ids subscribed to this topic |
| `bridge:app_subs:{app_id}` | Set | topics this app is subscribed to (reverse index) |

---

## File Map

```
bridge/
├── Cargo.toml              — add deadpool-redis, redis, async-trait, thiserror
├── src/
│   ├── config.rs           — add redis_url field
│   ├── registry.rs         — add serde derives to RegisteredTool, add hydrate()
│   ├── store.rs            — NEW: Store trait, HydratedState, StoreError,
│   │                         NoopStore, RedisStore
│   ├── router.rs           — add store: Arc<dyn Store> to AppState,
│   │                         call store in handle_register, update create_router
│   ├── main.rs             — init pool, create RedisStore, hydrate, pass to router
│   └── lib.rs              — add pub mod store
```

---

## Task 1: Dependencies + redis_url config

**Files:**
- Modify: `bridge/Cargo.toml`
- Modify: `bridge/src/config.rs`

- [x] **Step 1: Add redis_url to Config**
- [x] **Step 2: Add config tests for redis_url**
- [x] **Step 3: Run config tests to confirm they pass**
- [x] **Step 4: Add dependencies to Cargo.toml**
- [x] **Step 5: Confirm full build still passes**
- [x] **Step 6: Commit**

---

## Task 2: Store trait + NoopStore + serde on RegisteredTool + registry hydrate

**Files:**
- Create: `bridge/src/store.rs`
- Modify: `bridge/src/lib.rs`
- Modify: `bridge/src/registry.rs`

- [x] **Step 1: Add serde derives to RegisteredTool**
- [x] **Step 2: Add hydrate() to ToolRegistry**
- [x] **Step 3: Create bridge/src/store.rs**
- [x] **Step 4: Add pub mod store to lib.rs**
- [x] **Step 5: Run tests to confirm nothing broke**
- [x] **Step 6: Commit**

---

## Task 3: RedisStore — register + unregister

**Files:**
- Modify: `bridge/src/store.rs`

- [x] **Step 1: Write failing tests for register + unregister**
- [x] **Step 2: Run to confirm tests fail**
- [x] **Step 3: Implement register + unregister**
- [x] **Step 4: Run tests — skip gracefully if Redis is down, pass if up**
- [x] **Step 5: Confirm full test suite still passes**
- [x] **Step 6: Commit**

---

## Task 4: RedisStore — subscribe + hydrate

**Files:**
- Modify: `bridge/src/store.rs`

- [x] **Step 1: Write failing tests for subscribe + hydrate**
- [x] **Step 2: Run to confirm tests fail**
- [x] **Step 3: Implement subscribe**
- [x] **Step 4: Implement hydrate**
- [x] **Step 5: Run all store tests**
- [x] **Step 6: Run full suite**
- [x] **Step 7: Commit**

---

## Task 5: Wire store into router

**Files:**
- Modify: `bridge/src/router.rs`

- [x] **Step 1: Add store import to router.rs**
- [x] **Step 2: Add store field to AppState**
- [x] **Step 3: Update handle_register to write through to store**
- [x] **Step 4: Update create_router signature**
- [x] **Step 5: Add make_router helper to test module and update all test calls**
- [x] **Step 6: Run full test suite — all tests must pass without Redis**
- [x] **Step 7: Commit**

---

## Task 6: Startup hydration in main.rs

**Files:**
- Modify: `bridge/src/main.rs`

- [x] **Step 1: Write the updated main.rs**
- [x] **Step 2: Build to confirm it compiles**
- [x] **Step 3: Run full test suite**
- [x] **Step 4: Smoke test with Redis running**
- [x] **Step 5: Commit**
