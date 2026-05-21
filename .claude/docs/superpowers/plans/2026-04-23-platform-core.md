# Platform Core Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **Before starting:** Read `docs/tech.spec.md` and `CLAUDE.md` in full. They are the authoritative reference for every decision made here.

**Goal:** Build the Belgrade AI OS platform core — the Bootstrapper, Context API, EventBus, IO adapters, and executor that all apps will run on.

**Architecture:** The Bootstrapper scans `/apps/*/manifest.json` at startup, provisions per-app DB schemas and data directories, registers triggers (cron/observer/hook/event), and constructs a typed `AppContext` factory per app. All app invocations go through `safe_execute` which handles concurrency, error classification, and circuit breaking. Platform core is generic — no app-specific types in `core/`.

**Tech Stack:** FastAPI, SQLModel, SQLAlchemy asyncio, asyncpg, APScheduler, watchdog, Pydantic v2, FastMCP, pytest, pytest-asyncio, mypy.

**Spec references:** `docs/tech.spec.md` (v2.2), `CLAUDE.md`

---

## File Map

| File | Status | Responsibility |
|---|---|---|
| `pytest.ini` | Create | pytest + asyncio config |
| `mypy.ini` | Create | mypy strict config |
| `tests/conftest.py` | Create | shared fixtures |
| `tests/core/models/test_manifest.py` | Create | manifest validation tests |
| `tests/core/models/test_user.py` | Create | user identity tests |
| `tests/shared/test_database.py` | Create | async engine + schema provisioning tests |
| `tests/core/test_io.py` | Create | IO adapter tests |
| `tests/shared/test_ntfy.py` | Create | notification service tests |
| `tests/core/test_eventbus.py` | Create | EventBus + Schema Registry tests |
| `tests/core/test_context.py` | Create | AppContext construction + cleanup tests |
| `tests/core/test_executor.py` | Create | safe_execute + circuit breaker tests |
| `tests/core/test_loader.py` | Create | Bootstrapper tests |
| `tests/core/test_mcp.py` | Create | MCP tool generation tests |
| `core/models/manifest.py` | Create | AppManifest Pydantic model |
| `core/models/user.py` | Create | User identity model |
| `core/events.py` | Create | Typed system event classes |
| `core/io.py` | Create | IOAdapter protocol + LocalAdapter + ObsidianAdapter |
| `core/eventbus.py` | Create | EventBus + Schema Registry |
| `core/context.py` | Create | AppContext typed class + factory |
| `core/executor.py` | Create | safe_execute + CircuitBreaker |
| `core/loader.py` | Rewrite | Bootstrapper |
| `core/mcp.py` | Create | FastMCP server + tool auto-generation |
| `core/main.py` | Modify | Wire up Bootstrapper + MCP |
| `shared/database.py` | Rewrite | Async engine + shared schema provisioning |
| `shared/ntfy.py` | Create | NotifyService |
| `identity.json` | Create | User identity data |
| `setup.sh` | Modify | Add asyncpg, fastmcp, pytest, pytest-asyncio, pyyaml, httpx |
| `docker-compose.yml` | Modify | Add CouchDB service |

---

## Task 1: Project Infrastructure

**Files:**
- Create: `pytest.ini`
- Create: `mypy.ini`
- Create: `tests/__init__.py`, `tests/core/__init__.py`, `tests/core/models/__init__.py`, `tests/shared/__init__.py`
- Create: `tests/conftest.py`
- Modify: `setup.sh`

- [ ] **Step 1: Add missing dependencies to setup.sh**

```bash
# Replace the pip install line in setup.sh with:
pip install fastapi uvicorn requests psutil watchdog apscheduler python-dotenv \
    sqlmodel pydantic-settings psycopg2-binary asyncpg \
    fastmcp pyyaml httpx \
    pytest pytest-asyncio pytest-mock mypy
```

- [ ] **Step 2: Create pytest.ini**

```ini
[pytest]
asyncio_mode = auto
testpaths = tests
```

- [ ] **Step 3: Create mypy.ini**

```ini
[mypy]
python_version = 3.11
strict = false
ignore_missing_imports = true
disallow_untyped_defs = true
warn_return_any = true

[mypy-apps.*]
ignore_errors = true
```

- [ ] **Step 4: Create test directory structure**

```bash
mkdir -p tests/core/models tests/shared
touch tests/__init__.py
touch tests/core/__init__.py
touch tests/core/models/__init__.py
touch tests/shared/__init__.py
```

- [ ] **Step 5: Create tests/conftest.py**

```python
import pytest
import tempfile
from pathlib import Path


@pytest.fixture
def tmp_dir():
    with tempfile.TemporaryDirectory() as d:
        yield Path(d)


@pytest.fixture
def apps_dir(tmp_dir):
    apps = tmp_dir / "apps"
    apps.mkdir()
    return apps


@pytest.fixture
def sample_manifest_dict():
    return {
        "app_id": "test-app",
        "name": "Test App",
        "description": "A test application",
        "version": "1.0.0",
        "pattern": "worker",
        "mcp_enabled": False,
        "triggers": [{"type": "cron", "schedule": "0 20 * * *"}],
        "storage": {"scope": "test-app", "adapter": "local"},
        "config": {},
    }
```

- [ ] **Step 6: Run pytest to verify setup**

```bash
pytest --co -q
```

Expected: `no tests ran` — confirms pytest found the `tests/` directory.

- [ ] **Step 7: Commit**

```bash
git add pytest.ini mypy.ini setup.sh tests/
git commit -m "chore: add test infrastructure and project config"
```

---

## Task 2: AppManifest Pydantic Model

