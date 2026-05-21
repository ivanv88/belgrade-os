# Phase 3b — Tenant Model Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the file-based `identity.json` user model with a DB-backed tenant + user model, making `ctx.tenant` and `ctx.user` live values resolved from the database at request time.

**Architecture:** Two new tables (`shared.tenants`, `shared.users`) store the household and its members. A `TenantRegistry` loads them at startup into an in-memory map keyed by email. HTTP hook requests resolve `Identity.email` → `(User, Tenant)` via the registry; cron jobs use the first registered user as the default. `identity.json` is removed entirely.

**Tech Stack:** Python 3.12+, FastAPI, SQLModel, asyncpg, pydantic, pytest, mypy (strict).

---

## File Map

| Action | File | Responsibility |
|--------|------|----------------|
| Modify | `shared/database.py` | Add `shared.tenants` + `shared.users` DDL to `SHARED_SCHEMA_SQL` |
| Create | `shared/tenants.py` | SQLModel ORM rows: `TenantRow`, `UserRow` |
| Create | `core/models/tenant.py` | Frozen `Tenant` dataclass (domain model, not ORM) |
| Modify | `core/models/user.py` | Add `user_id`, remove `load_identity()` |
| Create | `core/registry.py` | `TenantRegistry`: loads from DB, resolves email → `(User, Tenant)` |
| Modify | `core/context.py` | Add `tenant: Tenant` field to `AppContext` |
| Modify | `core/loader.py` | Use `TenantRegistry` in `bootstrap()`, update `build_ctx_factory` + `_register_hook` |
| Modify | `core/main.py` | Instantiate `TenantRegistry`, pass to `bootstrapper.bootstrap()` |
| Modify | `tests/shared/test_database.py` | Assert new table names in `SHARED_SCHEMA_SQL` |
| Create | `tests/shared/test_tenants.py` | Unit tests for `TenantRow` + `UserRow` |
| Create | `tests/core/models/test_tenant.py` | Unit tests for `Tenant` dataclass |
| Modify | `tests/core/models/test_user.py` | Update for new `User` shape (no `load_identity`, add `user_id`) |
| Create | `tests/core/test_registry.py` | Unit tests for `TenantRegistry` |

---

### Task 1: DB Schema DDL — `shared.tenants` + `shared.users`

**Files:**
- Modify: `shared/database.py`
- Modify: `tests/shared/test_database.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/shared/test_database.py — add to existing file
def test_shared_schema_sql_contains_tenant_tables() -> None:
    from shared.database import SHARED_SCHEMA_SQL
    assert "CREATE TABLE IF NOT EXISTS shared.tenants" in SHARED_SCHEMA_SQL
    assert "CREATE TABLE IF NOT EXISTS shared.users" in SHARED_SCHEMA_SQL
    assert "idx_users_email" in SHARED_SCHEMA_SQL
    assert "REFERENCES shared.tenants" in SHARED_SCHEMA_SQL
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/shared/test_database.py::test_shared_schema_sql_contains_tenant_tables -v
```

Expected: FAIL — strings not found in `SHARED_SCHEMA_SQL`

- [ ] **Step 3: Append DDL to `SHARED_SCHEMA_SQL` in `shared/database.py`**

Find the end of the `SHARED_SCHEMA_SQL` string (currently ends before the closing `"""`). Append:

```python
SHARED_SCHEMA_SQL = """
CREATE SCHEMA IF NOT EXISTS shared;

CREATE TABLE IF NOT EXISTS shared.config (
    namespace   VARCHAR NOT NULL,
    data        JSONB NOT NULL,
    updated_at  TIMESTAMP DEFAULT NOW(),
    PRIMARY KEY (namespace, updated_at)
);

CREATE INDEX IF NOT EXISTS idx_shared_config_data
    ON shared.config USING GIN (data);

CREATE OR REPLACE VIEW shared.current_metrics AS
SELECT DISTINCT ON (namespace) namespace, data, updated_at
FROM shared.config
ORDER BY namespace, updated_at DESC;

CREATE TABLE IF NOT EXISTS shared.tenants (
    tenant_id   TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    created_at  TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS shared.users (
    user_id     TEXT PRIMARY KEY,
    tenant_id   TEXT NOT NULL REFERENCES shared.tenants(tenant_id),
    email       TEXT NOT NULL UNIQUE,
    name        TEXT NOT NULL,
    timezone    TEXT NOT NULL DEFAULT 'UTC',
    locale      TEXT NOT NULL DEFAULT 'en-US',
    created_at  TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_users_email ON shared.users (email);
"""
```

- [ ] **Step 4: Run test to verify it passes**

```bash
pytest tests/shared/test_database.py -v
```

Expected: all 4 tests PASS

- [ ] **Step 5: Commit**

```bash
git add shared/database.py tests/shared/test_database.py
git commit -m "feat: add shared.tenants + shared.users DDL"
```

---

### Task 2: SQLModel ORM rows — `shared/tenants.py`

**Files:**
- Create: `shared/tenants.py`
- Create: `tests/shared/test_tenants.py`

These are ORM-layer objects used only for type-safe DB queries. They never leave `shared/` — the domain models in `core/models/` are what the rest of the platform uses.

- [ ] **Step 1: Write the failing tests**

```python
# tests/shared/test_tenants.py
from __future__ import annotations


def test_tenant_row_fields() -> None:
    from shared.tenants import TenantRow
    row = TenantRow(tenant_id="t1", name="Household A")
    assert row.tenant_id == "t1"
    assert row.name == "Household A"
    assert row.created_at is None


def test_user_row_fields() -> None:
    from shared.tenants import UserRow
    row = UserRow(
        user_id="u1",
        tenant_id="t1",
        email="ivan@example.com",
        name="Ivan",
    )
    assert row.email == "ivan@example.com"
    assert row.timezone == "UTC"
    assert row.locale == "en-US"


def test_user_row_custom_locale() -> None:
    from shared.tenants import UserRow
    row = UserRow(
        user_id="u1",
        tenant_id="t1",
        email="ivan@example.com",
        name="Ivan",
        timezone="Europe/Belgrade",
        locale="sr-RS",
    )
    assert row.timezone == "Europe/Belgrade"
    assert row.locale == "sr-RS"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/shared/test_tenants.py -v
```

Expected: `ModuleNotFoundError: No module named 'shared.tenants'`

- [ ] **Step 3: Write minimal implementation**

```python
# shared/tenants.py
from __future__ import annotations
from datetime import datetime
from typing import Optional
from sqlmodel import Field, SQLModel


class TenantRow(SQLModel, table=True):
    __tablename__ = "tenants"  # type: ignore[assignment]
    __table_args__ = {"schema": "shared"}

    tenant_id: str = Field(primary_key=True)
    name: str
    created_at: Optional[datetime] = Field(default=None)


class UserRow(SQLModel, table=True):
    __tablename__ = "users"  # type: ignore[assignment]
    __table_args__ = {"schema": "shared"}

    user_id: str = Field(primary_key=True)
    tenant_id: str = Field(foreign_key="shared.tenants.tenant_id")
    email: str = Field(unique=True)
    name: str
    timezone: str = Field(default="UTC")
    locale: str = Field(default="en-US")
    created_at: Optional[datetime] = Field(default=None)
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/shared/test_tenants.py -v
```

Expected: 3 tests PASS

- [ ] **Step 5: Commit**

```bash
git add shared/tenants.py tests/shared/test_tenants.py
git commit -m "feat: add TenantRow + UserRow SQLModel ORM models"
```

---

### Task 3: Domain models — `Tenant` + updated `User`

**Files:**
- Create: `core/models/tenant.py`
- Modify: `core/models/user.py`
- Create: `tests/core/models/test_tenant.py`
- Modify: `tests/core/models/test_user.py`

