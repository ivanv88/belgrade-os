# Modular Multi-UI & Vault Service Implementation Plan

> **Goal:** Transform Belgrade OS into a visual, interactive platform. Build a secure, multi-tenant UI Proxy in the Go Gateway and a conflict-free Vault Service for Obsidian.

## 🏛️ Architecture & Security Model

### 1. One Binary, Two Modules
The Go Gateway will be refactored into two distinct functional zones:
- **API Module:** Handles `/v1/tasks` and SSE streams (Existing).
- **UI Module:** Handles `/ui/{app_id}/{bundle}/*` (New).
Shared components like **Auth (JWT)** and **Redis** will serve both modules.

### 2. Bouncer Security (Redis RBAC)
To keep the Gateway fast and avoid direct Database dependencies:
- **Truth:** Permissions are managed in Postgres (`shared.app_permissions`).
- **Cache:** The Platform Controller syncs these permissions to Redis hashes (`perms:{user_id}`).
- **Edge Enforcement:** The Gateway checks Redis for every `/ui/` and `/v1/execute` request.

---

## Task 1: Database & Permission Sync

**Files:**
- Modify: `shared/database.py` (Add permissions table)
- Modify: `platform_controller/scheduler.py` (Sync task)

- [ ] **Step 1: Add `shared.app_permissions` table**
    - Fields: `user_id`, `app_id`, `bundle_id` (optional), `role`.
- [ ] **Step 2: Implement Permission Sync**
    - Add a background task to the Platform Controller that serializes permissions into Redis: `HSET perms:{user_id} {app_id}:{bundle_id} {role}`.
- [ ] **Step 3: Seed Admin Permissions**

---

## Task 2: Gateway UI Module (Go)

**Files:**
- Create: `gateway/ui/handler.go`
- Create: `gateway/ui/middleware.go`
- Modify: `gateway/main.go`

- [ ] **Step 1: Create isolated `ui` package**
    - Implement `ServeAsset` which maps URLs to `/apps/{app_id}/static/{bundle}/`.
- [ ] **Step 2: Implement RBAC Middleware**
    - Performs an `HGET` on `perms:{user_id}` before serving any file.
- [ ] **Step 3: Implement Config Injector**
    - Dynamically injects `window.BELGRADE_CONFIG` into HTML files.
    - Fields: `app_id`, `bundle_id`, `user_id`, `tenant_id`, `role`, `gateway_url`.
- [ ] **Step 4: Path Traversal Protection**
    - Strict validation to ensure no `..` escapes the `/apps` directory.

---

## Task 3: Vault Service (Python)

**Files:**
- Create: `vault_service/main.py`
- Create: `vault_service/worker.py`
- Create: `vault_service/redis_client.py`

- [ ] **Step 1: Implement `tasks:vault_ops` consumer**
    - Consumes requests like: `{"op": "write", "path": "notes/test.md", "content": "..."}`.
- [ ] **Step 2: Implement Atomic Write & Redis Locking**
    - Use `SET NX EX` to prevent AI-vs-AI collisions.
    - Write to `.tmp` then `os.rename` for atomic sync compatibility.
- [ ] **Step 3: Emit `system.vault_updated` event**

---

## Task 4: SDK & MFE Manifest Support

**Files:**
- Modify: `sdk/belgrade_sdk/models.py`
- Modify: `sdk/belgrade_sdk/context.py`

- [ ] **Step 1: Update AppManifest for MFE support**
    - Add `related_apps: List[str]` to allow embedding specific UIs.
    - Add `ui.bundles` dictionary to support multiple builds (web, mobile, etc.).
- [ ] **Step 2: Refactor `ctx.vault.write`**
    - Replace direct filesystem access with a Redis `XADD` to the Vault Service.
- [ ] **Step 3: Deploy "Shopping" Test UI**
    - Create a simple `index.html` that uses `BELGRADE_CONFIG` to fetch the list.
- [ ] **Step 4: Deploy "Dashboard Shell"**
    - Create a reference app that lists all authorized UIs for the user.

---

## Verification & Robustness
- **Security:** Verify 403 response for unauthorized bundles.
- **Resilience:** Verify that a down Vault Service doesn't crash the calling app (async queueing).
- **MFE Check:** Verify that the Dashboard can successfully load the Shopping UI in an iframe.