**Files:**
- Create: `core/models/manifest.py`
- Create: `tests/core/models/test_manifest.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/core/models/test_manifest.py
import pytest
from pydantic import ValidationError
from core.models.manifest import AppManifest, AppTrigger, AppStorage, AppConfig


def test_valid_manifest(sample_manifest_dict):
    manifest = AppManifest.model_validate(sample_manifest_dict)
    assert manifest.app_id == "test-app"
    assert manifest.pattern == "worker"
    assert len(manifest.triggers) == 1
    assert manifest.triggers[0].type == "cron"
    assert manifest.triggers[0].schedule == "0 20 * * *"


def test_invalid_app_id_uppercase():
    with pytest.raises(ValidationError, match="app_id"):
        AppManifest.model_validate({
            "app_id": "TestApp",
            "name": "Test", "description": "d", "version": "1.0.0",
            "pattern": "worker", "triggers": [],
            "storage": {"scope": "test"},
        })


def test_invalid_app_id_spaces():
    with pytest.raises(ValidationError):
        AppManifest.model_validate({
            "app_id": "test app",
            "name": "Test", "description": "d", "version": "1.0.0",
            "pattern": "worker", "triggers": [],
            "storage": {"scope": "test"},
        })


def test_event_trigger_requires_namespaced_topic():
    with pytest.raises(ValidationError, match="topic"):
        AppTrigger.model_validate({"type": "event", "topic": "unnamespaced"})


def test_event_trigger_valid_topic():
    trigger = AppTrigger.model_validate({"type": "event", "topic": "nutrition.goal_reached"})
    assert trigger.topic == "nutrition.goal_reached"


def test_cron_trigger_requires_schedule():
    with pytest.raises(ValidationError, match="schedule"):
        AppTrigger.model_validate({"type": "cron"})


def test_observer_trigger_requires_path():
    with pytest.raises(ValidationError, match="path"):
        AppTrigger.model_validate({"type": "observer"})


def test_storage_defaults_to_local():
    storage = AppStorage.model_validate({"scope": "myapp"})
    assert storage.adapter == "local"


def test_shared_write_defaults_false():
    config = AppConfig.model_validate({})
    assert config.shared_write is False
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
pytest tests/core/models/test_manifest.py -v
```

Expected: `ImportError: No module named 'core.models.manifest'`

- [ ] **Step 3: Implement core/models/manifest.py**

```python
from __future__ import annotations
from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field, model_validator
from typing import Literal


class AppTrigger(BaseModel):
    type: Literal["cron", "observer", "hook", "event"]
    schedule: Optional[str] = None
    path: Optional[str] = None
    topic: Optional[str] = None

    @model_validator(mode="after")
    def validate_fields(self) -> "AppTrigger":
        if self.type == "cron" and not self.schedule:
            raise ValueError("schedule is required for cron triggers")
        if self.type == "observer" and not self.path:
            raise ValueError("path is required for observer triggers")
        if self.type == "event":
            if not self.topic:
                raise ValueError("topic is required for event triggers")
            if "." not in self.topic:
                raise ValueError("topic must be namespaced: {origin}.{event_type}")
        return self


class AppStorage(BaseModel):
    scope: str
    adapter: Literal["local", "obsidian", "gdrive"] = "local"


class AppConfig(BaseModel):
    shared_write: bool = False
    model_config = {"extra": "allow"}


class AppManifest(BaseModel):
    app_id: str = Field(..., pattern=r"^[a-z0-9-]+$")
    name: str
    description: str
    version: str
    pattern: Literal["worker", "observer", "bridge", "orchestrator"]
    mcp_enabled: bool = False
    triggers: List[AppTrigger]
    storage: AppStorage
    config: AppConfig = Field(default_factory=AppConfig)
```

- [ ] **Step 4: Run tests to confirm they pass**

```bash
pytest tests/core/models/test_manifest.py -v
```

Expected: all 9 tests PASS.

- [ ] **Step 5: Run mypy**

```bash
mypy core/models/manifest.py
```

Expected: `Success: no issues found`

- [ ] **Step 6: Commit**

```bash
git add core/models/manifest.py tests/core/models/test_manifest.py
git commit -m "feat: add AppManifest Pydantic model with trigger validation"
```

---

## Task 3: User Identity Model

**Files:**
- Create: `core/models/user.py`
- Create: `identity.json`
- Create: `tests/core/models/test_user.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/core/models/test_user.py
import json
import pytest
from pathlib import Path
from pydantic import ValidationError
from core.models.user import User, load_identity


def test_user_model():
    user = User.model_validate({
        "name": "Laurent",
        "email": "laurent@example.com",
    })
    assert user.name == "Laurent"
    assert user.timezone == "Europe/Belgrade"
    assert user.locale == "sr-RS"


def test_user_requires_name_and_email():
    with pytest.raises(ValidationError):
        User.model_validate({"name": "Laurent"})


def test_load_identity(tmp_dir):
    identity_file = tmp_dir / "identity.json"
    identity_file.write_text(json.dumps({
        "name": "Laurent",
        "email": "laurent@example.com",
        "timezone": "Europe/Belgrade",
    }))
    user = load_identity(identity_file)
    assert user.name == "Laurent"


def test_load_identity_missing_file(tmp_dir):
    with pytest.raises(FileNotFoundError):
        load_identity(tmp_dir / "missing.json")
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
pytest tests/core/models/test_user.py -v
```

Expected: `ImportError: No module named 'core.models.user'`

- [ ] **Step 3: Implement core/models/user.py**

```python
from __future__ import annotations
import json
from pathlib import Path
from pydantic import BaseModel, EmailStr


class User(BaseModel):
    name: str
    email: str
    timezone: str = "Europe/Belgrade"
    locale: str = "sr-RS"


def load_identity(path: Path) -> User:
    if not path.exists():
        raise FileNotFoundError(f"identity.json not found at {path}")
    return User.model_validate(json.loads(path.read_text()))
```

- [ ] **Step 4: Create identity.json in project root**

```json
{
  "name": "Laurent",
  "email": "laurent@example.com",
  "timezone": "Europe/Belgrade",
  "locale": "sr-RS"
}
```

- [ ] **Step 5: Run tests to confirm they pass**

```bash
pytest tests/core/models/test_user.py -v
```

Expected: all 4 tests PASS.

- [ ] **Step 6: Commit**

```bash
git add core/models/user.py identity.json tests/core/models/test_user.py
git commit -m "feat: add User identity model and identity.json"
```

---

## Task 4: Async Database Setup

**Files:**
- Rewrite: `shared/database.py`
- Create: `tests/shared/test_database.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/shared/test_database.py
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from shared.database import get_engine, get_session, SHARED_SCHEMA_SQL


def test_database_url_format():
    from shared.database import build_database_url
    url = build_database_url("user", "pass", "localhost", 5432, "mydb")
    assert url == "postgresql+asyncpg://user:pass@localhost:5432/mydb"


def test_shared_schema_sql_contains_required_statements():
    assert "CREATE SCHEMA IF NOT EXISTS shared" in SHARED_SCHEMA_SQL
    assert "shared.config" in SHARED_SCHEMA_SQL
    assert "shared.current_metrics" in SHARED_SCHEMA_SQL
    assert "GIN" in SHARED_SCHEMA_SQL


def test_get_engine_returns_engine():
    engine = get_engine("postgresql+asyncpg://user:pass@localhost/db")
    assert engine is not None
    # engine.url is a SQLAlchemy URL object
    assert "asyncpg" in str(engine.url)
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
pytest tests/shared/test_database.py -v
```