`Tenant` is a frozen dataclass (same pattern as `Identity`). `User` gains `user_id` and loses `load_identity()` — it is now always constructed from DB data, never from JSON.

- [ ] **Step 1: Write failing tests for `Tenant`**

```python
# tests/core/models/test_tenant.py
from __future__ import annotations
import pytest
from dataclasses import FrozenInstanceError
from core.models.tenant import Tenant


def test_tenant_stores_fields() -> None:
    t = Tenant(tenant_id="t1", name="Household A")
    assert t.tenant_id == "t1"
    assert t.name == "Household A"


def test_tenant_is_frozen() -> None:
    t = Tenant(tenant_id="t1", name="Household A")
    with pytest.raises(FrozenInstanceError):
        t.tenant_id = "t2"  # type: ignore[misc]


def test_tenant_equality() -> None:
    a = Tenant(tenant_id="t1", name="Household A")
    b = Tenant(tenant_id="t1", name="Household A")
    assert a == b
    c = Tenant(tenant_id="t2", name="Household B")
    assert a != c
```

- [ ] **Step 2: Write failing tests for updated `User`**

Replace the entire contents of `tests/core/models/test_user.py`:

```python
# tests/core/models/test_user.py
from __future__ import annotations
import pytest
from pydantic import ValidationError
from core.models.user import User


def test_user_stores_fields() -> None:
    user = User(user_id="u1", email="ivan@example.com", name="Ivan")
    assert user.user_id == "u1"
    assert user.email == "ivan@example.com"
    assert user.name == "Ivan"


def test_user_defaults() -> None:
    user = User(user_id="u1", email="ivan@example.com", name="Ivan")
    assert user.timezone == "UTC"
    assert user.locale == "en-US"


def test_user_custom_locale() -> None:
    user = User(
        user_id="u1",
        email="ivan@example.com",
        name="Ivan",
        timezone="Europe/Belgrade",
        locale="sr-RS",
    )
    assert user.timezone == "Europe/Belgrade"


def test_user_requires_user_id_email_name() -> None:
    with pytest.raises(ValidationError):
        User.model_validate({"email": "ivan@example.com", "name": "Ivan"})
```

- [ ] **Step 3: Run tests to verify they fail**

```bash
pytest tests/core/models/test_tenant.py tests/core/models/test_user.py -v
```

Expected: `test_tenant.py` — `ModuleNotFoundError`. `test_user.py` — some pass, `test_user_requires_user_id_email_name` fails (user_id not yet required).

- [ ] **Step 4: Implement `core/models/tenant.py`**

```python
# core/models/tenant.py
from __future__ import annotations
from dataclasses import dataclass


@dataclass(frozen=True)
class Tenant:
    tenant_id: str
    name: str
```

- [ ] **Step 5: Update `core/models/user.py`**

```python
# core/models/user.py
from __future__ import annotations
from pydantic import BaseModel


class User(BaseModel):
    user_id: str
    email: str
    name: str
    timezone: str = "UTC"
    locale: str = "en-US"
```

- [ ] **Step 6: Run tests to verify they pass**

```bash
pytest tests/core/models/test_tenant.py tests/core/models/test_user.py -v
```

Expected: 7 tests PASS

- [ ] **Step 7: Run full suite to check for regressions**

```bash
pytest -v 2>&1 | tail -15
```

Expected: only `tests/core/models/test_user.py` tests changed. `test_loader.py` may fail because `load_identity` is gone from the import — check and fix the import in `core/loader.py` if needed (remove `load_identity` import; the next task wires the replacement).

- [ ] **Step 8: Commit**

```bash
git add core/models/tenant.py core/models/user.py tests/core/models/test_tenant.py tests/core/models/test_user.py
git commit -m "feat: add Tenant domain model, add user_id to User, remove load_identity"
```

---

### Task 4: `TenantRegistry`

**Files:**
- Create: `core/registry.py`
- Create: `tests/core/test_registry.py`

