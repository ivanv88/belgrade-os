# Phase 3b — App Installer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a live app installer to the platform — `POST /installer/install` copies an app from a local source path and starts it immediately; `POST /installer/update` re-copies from the original source and hot-reloads.

**Architecture:** A `shared.installed_apps` registry table records every installed app (source path, install path, scope). The Bootstrapper gains `load_app()` and `unload_app()` methods for live load/unload without server restart. On first boot, existing apps in `apps/` are seeded into the registry automatically. The `AppInstaller` FastAPI router calls `load_app()` after install and `unload_app()` + re-copy + `load_app()` for update. Scope (`shared` or `tenant:{id}`) controls DB schema naming (`app_{app_id}` vs `app_{tenant_id}_{app_id}`).

**Tech Stack:** Python 3.12+, FastAPI, SQLModel, asyncpg, shutil, pytest, mypy.

**Prerequisite:** Phase 3b-tenant-model must be complete — this plan uses `TenantRegistry`, `User`, `Tenant`, and the updated `AppContext`.

---

## File Map

| Action | File | Responsibility |
|--------|------|----------------|
| Modify | `shared/database.py` | Add `shared.installed_apps` DDL to `SHARED_SCHEMA_SQL` |
| Create | `shared/installed_apps.py` | SQLModel ORM row: `InstalledAppRow` |
| Modify | `core/eventbus.py` | Add `unregister_app(app_id)` method |
| Modify | `core/loader.py` | Instance attrs for runtime state; `load_app()`, `unload_app()`; scope-aware provisioning; seed+discover from registry |
| Create | `core/installer.py` | `AppInstaller` — install/update logic + FastAPI router |
| Modify | `core/main.py` | Mount installer router; expose bootstrapper to installer |
| Modify | `tests/shared/test_database.py` | Assert `installed_apps` in `SHARED_SCHEMA_SQL` |
| Create | `tests/shared/test_installed_apps.py` | Unit tests for `InstalledAppRow` |
| Create | `tests/core/test_installer.py` | Unit tests for install + update flows |
| Modify | `tests/core/test_loader.py` | Add tests for `load_app` + `unload_app` |

---

### Task 1: DB Schema DDL — `shared.installed_apps`

**Files:**
- Modify: `shared/database.py`
- Modify: `tests/shared/test_database.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/shared/test_database.py — add to existing file
def test_shared_schema_sql_contains_installed_apps() -> None:
    from shared.database import SHARED_SCHEMA_SQL
    assert "CREATE TABLE IF NOT EXISTS shared.installed_apps" in SHARED_SCHEMA_SQL
    assert "source_path" in SHARED_SCHEMA_SQL
    assert "install_path" in SHARED_SCHEMA_SQL
    assert "scope" in SHARED_SCHEMA_SQL
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/shared/test_database.py::test_shared_schema_sql_contains_installed_apps -v
```

Expected: FAIL

- [ ] **Step 3: Append DDL to `SHARED_SCHEMA_SQL`**

Add to the end of the SQL string in `shared/database.py` (before the closing `"""`):

```sql
CREATE TABLE IF NOT EXISTS shared.installed_apps (
    app_id        TEXT PRIMARY KEY,
    source_path   TEXT NOT NULL,
    install_path  TEXT NOT NULL,
    scope         TEXT NOT NULL DEFAULT 'shared',
    installed_at  TIMESTAMPTZ DEFAULT NOW()
);
```

- [ ] **Step 4: Run all database tests**

```bash
pytest tests/shared/test_database.py -v
```

Expected: all tests PASS

- [ ] **Step 5: Commit**

```bash
git add shared/database.py tests/shared/test_database.py
git commit -m "feat: add shared.installed_apps DDL"
```

---

### Task 2: SQLModel ORM row — `shared/installed_apps.py`