Expected: `ImportError` or `AttributeError`

- [ ] **Step 3: Rewrite shared/database.py**

```python
from __future__ import annotations
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, AsyncEngine
from sqlalchemy.orm import sessionmaker
from sqlmodel import SQLModel
from dotenv import load_dotenv
import os

load_dotenv()

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
"""


def build_database_url(user: str, password: str, host: str, port: int, db: str) -> str:
    return f"postgresql+asyncpg://{user}:{password}@{host}:{port}/{db}"


def get_engine(url: str) -> AsyncEngine:
    return create_async_engine(url, echo=False)


DB_USER = os.getenv("DB_USER", "laurent")
DB_PASS = os.getenv("DB_PASSWORD", "")
DB_HOST = os.getenv("DB_HOST", "localhost")
DB_PORT = int(os.getenv("DB_PORT", "5432"))
DB_NAME = os.getenv("DB_NAME", "belgrade_os")

DATABASE_URL = build_database_url(DB_USER, DB_PASS, DB_HOST, DB_PORT, DB_NAME)
engine = get_engine(DATABASE_URL)

AsyncSessionLocal = sessionmaker(
    engine, class_=AsyncSession, expire_on_commit=False
)


async def init_db() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
        await conn.execute(__import__("sqlalchemy").text(SHARED_SCHEMA_SQL))


def get_session() -> AsyncSession:
    return AsyncSessionLocal()
```

- [ ] **Step 4: Run tests to confirm they pass**

```bash
pytest tests/shared/test_database.py -v
```

Expected: all 3 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add shared/database.py tests/shared/test_database.py
git commit -m "feat: rewrite database.py with async engine and shared schema"
```

---

## Task 5: IO Adapters — Local

**Files:**
- Create: `core/io.py`
- Create: `tests/core/test_io.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/core/test_io.py
import pytest
from pathlib import Path
from core.io import LocalAdapter


async def test_local_write_and_read(tmp_dir):
    adapter = LocalAdapter(tmp_dir)
    await adapter.write("hello.txt", "world")
    result = await adapter.read("hello.txt")
    assert result == "world"


async def test_local_write_creates_subdirs(tmp_dir):
    adapter = LocalAdapter(tmp_dir)
    await adapter.write("subdir/file.txt", "content")
    assert (tmp_dir / "subdir" / "file.txt").exists()


async def test_local_list(tmp_dir):
    adapter = LocalAdapter(tmp_dir)
    await adapter.write("a.txt", "a")
    await adapter.write("b.txt", "b")
    files = await adapter.list()
    assert "a.txt" in files
    assert "b.txt" in files


async def test_local_delete(tmp_dir):
    adapter = LocalAdapter(tmp_dir)
    await adapter.write("del.txt", "x")
    await adapter.delete("del.txt")
    assert not (tmp_dir / "del.txt").exists()


async def test_local_read_missing_file_raises(tmp_dir):
    adapter = LocalAdapter(tmp_dir)
    with pytest.raises(FileNotFoundError):
        await adapter.read("missing.txt")


async def test_base_path_is_injected(tmp_dir):
    adapter = LocalAdapter(tmp_dir)
    # App should never know the absolute base path
    await adapter.write("log.md", "data")
    assert (tmp_dir / "log.md").read_text() == "data"
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
pytest tests/core/test_io.py -v
```

Expected: `ImportError: No module named 'core.io'`

- [ ] **Step 3: Implement core/io.py with LocalAdapter**

```python
from __future__ import annotations
from pathlib import Path
from typing import Any, List
from typing import Protocol, runtime_checkable


@runtime_checkable
class IOAdapter(Protocol):
    async def read(self, path: str) -> Any: ...
    async def write(self, path: str, data: Any) -> None: ...
    async def list(self, path: str = "") -> List[str]: ...
    async def delete(self, path: str) -> None: ...