`TenantRegistry` is an in-memory map populated at startup from the DB. It never hits the DB per-request — everything is resolved from `_map`. `resolve()` raises HTTP 403 for unknown emails so unenrolled users get a clear rejection. `resolve_default()` is used by cron jobs that have no request identity.

- [ ] **Step 1: Write the failing tests**

```python
# tests/core/test_registry.py
from __future__ import annotations
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi import HTTPException
from core.registry import TenantRegistry
from core.models.user import User
from core.models.tenant import Tenant


def _make_registry(email: str = "ivan@example.com") -> TenantRegistry:
    registry = TenantRegistry()
    user = User(user_id="u1", email=email, name="Ivan", timezone="Europe/Belgrade", locale="sr-RS")
    tenant = Tenant(tenant_id="t1", name="Household")
    registry._map[email] = (user, tenant)
    return registry


def test_resolve_known_email() -> None:
    registry = _make_registry()
    user, tenant = registry.resolve("ivan@example.com")
    assert user.email == "ivan@example.com"
    assert tenant.tenant_id == "t1"


def test_resolve_unknown_email_raises_403() -> None:
    registry = TenantRegistry()
    with pytest.raises(HTTPException) as exc:
        registry.resolve("unknown@example.com")
    assert exc.value.status_code == 403


def test_resolve_default_returns_first_entry() -> None:
    registry = _make_registry()
    result = registry.resolve_default()
    assert result is not None
    user, tenant = result
    assert user.email == "ivan@example.com"


def test_resolve_default_empty_returns_none() -> None:
    registry = TenantRegistry()
    assert registry.resolve_default() is None


@pytest.mark.asyncio
async def test_load_populates_map() -> None:
    registry = TenantRegistry()

    fake_rows = [
        {
            "user_id": "u1", "email": "ivan@example.com", "name": "Ivan",
            "timezone": "UTC", "locale": "en-US",
            "tenant_id": "t1", "tenant_name": "Household",
        }
    ]

    mock_result = MagicMock()
    mock_result.mappings.return_value = fake_rows

    mock_conn = AsyncMock()
    mock_conn.execute = AsyncMock(return_value=mock_result)

    mock_ctx = MagicMock()
    mock_ctx.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_ctx.__aexit__ = AsyncMock(return_value=None)

    with patch("core.registry.engine") as mock_engine:
        mock_engine.connect = MagicMock(return_value=mock_ctx)
        await registry.load()

    assert "ivan@example.com" in registry._map
    user, tenant = registry._map["ivan@example.com"]
    assert user.name == "Ivan"
    assert tenant.tenant_id == "t1"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/core/test_registry.py -v
```

Expected: `ModuleNotFoundError: No module named 'core.registry'`

- [ ] **Step 3: Write minimal implementation**

```python
# core/registry.py
from __future__ import annotations
import logging
from typing import Optional
from sqlalchemy import text
from fastapi import HTTPException
from core.models.tenant import Tenant
from core.models.user import User

logger = logging.getLogger(__name__)


class TenantRegistry:
    def __init__(self) -> None:
        self._map: dict[str, tuple[User, Tenant]] = {}

    async def load(self) -> None:
        from shared.database import engine
        async with engine.connect() as conn:
            rows = await conn.execute(text(
                "SELECT u.user_id, u.email, u.name, u.timezone, u.locale,"
                "       t.tenant_id, t.name AS tenant_name"
                " FROM shared.users u"
                " JOIN shared.tenants t ON t.tenant_id = u.tenant_id"
            ))
            for row in rows.mappings():
                user = User(
                    user_id=row["user_id"],
                    email=row["email"],
                    name=row["name"],
                    timezone=row["timezone"],
                    locale=row["locale"],
                )
                tenant = Tenant(tenant_id=row["tenant_id"], name=row["tenant_name"])
                self._map[row["email"]] = (user, tenant)
        logger.info("TenantRegistry loaded %d user(s)", len(self._map))

    def resolve(self, email: str) -> tuple[User, Tenant]:
        entry = self._map.get(email)
        if entry is None:
            logger.error("No tenant entry for email: %s", email)
            raise HTTPException(status_code=403, detail="Access denied — no tenant for identity")
        return entry

    def resolve_default(self) -> Optional[tuple[User, Tenant]]:
        if not self._map:
            return None
        return next(iter(self._map.values()))
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/core/test_registry.py -v
```