**Files:**
- Create: `shared/installed_apps.py`
- Create: `tests/shared/test_installed_apps.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/shared/test_installed_apps.py
from __future__ import annotations


def test_installed_app_row_fields() -> None:
    from shared.installed_apps import InstalledAppRow
    row = InstalledAppRow(
        app_id="nutrition",
        source_path="/home/ivan/repos/nutrition",
        install_path="apps/nutrition",
    )
    assert row.app_id == "nutrition"
    assert row.scope == "shared"
    assert row.install_path == "apps/nutrition"


def test_installed_app_row_tenant_scope() -> None:
    from shared.installed_apps import InstalledAppRow
    row = InstalledAppRow(
        app_id="fitness",
        source_path="/home/ivan/repos/fitness",
        install_path="apps/fitness",
        scope="tenant:household-a",
    )
    assert row.scope == "tenant:household-a"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/shared/test_installed_apps.py -v
```

Expected: `ModuleNotFoundError: No module named 'shared.installed_apps'`

- [ ] **Step 3: Write minimal implementation**

```python
# shared/installed_apps.py
from __future__ import annotations
from datetime import datetime
from typing import Optional
from sqlmodel import Field, SQLModel


class InstalledAppRow(SQLModel, table=True):
    __tablename__ = "installed_apps"  # type: ignore[assignment]
    __table_args__ = {"schema": "shared"}

    app_id: str = Field(primary_key=True)
    source_path: str
    install_path: str
    scope: str = Field(default="shared")
    installed_at: Optional[datetime] = Field(default=None)
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/shared/test_installed_apps.py -v
```

Expected: 2 tests PASS

- [ ] **Step 5: Commit**

```bash
git add shared/installed_apps.py tests/shared/test_installed_apps.py
git commit -m "feat: add InstalledAppRow SQLModel ORM model"
```

---

### Task 3: `EventBus.unregister_app`

**Files:**
- Modify: `core/eventbus.py`
- Modify: `tests/core/test_eventbus.py`

`unload_app` (Task 4) needs to remove all EventBus subscriptions for an app being unloaded. Add `unregister_app(app_id)` to `EventBus`.

- [ ] **Step 1: Read `core/eventbus.py` to find the subscription data structure**

```bash
grep -n "subscription\|register" core/eventbus.py | head -20
```