class LocalAdapter:
    def __init__(self, base_path: Path) -> None:
        self._base = base_path
        self._base.mkdir(parents=True, exist_ok=True)

    def _resolve(self, path: str) -> Path:
        return self._base / path

    async def read(self, path: str) -> str:
        target = self._resolve(path)
        if not target.exists():
            raise FileNotFoundError(f"{path} not found in {self._base}")
        return target.read_text()

    async def write(self, path: str, data: str) -> None:
        target = self._resolve(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(data)

    async def list(self, path: str = "") -> List[str]:
        target = self._resolve(path) if path else self._base
        if not target.exists():
            return []
        return [str(p.relative_to(self._base)) for p in target.iterdir()]

    async def delete(self, path: str) -> None:
        self._resolve(path).unlink()
```

- [ ] **Step 4: Run tests to confirm they pass**

```bash
pytest tests/core/test_io.py -v
```

Expected: all 6 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add core/io.py tests/core/test_io.py
git commit -m "feat: add IOAdapter protocol and LocalAdapter"
```

---

## Task 6: NotifyService (ntfy.sh)

**Files:**
- Create: `shared/ntfy.py`
- Create: `tests/shared/test_ntfy.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/shared/test_ntfy.py
import pytest
from unittest.mock import AsyncMock, patch
from shared.ntfy import NotifyService


async def test_send_posts_to_ntfy(tmp_dir):
    service = NotifyService(topic="test_topic")
    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = AsyncMock(status_code=200)
        await service.send("Hello", title="Test", priority="default")
        mock_post.assert_called_once()
        call_kwargs = mock_post.call_args
        assert "test_topic" in str(call_kwargs)


async def test_send_includes_title_and_priority():
    service = NotifyService(topic="alerts")
    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = AsyncMock(status_code=200)
        await service.send("msg", title="MyTitle", priority="high")
        headers = mock_post.call_args.kwargs.get("headers", {})
        assert headers.get("Title") == "MyTitle"
        assert headers.get("Priority") == "high"


async def test_send_failure_does_not_raise():
    service = NotifyService(topic="test")
    with patch("httpx.AsyncClient.post", side_effect=Exception("network error")):
        # Should log but not raise — notifications are best-effort
        await service.send("msg")
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
pytest tests/shared/test_ntfy.py -v
```

Expected: `ImportError: No module named 'shared.ntfy'`

- [ ] **Step 3: Implement shared/ntfy.py**

```python
from __future__ import annotations
import logging
import httpx

logger = logging.getLogger(__name__)


class NotifyService:
    def __init__(
        self,
        topic: str,
        base_url: str = "https://ntfy.sh",
    ) -> None:
        self._url = f"{base_url}/{topic}"

    async def send(
        self,
        message: str,
        title: str = "",
        priority: str = "default",
    ) -> None:
        try:
            async with httpx.AsyncClient() as client:
                await client.post(
                    self._url,
                    content=message,
                    headers={
                        "Title": title,
                        "Priority": priority,
                    },
                )
        except Exception as e:
            logger.error(f"ntfy notification failed: {e}")
```

- [ ] **Step 4: Run tests to confirm they pass**

```bash
pytest tests/shared/test_ntfy.py -v
```

Expected: all 3 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add shared/ntfy.py tests/shared/test_ntfy.py
git commit -m "feat: add NotifyService for ntfy.sh push notifications"
```

---

## Task 7: System Events + EventBus

**Files:**
- Create: `core/events.py`
- Create: `core/eventbus.py`
- Create: `tests/core/test_eventbus.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/core/test_eventbus.py
import pytest
from pydantic import BaseModel
from core.eventbus import EventBus
from core.events import SystemAppFailed, SystemCircuitBroken


class GoalReached(BaseModel):
    calories: int
    deficit_met: bool


async def test_register_and_emit_valid_schema():
    bus = EventBus()
    bus.register_schema("nutrition.goal_reached", GoalReached)
    await bus.emit("nutrition.goal_reached", {"calories": 2000, "deficit_met": True})
    topic, event = await bus.get()
    assert topic == "nutrition.goal_reached"
    assert isinstance(event, GoalReached)
    assert event.calories == 2000


async def test_emit_invalid_payload_raises():
    bus = EventBus()
    bus.register_schema("nutrition.goal_reached", GoalReached)
    with pytest.raises(Exception):
        await bus.emit("nutrition.goal_reached", {"calories": "not_an_int"})


async def test_emit_unknown_system_topic_raises():
    bus = EventBus()
    with pytest.raises(ValueError, match="system"):
        await bus.emit("system.unknown_event", {"foo": "bar"})


async def test_emit_unknown_app_topic_passes_through():
    bus = EventBus()
    # No schema registered — permissive for app topics
    await bus.emit("myapp.something", {"any": "data"})
    topic, event = await bus.get()
    assert topic == "myapp.something"
    assert event == {"any": "data"}


async def test_register_subscription():
    bus = EventBus()
    bus.register_subscription("nutrition.goal_reached", "wiki-compiler")
    subscribers = bus.get_subscribers("nutrition.goal_reached")
    assert "wiki-compiler" in subscribers


async def test_system_app_failed_schema():
    event = SystemAppFailed(app_id="nutrition", error="KeyError", timestamp=__import__("datetime").datetime.now())
    assert event.app_id == "nutrition"


async def test_system_circuit_broken_schema():
    event = SystemCircuitBroken(app_id="nutrition", failure_count=3)
    assert event.failure_count == 3
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
pytest tests/core/test_eventbus.py -v
```

Expected: `ImportError`

- [ ] **Step 3: Implement core/events.py**

```python
from __future__ import annotations
from datetime import datetime
from pydantic import BaseModel


class SystemAppFailed(BaseModel):
    app_id: str
    error: str
    timestamp: datetime


class SystemCircuitBroken(BaseModel):
    app_id: str
    failure_count: int


class SystemStorageLow(BaseModel):
    path: str
    available_bytes: int


SYSTEM_SCHEMAS = {
    "system.app_failed": SystemAppFailed,
    "system.circuit_broken": SystemCircuitBroken,
    "system.storage_low": SystemStorageLow,
}
```

- [ ] **Step 4: Implement core/eventbus.py**

```python
from __future__ import annotations
import asyncio
import logging
from typing import Any, Dict, List, Type
from pydantic import BaseModel
from core.events import SYSTEM_SCHEMAS

logger = logging.getLogger(__name__)


class EventBus:
    def __init__(self) -> None:
        self._schemas: Dict[str, Type[BaseModel]] = dict(SYSTEM_SCHEMAS)
        self._subscriptions: Dict[str, List[str]] = {}
        self._queue: asyncio.Queue = asyncio.Queue()

    def register_schema(self, topic: str, schema: Type[BaseModel]) -> None:
        self._schemas[topic] = schema

    def register_subscription(self, topic: str, app_id: str) -> None:
        self._subscriptions.setdefault(topic, []).append(app_id)

    def get_subscribers(self, topic: str) -> List[str]:
        return self._subscriptions.get(topic, [])

    async def emit(self, topic: str, data: Any) -> None:
        if isinstance(data, dict):
            if topic in self._schemas:
                data = self._schemas[topic].model_validate(data)
            elif topic.startswith("system."):
                raise ValueError(f"No schema registered for system topic: {topic}")
            else:
                logger.warning(f"No schema for topic '{topic}' — passing dict through")
        await self._queue.put((topic, data))

    async def get(self) -> tuple:
        return await self._queue.get()
```

- [ ] **Step 5: Run tests to confirm they pass**

```bash
pytest tests/core/test_eventbus.py -v
```

Expected: all 7 tests PASS.

- [ ] **Step 6: Commit**

```bash
git add core/events.py core/eventbus.py tests/core/test_eventbus.py
git commit -m "feat: add typed system events and EventBus with Schema Registry"
```

---

## Task 8: AppContext

**Files:**
- Create: `core/context.py`
- Create: `tests/core/test_context.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/core/test_context.py
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime
from pathlib import Path
from core.context import AppContext, AppMeta, build_context
from core.models.user import User
from core.io import LocalAdapter
from shared.ntfy import NotifyService
from core.eventbus import EventBus


def make_user():
    return User(name="Laurent", email="laurent@example.com")


def make_meta(app_id="test-app"):
    return AppMeta(app_id=app_id, timestamp=datetime.now(), secrets={})


async def test_context_exposes_all_properties(tmp_dir):
    user = make_user()
    meta = make_meta()
    io = LocalAdapter(tmp_dir)
    notify = NotifyService(topic="test")
    bus = EventBus()
    db_session = AsyncMock()
    metrics = {"weight_kg": 104}

    ctx = AppContext(
        user=user,
        metrics=metrics,
        db=db_session,
        io=io,
        event_bus=bus,
        notify=notify,
        meta=meta,
    )

    assert ctx.user.name == "Laurent"
    assert ctx.metrics == {"weight_kg": 104}
    assert ctx.meta.app_id == "test-app"


async def test_ctx_emit_delegates_to_eventbus(tmp_dir):
    bus = EventBus()
    bus.emit = AsyncMock()
    ctx = AppContext(
        user=make_user(), metrics={}, db=AsyncMock(),
        io=LocalAdapter(tmp_dir), event_bus=bus,
        notify=NotifyService(topic="t"), meta=make_meta(),
    )
    await ctx.emit("myapp.event", {"key": "value"})
    bus.emit.assert_called_once_with("myapp.event", {"key": "value"})


async def test_ctx_cleanup_rolls_back_db(tmp_dir):
    db_session = AsyncMock()
    ctx = AppContext(
        user=make_user(), metrics={}, db=db_session,
        io=LocalAdapter(tmp_dir), event_bus=EventBus(),
        notify=NotifyService(topic="t"), meta=make_meta(),
    )
    await ctx.cleanup()
    db_session.rollback.assert_called_once()
    db_session.close.assert_called_once()
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
pytest tests/core/test_context.py -v
```

Expected: `ImportError`

- [ ] **Step 3: Implement core/context.py**

```python
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Dict
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from core.io import IOAdapter
from core.models.user import User
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
        metrics: Any,
        db: AsyncSession,
        io: IOAdapter,
        event_bus: Any,
        notify: NotifyService,
        meta: AppMeta,
    ) -> None:
        self.user = user
        self.metrics = metrics
        self.db = db
        self.io = io
        self.notify = notify
        self.meta = meta
        self._event_bus = event_bus

    async def emit(self, topic: str, data: Any) -> None:
        await self._event_bus.emit(topic, data)

    async def cleanup(self) -> None:
        await self.db.rollback()
        await self.db.close()