Expected: 5 tests PASS

- [ ] **Step 5: Run mypy**

```bash
mypy core/registry.py
```

Expected: Success

- [ ] **Step 6: Commit**

```bash
git add core/registry.py tests/core/test_registry.py
git commit -m "feat: add TenantRegistry — loads users+tenants from DB, resolves by email"
```

---

### Task 5: Wire registry into AppContext + Bootstrapper

**Files:**
- Modify: `core/context.py`
- Modify: `core/loader.py`
- Modify: `core/main.py`

This task connects everything. `AppContext` gains `tenant`. `bootstrap()` loads the registry instead of reading `identity.json`. Hook handlers resolve `user` + `tenant` per-request from the registry. Cron jobs use `resolve_default()`.

**Note:** After this task, the platform requires at least one row in `shared.tenants` and `shared.users` to start successfully. Seed data must be inserted manually before first boot:

```sql
INSERT INTO shared.tenants (tenant_id, name) VALUES ('household-a', 'My Household');
INSERT INTO shared.users (user_id, tenant_id, email, name, timezone, locale)
VALUES ('user-1', 'household-a', 'you@example.com', 'Your Name', 'Europe/Belgrade', 'sr-RS');
```

- [ ] **Step 1: Update `core/context.py` — add `tenant` field**

```python
# core/context.py
from __future__ import annotations
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict

logger = logging.getLogger(__name__)
from sqlalchemy.ext.asyncio import AsyncSession
from core.io import IOAdapter
from core.models.user import User
from core.models.tenant import Tenant
from shared.ntfy import NotifyService


@dataclass
class AppMeta:
    app_id: str
    timestamp: datetime
    secrets: Dict[str, str]


class AppContext:
    def __init__(
        self,
        user: User,
        tenant: Tenant,
        metrics: Any,
        db: AsyncSession,
        io: IOAdapter,
        event_bus: Any,
        notify: NotifyService,
        meta: AppMeta,
    ) -> None:
        self.user = user
        self.tenant = tenant
        self.metrics = metrics
        self.db = db
        self.io = io
        self.notify = notify
        self.meta = meta
        self._event_bus = event_bus

    async def emit(self, topic: str, data: Any) -> None:
        await self._event_bus.emit(topic, data)

    async def cleanup(self) -> None:
        try:
            await self.db.rollback()
        except Exception as e:
            logger.warning("DB rollback failed during cleanup: %s", e)
        try:
            await self.db.close()
        except Exception as e:
            logger.warning("DB close failed during cleanup: %s", e)
```

- [ ] **Step 2: Run existing context tests to confirm no regression**

```bash
pytest tests/core/test_context.py -v
```

Expected: tests that construct `AppContext` will fail because `tenant` is now required. Note which tests fail — they need `tenant=` added.

Fix the failing tests by adding `tenant=Tenant(tenant_id="t1", name="Test")` to each `AppContext(...)` call in `tests/core/test_context.py`.

- [ ] **Step 3: Update `core/loader.py`**

Replace the full file with:

```python
# core/loader.py
from __future__ import annotations
import importlib
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from fastapi import FastAPI, Request
from pydantic import ValidationError
from sqlalchemy import text

from core.context import AppContext, AppMeta
from core.eventbus import EventBus
from core.executor import safe_execute
from core.io import LocalAdapter
from core.models.manifest import AppManifest
from core.models.tenant import Tenant
from core.models.user import User
from core.registry import TenantRegistry
from shared.database import engine, AsyncSessionLocal
from shared.ntfy import NotifyService

logger = logging.getLogger(__name__)


class Bootstrapper:
    def __init__(self, apps_dir: Path, data_dir: Path) -> None:
        self.apps_dir = apps_dir
        self.data_dir = data_dir
        self.subscription_map: Dict[str, List[str]] = {}
        self._manifests: List[AppManifest] = []

    def discover(self) -> List[AppManifest]:
        manifests: List[AppManifest] = []
        for item in self.apps_dir.iterdir():
            if not item.is_dir():
                continue
            manifest_path = item / "manifest.json"
            if not manifest_path.exists():
                continue
            try:
                data = json.loads(manifest_path.read_text())
                manifests.append(AppManifest.model_validate(data))
            except (ValidationError, json.JSONDecodeError) as e:
                logger.warning("Skipping %s: %s", item.name, e)
        self._manifests = manifests
        return manifests

    def build_subscription_map(self, manifests: List[AppManifest]) -> None:
        for manifest in manifests:
            for trigger in manifest.triggers:
                if trigger.type == "event" and trigger.topic:
                    self.subscription_map.setdefault(trigger.topic, []).append(manifest.app_id)

    async def _provision_db_schema(self, app_id: str) -> None:
        safe_id = app_id.replace("-", "_")
        async with engine.begin() as conn:
            await conn.execute(text(f"CREATE SCHEMA IF NOT EXISTS app_{safe_id}"))

    async def provision(self, app_id: str) -> None:
        app_data_dir = self.data_dir / app_id
        app_data_dir.mkdir(parents=True, exist_ok=True)
        await self._provision_db_schema(app_id)

    def _register_app_schemas(self, app_id: str, event_bus: EventBus) -> None:
        try:
            mod = importlib.import_module(f"apps.{app_id}.events")
            for topic, schema in getattr(mod, "EVENT_SCHEMAS", {}).items():
                event_bus.register_schema(topic, schema)
                logger.info("Registered schema for %s", topic)
        except ImportError:
            pass

    def _load_metrics_schema(self, app_id: str) -> Optional[Any]:
        try:
            mod = importlib.import_module(f"apps.{app_id}.metrics")
            return getattr(mod, "MetricsSchema", None)
        except ImportError:
            return None

    def build_ctx_factory(
        self,
        manifest: AppManifest,
        default_user: User,
        default_tenant: Tenant,
        event_bus: EventBus,
        notify: NotifyService,
        registry: TenantRegistry,
    ) -> Callable[[Optional[tuple[User, Tenant]]], Any]:
        base_path = self.data_dir / manifest.app_id
        metrics_schema = self._load_metrics_schema(manifest.app_id)

        async def factory(identity_ctx: Optional[tuple[User, Tenant]] = None) -> AppContext:
            user, tenant = identity_ctx if identity_ctx else (default_user, default_tenant)
            session = AsyncSessionLocal()
            io = LocalAdapter(base_path)
            metrics: Any = {}
            if metrics_schema:
                async with engine.connect() as conn:
                    result = await conn.execute(
                        text(
                            "SELECT data FROM shared.current_metrics"
                            " WHERE namespace = 'user.metrics'"
                        )
                    )
                    row = result.fetchone()
                    if row:
                        metrics = metrics_schema.model_validate(dict(row[0]))
            return AppContext(
                user=user,
                tenant=tenant,
                metrics=metrics,
                db=session,
                io=io,
                event_bus=event_bus,
                notify=notify,
                meta=AppMeta(
                    app_id=manifest.app_id,
                    timestamp=datetime.now(),
                    secrets={},
                ),
            )

        return factory

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

            for trigger in manifest.triggers:
                if trigger.type == "cron" and trigger.schedule:
                    async def cron_job(
                        mid: str = manifest.app_id,
                        factory: Callable[[], Any] = ctx_factory,
                    ) -> None:
                        ctx = await factory()
                        await safe_execute(mid, ctx, event_bus)

                    scheduler.add_job(cron_job, "cron", **_parse_cron(trigger.schedule))

                elif trigger.type == "hook":
                    _register_hook(app, manifest.app_id, ctx_factory, event_bus, registry)

                elif trigger.type == "observer":
                    logger.warning(
                        "Observer trigger for %s: watchdog not yet wired",
                        manifest.app_id,
                    )

            if manifest.mcp_enabled:
                logger.info("MCP tool pending for %s (Task 13)", manifest.app_id)

            logger.info("Bootstrapped: %s", manifest.app_id)

        scheduler.start()


def _parse_cron(schedule: str) -> Dict[str, str]:
    minute, hour, day, month, day_of_week = schedule.split()
    return dict(minute=minute, hour=hour, day=day, month=month, day_of_week=day_of_week)


def _register_hook(
    app: FastAPI,
    app_id: str,
    ctx_factory: Callable[[Optional[tuple[User, Tenant]]], Any],
    event_bus: EventBus,
    registry: TenantRegistry,
) -> None:
    from core.auth import resolve_identity

    async def handler(request: Request) -> Dict[str, str]:
        identity = resolve_identity(dict(request.headers))
        identity_ctx = registry.resolve(identity.email)
        ctx = await ctx_factory(identity_ctx)
        await safe_execute(app_id, ctx, event_bus)
        return {"status": "ok", "app_id": app_id}

    app.add_api_route(f"/{app_id}/run", handler, methods=["POST"])
```