Note the structure of `self._subscriptions` (it's a `dict[str, list[str]]` mapping topic → [app_ids]).

- [ ] **Step 2: Write the failing test**

```python
# tests/core/test_eventbus.py — add to existing file
def test_unregister_app_removes_from_all_topics() -> None:
    from core.eventbus import EventBus
    bus = EventBus()
    bus.register_subscription("nutrition.meal_logged", "dashboard")
    bus.register_subscription("nutrition.meal_logged", "analytics")
    bus.register_subscription("fitness.workout_done", "dashboard")

    bus.unregister_app("dashboard")

    # dashboard removed from both topics
    assert "dashboard" not in bus._subscriptions.get("nutrition.meal_logged", [])
    assert "dashboard" not in bus._subscriptions.get("fitness.workout_done", [])
    # analytics still registered
    assert "analytics" in bus._subscriptions.get("nutrition.meal_logged", [])
```

- [ ] **Step 3: Run test to verify it fails**

```bash
pytest tests/core/test_eventbus.py::test_unregister_app_removes_from_all_topics -v
```

Expected: FAIL — `AttributeError: 'EventBus' object has no attribute 'unregister_app'`

- [ ] **Step 4: Add `unregister_app` to `EventBus`**

Open `core/eventbus.py` and add this method to the `EventBus` class (after `register_subscription`):

```python
def unregister_app(self, app_id: str) -> None:
    for topic in list(self._subscriptions.keys()):
        self._subscriptions[topic] = [
            sub for sub in self._subscriptions[topic] if sub != app_id
        ]
    logger.info("EventBus: unregistered all subscriptions for %s", app_id)
```

- [ ] **Step 5: Run all eventbus tests**

```bash
pytest tests/core/test_eventbus.py -v
```

Expected: all tests PASS

- [ ] **Step 6: Commit**

```bash
git add core/eventbus.py tests/core/test_eventbus.py
git commit -m "feat: add EventBus.unregister_app for clean app unload"
```

---

### Task 4: `Bootstrapper.load_app` + `unload_app` + scope-aware provisioning

**Files:**
- Modify: `core/loader.py`

This is the biggest task. The Bootstrapper becomes a proper runtime: it stores its own references to the FastAPI app, scheduler, event bus, and registry as instance attributes so `load_app`/`unload_app` can use them after `bootstrap()` completes. Job IDs are tracked per app so cron jobs can be removed individually.

Key design:
- `load_app(app_id, scope)` runs the full single-app startup sequence
- `unload_app(app_id)` reverses it: remove scheduler jobs, routes, EventBus subs, evict sys.modules
- `_provision_db_schema(app_id, scope)` becomes scope-aware
- A helper `_schema_name(app_id, scope)` is extracted for testability

- [ ] **Step 1: Write failing tests for scope-aware schema naming**

```python
# tests/core/test_loader.py — add to existing file
def test_schema_name_shared() -> None:
    from core.loader import _schema_name
    assert _schema_name("nutrition", "shared") == "app_nutrition"


def test_schema_name_shared_with_hyphens() -> None:
    from core.loader import _schema_name
    assert _schema_name("meal-tracker", "shared") == "app_meal_tracker"


def test_schema_name_tenant_scoped() -> None:
    from core.loader import _schema_name
    assert _schema_name("nutrition", "tenant:household-a") == "app_household_a_nutrition"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/core/test_loader.py::test_schema_name_shared tests/core/test_loader.py::test_schema_name_shared_with_hyphens tests/core/test_loader.py::test_schema_name_tenant_scoped -v
```

Expected: FAIL — `ImportError: cannot import name '_schema_name'`

- [ ] **Step 3: Add `_schema_name` helper and update `_provision_db_schema`**

Add this module-level function to `core/loader.py` (near the bottom, before `_parse_cron`):

```python
def _schema_name(app_id: str, scope: str) -> str:
    safe_id = app_id.replace("-", "_")
    if scope == "shared":
        return f"app_{safe_id}"
    tenant_id = scope.split(":", 1)[1].replace("-", "_")
    return f"app_{tenant_id}_{safe_id}"
```

Update `_provision_db_schema` in the `Bootstrapper` class:

```python
async def _provision_db_schema(self, app_id: str, scope: str = "shared") -> None:
    schema = _schema_name(app_id, scope)
    async with engine.begin() as conn:
        await conn.execute(text(f"CREATE SCHEMA IF NOT EXISTS {schema}"))
```

Update `provision` to accept scope:

```python
async def provision(self, app_id: str, scope: str = "shared") -> None:
    app_data_dir = self.data_dir / app_id
    app_data_dir.mkdir(parents=True, exist_ok=True)
    await self._provision_db_schema(app_id, scope)
```

- [ ] **Step 4: Run schema naming tests**

```bash
pytest tests/core/test_loader.py::test_schema_name_shared tests/core/test_loader.py::test_schema_name_shared_with_hyphens tests/core/test_loader.py::test_schema_name_tenant_scoped -v
```

Expected: 3 tests PASS

- [ ] **Step 5: Add instance state attributes + `load_app` + `unload_app` to Bootstrapper**

Update `Bootstrapper.__init__` to add runtime state fields:

```python
def __init__(self, apps_dir: Path, data_dir: Path) -> None:
    self.apps_dir = apps_dir
    self.data_dir = data_dir
    self.subscription_map: Dict[str, List[str]] = {}
    self._manifests: List[AppManifest] = []
    # Runtime state — set by bootstrap(), used by load_app/unload_app
    self._app: Optional[FastAPI] = None
    self._scheduler: Optional[Any] = None
    self._event_bus: Optional[EventBus] = None
    self._registry: Optional[TenantRegistry] = None
    self._notify: Optional[NotifyService] = None
    self._job_ids: Dict[str, List[str]] = {}  # app_id → [scheduler job ids]
```

Update `bootstrap()` to store references on self (add at the start, before the loop):

```python
async def bootstrap(
    self, app: FastAPI, event_bus: EventBus, registry: TenantRegistry
) -> None:
    from apscheduler.schedulers.asyncio import AsyncIOScheduler

    await registry.load()
    default = registry.resolve_default()
    if default is None:
        logger.warning(
            "TenantRegistry is empty — seed shared.tenants + shared.users before running apps"
        )
        return

    default_user, default_tenant = default
    ntfy_topic: Optional[str] = None
    try:
        from core.config import settings
        ntfy_topic = getattr(settings, "ntfy_topic", None)
    except Exception:
        pass
    notify = NotifyService(topic=ntfy_topic)
    scheduler = AsyncIOScheduler()

    # Store runtime references for load_app / unload_app
    self._app = app
    self._scheduler = scheduler
    self._event_bus = event_bus
    self._registry = registry
    self._notify = notify

    manifests = self.discover()
    self.build_subscription_map(manifests)

    for manifest in manifests:
        await self.provision(manifest.app_id)
        self._register_app_schemas(manifest.app_id, event_bus)

        for topic, subscribers in self.subscription_map.items():
            for sub_id in subscribers:
                event_bus.register_subscription(topic, sub_id)

        ctx_factory = self.build_ctx_factory(
            manifest, default_user, default_tenant, event_bus, notify, registry
        )

        job_ids: List[str] = []
        for trigger in manifest.triggers:
            if trigger.type == "cron" and trigger.schedule:
                async def cron_job(
                    mid: str = manifest.app_id,
                    factory: Callable[[], Any] = ctx_factory,
                ) -> None:
                    ctx = await factory()
                    await safe_execute(mid, ctx, event_bus)

                job = scheduler.add_job(cron_job, "cron", **_parse_cron(trigger.schedule))
                job_ids.append(job.id)

            elif trigger.type == "hook":
                _register_hook(app, manifest.app_id, ctx_factory, event_bus, registry)

            elif trigger.type == "observer":
                logger.warning(
                    "Observer trigger for %s: watchdog not yet wired",
                    manifest.app_id,
                )

        self._job_ids[manifest.app_id] = job_ids

        if manifest.mcp_enabled:
            logger.info("MCP tool pending for %s", manifest.app_id)

        logger.info("Bootstrapped: %s", manifest.app_id)

    scheduler.start()
```

Add `load_app` and `unload_app` methods to `Bootstrapper`:

```python
async def load_app(self, app_id: str, scope: str = "shared") -> None:
    """Live-load a single app. Called by the installer after copying app code."""
    assert self._app is not None, "bootstrap() must be called before load_app()"
    assert self._scheduler is not None
    assert self._event_bus is not None
    assert self._registry is not None
    assert self._notify is not None

    manifest_path = self.apps_dir / app_id / "manifest.json"
    data = json.loads(manifest_path.read_text())
    manifest = AppManifest.model_validate(data)

    await self.provision(app_id, scope)
    self._register_app_schemas(app_id, self._event_bus)

    if manifest.app_id not in self.subscription_map:
        for trigger in manifest.triggers:
            if trigger.type == "event" and trigger.topic:
                self.subscription_map.setdefault(trigger.topic, []).append(app_id)

    for topic, subscribers in self.subscription_map.items():
        for sub_id in subscribers:
            self._event_bus.register_subscription(topic, sub_id)

    default = self._registry.resolve_default()
    if default is None:
        logger.warning("Cannot load app %s — TenantRegistry is empty", app_id)
        return
    default_user, default_tenant = default

    ctx_factory = self.build_ctx_factory(
        manifest, default_user, default_tenant, self._event_bus, self._notify, self._registry
    )

    job_ids = []
    for trigger in manifest.triggers:
        if trigger.type == "cron" and trigger.schedule:
            async def cron_job(
                mid: str = app_id,
                factory: Callable[[], Any] = ctx_factory,
            ) -> None:
                ctx = await factory()
                await safe_execute(mid, ctx, self._event_bus)  # type: ignore[arg-type]

            job = self._scheduler.add_job(cron_job, "cron", **_parse_cron(trigger.schedule))
            job_ids.append(job.id)

        elif trigger.type == "hook":
            _register_hook(self._app, app_id, ctx_factory, self._event_bus, self._registry)

    self._job_ids[app_id] = job_ids
    self._manifests.append(manifest)
    logger.info("Loaded: %s (scope=%s)", app_id, scope)

def unload_app(self, app_id: str) -> None:
    """Remove all runtime registrations for an app. Called before re-copying for update."""
    import sys

    # Remove APScheduler jobs
    for job_id in self._job_ids.pop(app_id, []):
        try:
            if self._scheduler:
                self._scheduler.remove_job(job_id)
        except Exception:
            pass

    # Remove FastAPI routes
    if self._app is not None:
        route_path = f"/{app_id}/run"
        self._app.routes = [
            r for r in self._app.routes
            if getattr(r, "path", "") != route_path
        ]

    # Remove EventBus subscriptions
    if self._event_bus is not None:
        self._event_bus.unregister_app(app_id)

    # Evict from Python module cache so re-import picks up fresh code
    to_remove = [
        k for k in sys.modules
        if k == f"apps.{app_id}" or k.startswith(f"apps.{app_id}.")
    ]
    for k in to_remove:
        del sys.modules[k]

    # Remove from manifests list
    self._manifests = [m for m in self._manifests if m.app_id != app_id]
    logger.info("Unloaded: %s", app_id)
```

- [ ] **Step 6: Run full test suite**

```bash
pytest -v 2>&1 | tail -20
```

Expected: all existing tests pass. Fix any test that calls `provision()` without scope (default `"shared"` means it's backward compatible).

- [ ] **Step 7: Commit**

```bash
git add core/loader.py
git commit -m "feat: Bootstrapper runtime state, load_app/unload_app, scope-aware provisioning"
```

---

### Task 5: Seed + discover from registry

**Files:**
- Modify: `core/loader.py`

`discover()` currently scans `apps/` directory. After this task it reads from `shared.installed_apps`. On first boot (empty registry), it seeds from the directory and inserts records, preserving backward compatibility.

- [ ] **Step 1: Write failing tests**

```python
# tests/core/test_loader.py — add to existing file
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from pathlib import Path


@pytest.mark.asyncio
async def test_discover_seeds_from_apps_dir_when_registry_empty(apps_dir: Path) -> None:
    """On first boot with empty registry, apps/ directory is seeded into installed_apps."""
    from core.loader import Bootstrapper
    import json

    # Create a fake app in apps_dir
    app_dir = apps_dir / "nutrition"
    app_dir.mkdir()
    (app_dir / "manifest.json").write_text(json.dumps({
        "app_id": "nutrition",
        "name": "Nutrition",
        "description": "Track meals",
        "version": "1.0.0",
        "pattern": "worker",
        "mcp_enabled": False,
        "triggers": [{"type": "cron", "schedule": "0 20 * * *"}],
        "storage": {"scope": "nutrition", "adapter": "local"},
        "config": {},
    }))

    bootstrapper = Bootstrapper(apps_dir=apps_dir, data_dir=apps_dir / "data")

    mock_conn = AsyncMock()
    mock_conn.scalar = AsyncMock(return_value=0)  # empty registry
    mock_conn.execute = AsyncMock()

    mock_ctx = MagicMock()
    mock_ctx.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_ctx.__aexit__ = AsyncMock(return_value=None)

    with patch("core.loader.engine") as mock_engine:
        mock_engine.begin = MagicMock(return_value=mock_ctx)
        manifests = await bootstrapper.discover_from_registry()

    assert len(manifests) == 1
    assert manifests[0].app_id == "nutrition"
    # Verify INSERT was called (seeding)
    assert mock_conn.execute.called
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/core/test_loader.py::test_discover_seeds_from_apps_dir_when_registry_empty -v
```

Expected: FAIL — `AttributeError: 'Bootstrapper' has no 'discover_from_registry'`

- [ ] **Step 3: Add `discover_from_registry` to Bootstrapper in `core/loader.py`**

Add this method to `Bootstrapper` (keep existing `discover()` unchanged — it's used by tests that don't need DB):

```python
async def discover_from_registry(self) -> List[AppManifest]:
    """Read installed apps from DB registry. Seeds from apps/ dir on first boot."""
    from sqlalchemy import text as sa_text

    async with engine.begin() as conn:
        count = await conn.scalar(sa_text("SELECT COUNT(*) FROM shared.installed_apps"))
        if count == 0:
            # First boot: seed from apps/ directory
            for item in self.apps_dir.iterdir():
                if not item.is_dir():
                    continue
                manifest_path = item / "manifest.json"
                if not manifest_path.exists():
                    continue
                await conn.execute(sa_text(
                    "INSERT INTO shared.installed_apps "
                    "(app_id, source_path, install_path, scope) "
                    "VALUES (:app_id, :source, :install, 'shared') "
                    "ON CONFLICT (app_id) DO NOTHING"
                ), {
                    "app_id": item.name,
                    "source": str(item.resolve()),
                    "install": str(item),
                })
                logger.info("Seeded from apps/: %s", item.name)

        rows = await conn.execute(
            sa_text("SELECT app_id, install_path, scope FROM shared.installed_apps")
        )
        installed = list(rows.mappings())

    manifests: List[AppManifest] = []
    for row in installed:
        manifest_path = Path(row["install_path"]) / "manifest.json"
        if not manifest_path.exists():
            logger.warning("Installed app %s missing manifest at %s", row["app_id"], manifest_path)
            continue
        try:
            data = json.loads(manifest_path.read_text())
            manifests.append(AppManifest.model_validate(data))
        except (ValidationError, json.JSONDecodeError) as e:
            logger.warning("Skipping %s: %s", row["app_id"], e)

    self._manifests = manifests
    return manifests
```

- [ ] **Step 4: Run the test**

```bash
pytest tests/core/test_loader.py::test_discover_seeds_from_apps_dir_when_registry_empty -v
```

Expected: PASS

- [ ] **Step 5: Run full test suite**

```bash
pytest -v 2>&1 | tail -15
```

Expected: all tests pass

- [ ] **Step 6: Commit**

```bash
git add core/loader.py tests/core/test_loader.py
git commit -m "feat: discover_from_registry — seeds from apps/ on first boot, reads from DB thereafter"
```

---

### Task 6: `AppInstaller` router

**Files:**
- Create: `core/installer.py`
- Modify: `core/main.py`
- Create: `tests/core/test_installer.py`

The installer is a FastAPI router (not an app) created with a reference to the `Bootstrapper`. It has two endpoints: `install` and `update`. Both are synchronous from the caller's perspective — the response only returns after the app is running (or re-running after update).

- [ ] **Step 1: Write failing tests**

```python
# tests/core/test_installer.py
from __future__ import annotations
import json
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi.testclient import TestClient
from fastapi import FastAPI


def make_fake_app_dir(tmp_path: Path, app_id: str = "nutrition") -> Path:
    source = tmp_path / "source" / app_id
    source.mkdir(parents=True)
    (source / "manifest.json").write_text(json.dumps({
        "app_id": app_id,
        "name": "Nutrition",
        "description": "Track meals",
        "version": "1.0.0",
        "pattern": "worker",
        "mcp_enabled": False,
        "triggers": [{"type": "cron", "schedule": "0 20 * * *"}],
        "storage": {"scope": app_id, "adapter": "local"},
        "config": {},
    }))
    (source / "main.py").write_text("async def execute(ctx): pass\n")
    return source


def test_install_copies_app_and_returns_running(tmp_path: Path) -> None:
    from core.installer import create_installer_router
    from core.loader import Bootstrapper

    source = make_fake_app_dir(tmp_path)
    install_dir = tmp_path / "apps"
    install_dir.mkdir()

    bootstrapper = MagicMock(spec=Bootstrapper)
    bootstrapper.apps_dir = install_dir
    bootstrapper.load_app = AsyncMock()

    mock_conn = AsyncMock()
    mock_conn.execute = AsyncMock()
    mock_ctx = MagicMock()
    mock_ctx.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_ctx.__aexit__ = AsyncMock(return_value=None)

    test_app = FastAPI()
    router = create_installer_router(bootstrapper)
    test_app.include_router(router)
    client = TestClient(test_app)

    with patch("core.installer.engine") as mock_engine:
        mock_engine.begin = MagicMock(return_value=mock_ctx)
        response = client.post("/installer/install", json={
            "source_path": str(source),
            "scope": "shared",
        })

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "running"
    assert data["app_id"] == "nutrition"
    assert (install_dir / "nutrition").exists()
    bootstrapper.load_app.assert_called_once_with("nutrition", "shared")


def test_install_fails_if_no_manifest(tmp_path: Path) -> None:
    from core.installer import create_installer_router
    from core.loader import Bootstrapper

    source = tmp_path / "empty-app"
    source.mkdir()
    install_dir = tmp_path / "apps"
    install_dir.mkdir()

    bootstrapper = MagicMock(spec=Bootstrapper)
    bootstrapper.apps_dir = install_dir

    test_app = FastAPI()
    router = create_installer_router(bootstrapper)
    test_app.include_router(router)
    client = TestClient(test_app)

    response = client.post("/installer/install", json={
        "source_path": str(source),
        "scope": "shared",
    })
    assert response.status_code == 400
    assert "manifest.json" in response.json()["detail"]


def test_update_unloads_recopies_and_reloads(tmp_path: Path) -> None:
    from core.installer import create_installer_router
    from core.loader import Bootstrapper

    source = make_fake_app_dir(tmp_path, "nutrition")
    install_dir = tmp_path / "apps"
    (install_dir / "nutrition").mkdir(parents=True)
    # Existing install record in DB will be mocked

    bootstrapper = MagicMock(spec=Bootstrapper)
    bootstrapper.apps_dir = install_dir
    bootstrapper.load_app = AsyncMock()
    bootstrapper.unload_app = MagicMock()

    mock_row = MagicMock()
    mock_row.__getitem__ = lambda self, key: {
        "source_path": str(source),
        "scope": "shared",
    }[key]

    mock_result = MagicMock()
    mock_result.mappings.return_value.__iter__ = MagicMock(return_value=iter([mock_row]))
    mock_result.fetchone = MagicMock(return_value=mock_row)

    mock_conn = AsyncMock()
    mock_conn.execute = AsyncMock(return_value=mock_result)
    mock_ctx = MagicMock()
    mock_ctx.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_ctx.__aexit__ = AsyncMock(return_value=None)

    test_app = FastAPI()
    router = create_installer_router(bootstrapper)
    test_app.include_router(router)
    client = TestClient(test_app)

    with patch("core.installer.engine") as mock_engine:
        mock_engine.begin = MagicMock(return_value=mock_ctx)
        mock_engine.connect = MagicMock(return_value=mock_ctx)
        response = client.post("/installer/update", json={"app_id": "nutrition"})

    assert response.status_code == 200
    assert response.json()["status"] == "running"
    bootstrapper.unload_app.assert_called_once_with("nutrition")
    bootstrapper.load_app.assert_called_once_with("nutrition", "shared")
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/core/test_installer.py -v
```

Expected: `ModuleNotFoundError: No module named 'core.installer'`

- [ ] **Step 3: Implement `core/installer.py`**

```python
# core/installer.py
from __future__ import annotations
import logging
import shutil
from pathlib import Path
from pydantic import BaseModel
from fastapi import APIRouter, HTTPException
from sqlalchemy import text

logger = logging.getLogger(__name__)


class InstallRequest(BaseModel):
    source_path: str
    scope: str = "shared"


class UpdateRequest(BaseModel):
    app_id: str


def create_installer_router(bootstrapper: object) -> APIRouter:
    from shared.database import engine

    router = APIRouter(prefix="/installer")

    @router.post("/install")
    async def install(req: InstallRequest) -> dict[str, str]:
        source = Path(req.source_path)
        manifest_path = source / "manifest.json"
        if not manifest_path.exists():
            raise HTTPException(status_code=400, detail=f"manifest.json not found in {source}")

        import json
        from pydantic import ValidationError
        from core.models.manifest import AppManifest
        try:
            manifest = AppManifest.model_validate(json.loads(manifest_path.read_text()))
        except (ValidationError, json.JSONDecodeError) as e:
            raise HTTPException(status_code=400, detail=f"Invalid manifest: {e}")

        app_id = manifest.app_id
        install_path = bootstrapper.apps_dir / app_id  # type: ignore[union-attr]

        if install_path.exists():
            shutil.rmtree(install_path)
        shutil.copytree(source, install_path)
        logger.info("Installed %s from %s", app_id, source)

        async with engine.begin() as conn:
            await conn.execute(text(
                "INSERT INTO shared.installed_apps "
                "(app_id, source_path, install_path, scope) "
                "VALUES (:app_id, :source, :install, :scope) "
                "ON CONFLICT (app_id) DO UPDATE SET "
                "source_path = EXCLUDED.source_path, "
                "install_path = EXCLUDED.install_path, "
                "scope = EXCLUDED.scope, "
                "installed_at = NOW()"
            ), {
                "app_id": app_id,
                "source": str(source),
                "install": str(install_path),
                "scope": req.scope,
            })

        await bootstrapper.load_app(app_id, req.scope)  # type: ignore[union-attr]
        return {"status": "running", "app_id": app_id}

    @router.post("/update")
    async def update(req: UpdateRequest) -> dict[str, str]:
        async with engine.connect() as conn:
            result = await conn.execute(text(
                "SELECT source_path, scope FROM shared.installed_apps WHERE app_id = :app_id"
            ), {"app_id": req.app_id})
            row = result.fetchone()

        if row is None:
            raise HTTPException(status_code=404, detail=f"App {req.app_id!r} not in registry")

        source = Path(row[0])
        scope = row[1]

        bootstrapper.unload_app(req.app_id)  # type: ignore[union-attr]

        install_path = bootstrapper.apps_dir / req.app_id  # type: ignore[union-attr]
        if install_path.exists():
            shutil.rmtree(install_path)
        shutil.copytree(source, install_path)
        logger.info("Updated %s from %s", req.app_id, source)

        await bootstrapper.load_app(req.app_id, scope)  # type: ignore[union-attr]
        return {"status": "running", "app_id": req.app_id}

    return router
```

- [ ] **Step 4: Update `core/main.py` — mount installer router**

```python
# core/main.py
from __future__ import annotations
from contextlib import asynccontextmanager
from collections.abc import AsyncGenerator
from pathlib import Path
from fastapi import FastAPI
from core.loader import Bootstrapper
from core.eventbus import EventBus
from core.installer import create_installer_router
from core.registry import TenantRegistry
from shared.database import init_db


event_bus = EventBus()
registry = TenantRegistry()
bootstrapper = Bootstrapper(
    apps_dir=Path("apps"),
    data_dir=Path("data/apps"),
)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    await init_db()
    await bootstrapper.bootstrap(app, event_bus, registry)
    yield


app = FastAPI(title="Belgrade AI OS", lifespan=lifespan)
app.include_router(create_installer_router(bootstrapper))


@app.get("/")
async def root() -> dict[str, str]:
    return {"message": "Zdravo!", "status": "running"}


@app.get("/health")
async def health() -> dict[str, object]:
    return {"status": "ok", "apps": len(bootstrapper._manifests)}
```

- [ ] **Step 5: Run installer tests**

```bash
pytest tests/core/test_installer.py -v
```

Expected: 3 tests PASS

- [ ] **Step 6: Run full test suite**

```bash
pytest -v 2>&1 | tail -20
```

Expected: all tests pass

- [ ] **Step 7: Run mypy**

```bash
mypy core/installer.py core/loader.py core/main.py
```

Expected: no new errors

- [ ] **Step 8: Commit**

```bash
git add core/installer.py core/main.py tests/core/test_installer.py
git commit -m "feat: AppInstaller — install + update with hot-reload via load_app/unload_app"
```

---

## Self-Review Checklist

**Spec coverage:**
- [x] `shared.installed_apps` DDL — Task 1
- [x] `InstalledAppRow` ORM model — Task 2
- [x] `EventBus.unregister_app` — Task 3
- [x] `_schema_name()` helper + scope-aware provisioning — Task 4
- [x] `Bootstrapper` instance attrs for runtime state — Task 4
- [x] `Bootstrapper.load_app(app_id, scope)` — Task 4
- [x] `Bootstrapper.unload_app(app_id)` — Task 4
- [x] sys.modules eviction on unload — Task 4
- [x] Seed from `apps/` on first boot — Task 5
- [x] `discover_from_registry()` reads from DB — Task 5
- [x] `POST /installer/install` — validates manifest, copies, writes registry, hot-loads — Task 6
- [x] `POST /installer/update` — looks up source from registry, unloads, re-copies, hot-loads — Task 6
- [x] Returns `{ status: "running", app_id }` when install completes — Task 6
- [x] 400 if manifest.json missing — Task 6
- [x] 404 if app_id not in registry for update — Task 6

**Type consistency:**
- `_schema_name(app_id: str, scope: str) -> str` — used in `_provision_db_schema` and tested in Task 4
- `Bootstrapper.load_app(app_id: str, scope: str = "shared")` — called from installer Task 6
- `Bootstrapper.unload_app(app_id: str)` — called from installer Task 6
- `EventBus.unregister_app(app_id: str)` — called from `unload_app` in Task 4, defined in Task 3