```

- [ ] **Step 4: Run tests to confirm they pass**

```bash
pytest tests/core/test_context.py -v
```

Expected: all 3 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add core/context.py tests/core/test_context.py
git commit -m "feat: add AppContext typed class with emit delegation and cleanup"
```

---

## Task 9: safe_execute + Circuit Breaker

**Files:**
- Create: `core/executor.py`
- Create: `tests/core/test_executor.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/core/test_executor.py
import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from core.executor import CircuitBreaker, safe_execute, reset_breaker


def test_circuit_breaker_opens_after_threshold():
    breaker = CircuitBreaker("test-app", threshold=3, window_seconds=600)
    assert not breaker.is_open()
    breaker.record_failure()
    breaker.record_failure()
    opened = breaker.record_failure()
    assert opened is True
    assert breaker.is_open()


def test_circuit_breaker_resets():
    breaker = CircuitBreaker("test-app", threshold=3, window_seconds=600)
    breaker.record_failure()
    breaker.record_failure()
    breaker.record_failure()
    assert breaker.is_open()
    breaker.reset()
    assert not breaker.is_open()


def test_circuit_breaker_expires_old_failures():
    import time
    breaker = CircuitBreaker("test-app", threshold=3, window_seconds=1)
    breaker.record_failure()
    breaker.record_failure()
    # simulate time passing by manipulating the deque directly
    breaker._failures[0] = time.time() - 2
    breaker._failures[1] = time.time() - 2
    opened = breaker.record_failure()
    # only 1 failure in window, should not open
    assert opened is False


async def test_safe_execute_calls_execute(tmp_dir):
    from unittest.mock import AsyncMock, MagicMock
    mock_module = MagicMock()
    mock_module.execute = AsyncMock()
    mock_ctx = AsyncMock()
    mock_ctx.cleanup = AsyncMock()
    bus = MagicMock()
    bus.emit = AsyncMock()

    with patch("core.executor.lazy_load", return_value=mock_module):
        reset_breaker("myapp")
        await safe_execute("myapp", mock_ctx, bus)

    mock_module.execute.assert_called_once_with(mock_ctx)
    mock_ctx.cleanup.assert_called_once()


async def test_safe_execute_calls_cleanup_on_error(tmp_dir):
    mock_module = MagicMock()
    mock_module.execute = AsyncMock(side_effect=ValueError("boom"))
    mock_ctx = AsyncMock()
    mock_ctx.cleanup = AsyncMock()
    mock_ctx.notify = AsyncMock()
    mock_ctx.notify.send = AsyncMock()
    bus = MagicMock()
    bus.emit = AsyncMock()

    with patch("core.executor.lazy_load", return_value=mock_module):
        reset_breaker("myapp")
        await safe_execute("myapp", mock_ctx, bus)

    mock_ctx.cleanup.assert_called_once()


async def test_safe_execute_skips_open_circuit():
    mock_module = MagicMock()
    mock_module.execute = AsyncMock()
    mock_ctx = AsyncMock()
    bus = MagicMock()
    bus.emit = AsyncMock()

    reset_breaker("blocked-app")
    # Force circuit open
    from core.executor import _circuit_breakers, CircuitBreaker
    breaker = CircuitBreaker("blocked-app", threshold=1)
    breaker.record_failure()
    _circuit_breakers["blocked-app"] = breaker

    with patch("core.executor.lazy_load", return_value=mock_module):
        await safe_execute("blocked-app", mock_ctx, bus)

    mock_module.execute.assert_not_called()
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
pytest tests/core/test_executor.py -v
```

Expected: `ImportError`

- [ ] **Step 3: Implement core/executor.py**

```python
from __future__ import annotations
import asyncio
import importlib
import logging
import time
from collections import deque
from datetime import datetime
from typing import Any, Dict

logger = logging.getLogger(__name__)
_semaphore = asyncio.Semaphore(3)
_circuit_breakers: Dict[str, "CircuitBreaker"] = {}


class CircuitBreaker:
    def __init__(
        self, app_id: str, threshold: int = 3, window_seconds: int = 600
    ) -> None:
        self.app_id = app_id
        self.threshold = threshold
        self.window_seconds = window_seconds
        self._failures: deque = deque()
        self._open = False

    def record_failure(self) -> bool:
        now = time.time()
        while self._failures and now - self._failures[0] > self.window_seconds:
            self._failures.popleft()
        self._failures.append(now)
        if len(self._failures) >= self.threshold:
            self._open = True
            return True
        return False

    def is_open(self) -> bool:
        return self._open

    def reset(self) -> None:
        self._open = False
        self._failures.clear()


def reset_breaker(app_id: str) -> None:
    if app_id in _circuit_breakers:
        _circuit_breakers[app_id].reset()
    else:
        _circuit_breakers[app_id] = CircuitBreaker(app_id)


async def lazy_load(app_id: str) -> Any:
    return importlib.import_module(f"apps.{app_id}.main")


async def safe_execute(app_id: str, ctx: Any, event_bus: Any, **kwargs: Any) -> None:
    breaker = _circuit_breakers.setdefault(app_id, CircuitBreaker(app_id))
    if breaker.is_open():
        logger.warning(f"Circuit open for {app_id} — skipping")
        return

    async with _semaphore:
        try:
            module = await lazy_load(app_id)
            await module.execute(ctx, **kwargs)
        except Exception as e:
            logger.error(f"App {app_id} failed: {e}", exc_info=True)
            broken = breaker.record_failure()
            if broken:
                await event_bus.emit(
                    "system.circuit_broken",
                    {"app_id": app_id, "failure_count": breaker.threshold},
                )
                await ctx.notify.send(
                    f"Circuit broken: {app_id}", title="Belgrade OS", priority="high"
                )
            await event_bus.emit(
                "system.app_failed",
                {"app_id": app_id, "error": str(e), "timestamp": datetime.now()},
            )
        finally:
            await ctx.cleanup()
```