- [ ] **Step 4: Update `core/main.py`**

```python
# core/main.py
from __future__ import annotations
from contextlib import asynccontextmanager
from collections.abc import AsyncGenerator
from pathlib import Path
from fastapi import FastAPI
from core.loader import Bootstrapper
from core.eventbus import EventBus
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


@app.get("/")
async def root() -> dict[str, str]:
    return {"message": "Zdravo!", "status": "running"}


@app.get("/health")
async def health() -> dict[str, object]:
    return {"status": "ok", "apps": len(bootstrapper._manifests)}
```

- [ ] **Step 5: Run full test suite**

```bash
pytest -v 2>&1 | tail -20
```

Expected: all tests pass. Fix any test that constructs `AppContext` directly — add `tenant=Tenant(tenant_id="t1", name="Test")`. Fix any test that imports `load_identity` — remove those imports (the function is gone).

- [ ] **Step 6: Run mypy**

```bash
mypy .
```

Expected: no new errors introduced by this task

- [ ] **Step 7: Commit**

```bash
git add core/context.py core/loader.py core/main.py
git commit -m "feat: wire TenantRegistry into AppContext + Bootstrapper, remove identity.json"
```

---

## Self-Review Checklist

**Spec coverage:**
- [x] `shared.tenants` + `shared.users` DDL — Task 1
- [x] SQLModel ORM rows for both tables — Task 2
- [x] `Tenant` frozen dataclass — Task 3
- [x] `User` model updated (user_id, no load_identity) — Task 3
- [x] `TenantRegistry.load()` from DB — Task 4
- [x] `TenantRegistry.resolve(email)` raises 403 for unknown — Task 4
- [x] `TenantRegistry.resolve_default()` for cron jobs — Task 4
- [x] `AppContext.tenant` added — Task 5
- [x] `bootstrap()` uses registry instead of identity.json — Task 5
- [x] Hook handler resolves user+tenant per-request — Task 5
- [x] Cron jobs use resolve_default() — Task 5
- [x] `identity.json` dependency removed — Task 5

**Type consistency:**
- `Tenant(tenant_id: str, name: str)` — used consistently across Tasks 3, 4, 5
- `User(user_id, email, name, timezone, locale)` — matches Task 3 definition
- `TenantRegistry.resolve()` → `tuple[User, Tenant]` — matches factory signature in Task 5
- `build_ctx_factory()` identity_ctx parameter: `Optional[tuple[User, Tenant]]` — consistent Tasks 4→5