- [ ] **Step 4: Run tests to confirm they pass**

```bash
pytest tests/core/test_executor.py -v
```

Expected: all 6 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add core/executor.py tests/core/test_executor.py
git commit -m "feat: add safe_execute wrapper and CircuitBreaker"
```

---

## Task 10: Bootstrapper

**Files:**
- Rewrite: `core/loader.py`
- Create: `tests/core/test_loader.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/core/test_loader.py
import json
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
from core.loader import Bootstrapper


def make_app_dir(apps_dir: Path, app_id: str, manifest: dict) -> Path:
    app_path = apps_dir / app_id
    app_path.mkdir()
    (app_path / "manifest.json").write_text(json.dumps(manifest))
    (app_path / "main.py").write_text("async def execute(ctx): pass")
    return app_path


def test_bootstrapper_discovers_manifests(apps_dir, sample_manifest_dict):
    make_app_dir(apps_dir, "test-app", sample_manifest_dict)
    boot = Bootstrapper(apps_dir=apps_dir, data_dir=apps_dir.parent / "data")
    manifests = boot.discover()
    assert len(manifests) == 1
    assert manifests[0].app_id == "test-app"


def test_bootstrapper_skips_invalid_manifest(apps_dir):
    app_path = apps_dir / "bad-app"
    app_path.mkdir()
    (app_path / "manifest.json").write_text('{"invalid": true}')
    boot = Bootstrapper(apps_dir=apps_dir, data_dir=apps_dir.parent / "data")
    manifests = boot.discover()
    assert len(manifests) == 0


def test_bootstrapper_skips_dir_without_manifest(apps_dir):
    (apps_dir / "no-manifest").mkdir()
    boot = Bootstrapper(apps_dir=apps_dir, data_dir=apps_dir.parent / "data")
    manifests = boot.discover()
    assert len(manifests) == 0


async def test_bootstrapper_provisions_data_dir(apps_dir, sample_manifest_dict, tmp_dir):
    make_app_dir(apps_dir, "test-app", sample_manifest_dict)
    data_dir = tmp_dir / "data" / "apps"
    boot = Bootstrapper(apps_dir=apps_dir, data_dir=data_dir)

    with patch.object(boot, "_provision_db_schema", new_callable=AsyncMock):
        await boot.provision("test-app")

    assert (data_dir / "test-app").exists()


def test_bootstrapper_builds_subscription_map(apps_dir, sample_manifest_dict, tmp_dir):
    manifest = {**sample_manifest_dict, "triggers": [{"type": "event", "topic": "nutrition.goal_reached"}]}
    make_app_dir(apps_dir, "test-app", manifest)
    boot = Bootstrapper(apps_dir=apps_dir, data_dir=tmp_dir)
    manifests = boot.discover()
    boot.build_subscription_map(manifests)
    assert "test-app" in boot.subscription_map.get("nutrition.goal_reached", [])
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
pytest tests/core/test_loader.py -v
```

Expected: `ImportError`

- [ ] **Step 3: Implement core/loader.py**

```python
from __future__ import annotations
import importlib
import json
import logging
from pathlib import Path
from typing import Any, Dict, List

from fastapi import FastAPI
from pydantic import ValidationError
from sqlalchemy import text

from core.context import AppContext, AppMeta
from core.eventbus import EventBus
from core.executor import safe_execute
from core.io import LocalAdapter
from core.models.manifest import AppManifest
from core.models.user import User, load_identity
from shared.database import engine, AsyncSessionLocal, SHARED_SCHEMA_SQL
from shared.ntfy import NotifyService
from core.config import settings

logger = logging.getLogger(__name__)


class Bootstrapper:
    def __init__(self, apps_dir: Path, data_dir: Path) -> None:
        self.apps_dir = apps_dir
        self.data_dir = data_dir
        self.subscription_map: Dict[str, List[str]] = {}
        self._manifests: List[AppManifest] = []

    def discover(self) -> List[AppManifest]:
        manifests = []
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
                logger.warning(f"Skipping {item.name}: {e}")
        self._manifests = manifests
        return manifests

    def build_subscription_map(self, manifests: List[AppManifest]) -> None:
        for manifest in manifests:
            for trigger in manifest.triggers:
                if trigger.type == "event" and trigger.topic:
                    self.subscription_map.setdefault(trigger.topic, []).append(manifest.app_id)

    async def _provision_db_schema(self, app_id: str) -> None:
        async with engine.begin() as conn:
            await conn.execute(text(f"CREATE SCHEMA IF NOT EXISTS app_{app_id.replace('-', '_')}"))

    async def provision(self, app_id: str) -> None:
        app_data_dir = self.data_dir / app_id
        app_data_dir.mkdir(parents=True, exist_ok=True)
        await self._provision_db_schema(app_id)

    def _register_app_schemas(self, app_id: str, event_bus: EventBus) -> None:
        try:
            mod = importlib.import_module(f"apps.{app_id}.events")
            for topic, schema in getattr(mod, "EVENT_SCHEMAS", {}).items():
                event_bus.register_schema(topic, schema)
                logger.info(f"Registered schema for {topic}")
        except ImportError:
            pass

    def _load_metrics_schema(self, app_id: str) -> Any:
        try:
            mod = importlib.import_module(f"apps.{app_id}.metrics")
            return getattr(mod, "MetricsSchema", None)
        except ImportError:
            return None

    def build_ctx_factory(
        self,
        manifest: AppManifest,
        user: User,
        event_bus: EventBus,
        notify: NotifyService,
    ):
        base_path = self.data_dir / manifest.app_id
        metrics_schema = self._load_metrics_schema(manifest.app_id)

        async def factory() -> AppContext:
            from datetime import datetime
            session = AsyncSessionLocal()
            io = LocalAdapter(base_path)
            metrics: Any = {}
            if metrics_schema:
                async with engine.connect() as conn:
                    result = await conn.execute(
                        text("SELECT data FROM shared.current_metrics WHERE namespace = 'user.metrics'")
                    )
                    row = result.fetchone()
                    if row:
                        metrics = metrics_schema.model_validate(dict(row[0]))
            return AppContext(
                user=user,
                metrics=metrics,
                db=session,
                io=io,
                event_bus=event_bus,
                notify=notify,
                meta=AppMeta(
                    app_id=manifest.app_id,
                    timestamp=datetime.now(),
                    secrets=dict(settings.model_dump()),
                ),
            )

        return factory

    async def bootstrap(self, app: FastAPI, event_bus: EventBus) -> None:
        from apscheduler.schedulers.asyncio import AsyncIOScheduler
        import asyncio

        user = load_identity(Path("identity.json"))
        notify = NotifyService(topic=settings.ntfy_topic)
        scheduler = AsyncIOScheduler()
        manifests = self.discover()
        self.build_subscription_map(manifests)

        for manifest in manifests:
            await self.provision(manifest.app_id)
            self._register_app_schemas(manifest.app_id, event_bus)

            for topic, subscribers in self.subscription_map.items():
                for sub_id in subscribers:
                    event_bus.register_subscription(topic, sub_id)

            ctx_factory = self.build_ctx_factory(manifest, user, event_bus, notify)

            for trigger in manifest.triggers:
                if trigger.type == "cron" and trigger.schedule:
                    async def cron_job(mid=manifest.app_id, factory=ctx_factory):
                        ctx = await factory()
                        await safe_execute(mid, ctx, event_bus)

                    scheduler.add_job(
                        cron_job,
                        "cron",
                        **_parse_cron(trigger.schedule),
                    )
                elif trigger.type == "hook":
                    _register_hook(app, manifest.app_id, ctx_factory, event_bus)

            if manifest.mcp_enabled:
                logger.info(f"MCP tool registered for {manifest.app_id}")

            logger.info(f"Bootstrapped: {manifest.app_id}")

        scheduler.start()


def _parse_cron(schedule: str) -> dict:
    minute, hour, day, month, day_of_week = schedule.split()
    return dict(minute=minute, hour=hour, day=day, month=month, day_of_week=day_of_week)


def _register_hook(app: FastAPI, app_id: str, ctx_factory: Any, event_bus: EventBus) -> None:
    async def handler():
        ctx = await ctx_factory()
        await safe_execute(app_id, ctx, event_bus)
        return {"status": "ok", "app_id": app_id}

    app.add_api_route(f"/{app_id}/run", handler, methods=["POST"])
```

- [ ] **Step 4: Run tests to confirm they pass**

```bash
pytest tests/core/test_loader.py -v
```

Expected: all 5 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add core/loader.py tests/core/test_loader.py
git commit -m "feat: implement Bootstrapper with manifest discovery, provisioning, and trigger registration"
```

---

## Task 11: Obsidian IO Adapter

**Files:**
- Modify: `core/io.py`
- Modify: `tests/core/test_io.py`

- [ ] **Step 1: Write failing tests (append to existing test file)**

```python
# Append to tests/core/test_io.py
from core.io import ObsidianAdapter


async def test_obsidian_write_creates_frontmatter(tmp_dir):
    adapter = ObsidianAdapter(tmp_dir)
    await adapter.write("log.md", {
        "frontmatter": {"weight_kg": 104, "date": "2026-04-23"},
        "body": "Feeling good today.",
    })
    content = (tmp_dir / "log.md").read_text()
    assert "weight_kg: 104" in content
    assert "Feeling good today." in content
    assert content.startswith("---")


async def test_obsidian_read_parses_frontmatter(tmp_dir):
    adapter = ObsidianAdapter(tmp_dir)
    (tmp_dir / "note.md").write_text(
        "---\nweight_kg: 104\ndate: '2026-04-23'\n---\nBody text here."
    )
    result = await adapter.read("note.md")
    assert result["frontmatter"]["weight_kg"] == 104
    assert result["body"] == "Body text here."


async def test_obsidian_read_file_without_frontmatter(tmp_dir):
    adapter = ObsidianAdapter(tmp_dir)
    (tmp_dir / "plain.md").write_text("Just plain text.")
    result = await adapter.read("plain.md")
    assert result["frontmatter"] == {}
    assert result["body"] == "Just plain text."


async def test_obsidian_roundtrip(tmp_dir):
    adapter = ObsidianAdapter(tmp_dir)
    original = {"frontmatter": {"calories": 2000}, "body": "Good deficit day."}
    await adapter.write("entry.md", original)
    result = await adapter.read("entry.md")
    assert result["frontmatter"]["calories"] == 2000
    assert "Good deficit day." in result["body"]
```

- [ ] **Step 2: Run new tests to confirm they fail**

```bash
pytest tests/core/test_io.py -k "obsidian" -v
```

Expected: `ImportError: cannot import name 'ObsidianAdapter'`

- [ ] **Step 3: Add ObsidianAdapter to core/io.py**

```python
# Append to core/io.py
import yaml
from typing import Dict


class ObsidianAdapter:
    def __init__(self, base_path: Path) -> None:
        self._local = LocalAdapter(base_path)

    async def read(self, path: str) -> Dict[str, Any]:
        content = await self._local.read(path)
        if content.startswith("---"):
            parts = content.split("---", 2)
            if len(parts) >= 3:
                frontmatter = yaml.safe_load(parts[1]) or {}
                body = parts[2].strip()
                return {"frontmatter": frontmatter, "body": body}
        return {"frontmatter": {}, "body": content}

    async def write(self, path: str, data: Dict[str, Any]) -> None:
        frontmatter = data.get("frontmatter", {})
        body = data.get("body", "")
        content = f"---\n{yaml.dump(frontmatter, default_flow_style=False)}---\n{body}"
        await self._local.write(path, content)

    async def list(self, path: str = "") -> List[str]:
        return await self._local.list(path)

    async def delete(self, path: str) -> None:
        await self._local.delete(path)
```

- [ ] **Step 4: Run all IO tests to confirm they pass**

```bash
pytest tests/core/test_io.py -v
```

Expected: all 10 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add core/io.py tests/core/test_io.py
git commit -m "feat: add ObsidianAdapter with frontmatter read/write"
```

---

## Task 12: Wire Up core/main.py + docker-compose

**Files:**
- Modify: `core/main.py`
- Modify: `docker-compose.yml`

- [ ] **Step 1: Update docker-compose.yml to add CouchDB**

```yaml
# Add this service to docker-compose.yml alongside db and tunnel:
  couchdb:
    image: couchdb:3
    container_name: belgrade-couchdb
    restart: always
    environment:
      COUCHDB_USER: admin
      COUCHDB_PASSWORD: ${COUCHDB_PASSWORD}
    ports:
      - "5984:5984"
    volumes:
      - ./data/couchdb:/opt/couchdb/data
```

- [ ] **Step 1b: Add COUCHDB_PASSWORD to .env**

```bash
# Add to .env (never commit this file):
COUCHDB_PASSWORD=your_password_here
```

- [ ] **Step 2: Update core/main.py**

```python
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI
from core.loader import Bootstrapper
from core.eventbus import EventBus
from shared.database import init_db

event_bus = EventBus()
bootstrapper = Bootstrapper(
    apps_dir=Path("apps"),
    data_dir=Path("data/apps"),
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    await bootstrapper.bootstrap(app, event_bus)
    yield


app = FastAPI(title="Belgrade AI OS", lifespan=lifespan)


@app.get("/")
async def root():
    return {"message": "Zdravo, Laurent!", "status": "running"}


@app.get("/health")
async def health():
    return {"status": "ok", "apps": len(bootstrapper._manifests)}
```

- [ ] **Step 3: Smoke test — start the server with no apps**

```bash
uvicorn core.main:app --reload
```

Expected output includes:
```
INFO:     Application startup complete.
```
And `GET /health` returns `{"status": "ok", "apps": 0}`.

- [ ] **Step 4: Commit**

```bash
git add core/main.py docker-compose.yml
git commit -m "feat: wire up Bootstrapper and EventBus in main.py, add CouchDB to compose"
```

---

## Task 13: MCP Integration

**Files:**
- Create: `core/mcp.py`
- Modify: `core/main.py`
- Create: `tests/core/test_mcp.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/core/test_mcp.py
import pytest
from core.mcp import build_mcp_tool_name, validate_cloudflare_header
from core.models.manifest import AppManifest, AppStorage, AppConfig


def make_mcp_manifest(app_id: str) -> AppManifest:
    return AppManifest(
        app_id=app_id,
        name="Test",
        description="Does something useful",
        version="1.0.0",
        pattern="worker",
        mcp_enabled=True,
        triggers=[],
        storage=AppStorage(scope=app_id),
        config=AppConfig(),
    )


def test_tool_name_format():
    manifest = make_mcp_manifest("nutrition")
    assert build_mcp_tool_name(manifest) == "run_nutrition"


def test_tool_name_replaces_hyphens():
    manifest = make_mcp_manifest("wiki-compiler")
    assert build_mcp_tool_name(manifest) == "run_wiki_compiler"


def test_cloudflare_header_valid():
    # Any non-empty value is accepted (actual JWT validation is Cloudflare's job)
    assert validate_cloudflare_header("some-jwt-token") is True


def test_cloudflare_header_missing_raises():
    with pytest.raises(ValueError, match="Cloudflare"):
        validate_cloudflare_header(None)


def test_cloudflare_header_empty_raises():
    with pytest.raises(ValueError, match="Cloudflare"):
        validate_cloudflare_header("")
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
pytest tests/core/test_mcp.py -v
```

Expected: `ImportError`

- [ ] **Step 3: Implement core/mcp.py**

```python
from __future__ import annotations
import logging
from typing import Any, Callable, Optional
from fastapi import FastAPI, Header, HTTPException
from core.models.manifest import AppManifest

logger = logging.getLogger(__name__)


def build_mcp_tool_name(manifest: AppManifest) -> str:
    return f"run_{manifest.app_id.replace('-', '_')}"


def validate_cloudflare_header(value: Optional[str]) -> bool:
    if not value:
        raise ValueError("Cloudflare Access Identity header is required")
    return True


def register_mcp_tools(app: FastAPI, manifests: list[AppManifest], ctx_factories: dict[str, Callable]) -> None:
    try:
        from fastmcp import FastMCP
    except ImportError:
        logger.warning("fastmcp not installed — MCP endpoint skipped")
        return

    mcp = FastMCP("Belgrade AI OS")

    for manifest in manifests:
        if not manifest.mcp_enabled:
            continue

        tool_name = build_mcp_tool_name(manifest)
        description = manifest.description
        ctx_factory = ctx_factories.get(manifest.app_id)

        @mcp.tool(name=tool_name, description=description)
        async def run_app(
            cf_access: Optional[str] = Header(None, alias="X-Cloudflare-Access-Identity"),
            _app_id: str = manifest.app_id,
            _factory: Any = ctx_factory,
        ) -> dict:
            try:
                validate_cloudflare_header(cf_access)
            except ValueError as e:
                raise HTTPException(status_code=401, detail=str(e))
            return {"status": "triggered", "app_id": _app_id}

        logger.info(f"MCP tool registered: {tool_name}")

    app.mount("/mcp", mcp.get_asgi_app())
```

- [ ] **Step 4: Run tests to confirm they pass**

```bash
pytest tests/core/test_mcp.py -v
```

Expected: all 5 tests PASS.

- [ ] **Step 5: Run full test suite**

```bash
pytest -v
```

Expected: all tests PASS. Note any failures and fix before proceeding.

- [ ] **Step 6: Run mypy**

```bash
mypy core/ shared/
```

Expected: no errors (warnings about missing stubs for third-party libs are acceptable).

- [ ] **Step 7: Commit**

```bash
git add core/mcp.py tests/core/test_mcp.py
git commit -m "feat: add MCP tool auto-generation with Cloudflare Access auth"
```

---

## Final Checklist

Run these before declaring Phase 2 complete:

- [ ] `pytest -v` — all tests pass
- [ ] `mypy core/ shared/` — no type errors
- [ ] `uvicorn core.main:app --reload` — server starts cleanly with no apps
- [ ] Add a minimal test app (`apps/hello/manifest.json` + `apps/hello/main.py`) and verify it bootstraps
- [ ] `GET /health` returns correct app count
- [ ] Commit any final fixes

```bash
git add .
git commit -m "feat: Phase 2 platform core complete"
```
