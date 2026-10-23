# App Isolation (H1–H3) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A compromised app can no longer become root (H1), read the platform's `.env` (H2), or touch other apps' data (H3).

**Architecture:** Apps stop being child processes of the controller (user `ivan`, in the `docker` group). Each app runs as a systemd unit `belgrade-os-app@<id>.service` with `DynamicUser=yes` and a sandbox: it sees only its own directory, the SDK and the venv; its network is localhost-only. The controller starts/stops units via `systemctl`, permitted by one narrow polkit rule, and hands each app its environment through a `0600` env file under `/run/belgrade-os/apps/`. Each app gets its own non-superuser Postgres role (`app_<id>`) with a password derived from `APP_DB_SECRET`, and the SDK refuses to use a tenant schema it doesn't own. A `subprocess` runner keeps today's behaviour for local dev (Mac, tests).

**Tech Stack:** Python 3.12 (FastAPI controller, SQLAlchemy async + asyncpg), systemd 255, polkit 124 (JS rules), Postgres 15, pytest.

**Audit findings addressed:** H1 (app → docker group → root), H2 (app reads `.env` as same uid), H3 (apps use a Postgres superuser). Out of scope: M1, M2, per-person tokens.

---

## File structure

| File | Responsibility |
|---|---|
| `services/platform_controller/app_db.py` (new) | Per-app Postgres role: name, derived password, DB URL, idempotent provisioning |
| `services/platform_controller/app_runner.py` (new) | How an app process runs: `SubprocessRunner` (dev) and `SystemdRunner` (prod) behind one interface |
| `services/platform_controller/main.py` | `AppProcess` builds env (incl. per-app DB URL); `AppSupervisor` delegates lifecycle to a runner |
| `services/platform_controller/config.py` | `CONTROLLER_APP_RUNNER`, `APP_DB_SECRET`, `CONTROLLER_APP_ENV_DIR` |
| `sdk/belgrade_sdk/context.py` | `ctx.db` refuses a tenant schema owned by another role |
| `deploy/belgrade-os-app@.service` (new) | Sandboxed app unit template |
| `deploy/50-belgrade-os.rules` (new) | polkit: controller user may start/stop/restart only `belgrade-os-app@*.service` |
| `deploy/platform_controller-runtime-dir.conf` (new) | controller drop-in creating `/run/belgrade-os/apps` |
| `scripts/install-systemd.sh` | installs the three files above |
| `scripts/setup.sh` | `.env` gains `APP_DB_SECRET`, `CONTROLLER_APP_RUNNER` |

Test DB for integration tests: `BEG_OS_TEST_DB_URL` (default `postgresql+asyncpg://postgres:test@localhost:5433/belgrade_test`, a throwaway `postgres:15-alpine` container). Tests skip when it's unreachable, like the existing SDK isolation tests.

---

### Task 1: Per-app Postgres role (`app_db.py`)

**Files:**
- Create: `services/platform_controller/app_db.py`
- Test: `services/platform_controller/tests/test_app_db.py`

- [ ] **Step 1: Write the failing tests**

```python
# services/platform_controller/tests/test_app_db.py
from __future__ import annotations
import os
import uuid
import pytest
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

from app_db import app_db_url, app_role_name, app_role_password, ensure_app_role

TEST_DB_URL = os.getenv(
    "BEG_OS_TEST_DB_URL", "postgresql+asyncpg://postgres:test@localhost:5433/belgrade_test"
)


def test_role_name_is_prefixed_and_sql_safe():
    assert app_role_name("shopping") == "app_shopping"
    assert app_role_name("my-app") == "app_my_app"
    for bad in ("x; DROP ROLE postgres", "", "a" * 60):
        with pytest.raises(ValueError):
            app_role_name(bad)


def test_password_is_derived_per_app_and_secret():
    p1 = app_role_password("secret", "shopping")
    assert p1 == app_role_password("secret", "shopping")
    assert p1 != app_role_password("secret", "notes")
    assert p1 != app_role_password("other-secret", "shopping")
    assert len(p1) == 64 and all(c in "0123456789abcdef" for c in p1)


def test_password_requires_a_secret():
    with pytest.raises(ValueError):
        app_role_password("", "shopping")


def test_db_url_swaps_credentials_keeps_host_and_db():
    url = make_url(app_db_url("postgresql+asyncpg://laurent:pw@localhost:5434/belgrade_os", "app_shopping", "abc"))
    assert (url.username, url.password, url.host, url.port, url.database) == (
        "app_shopping", "abc", "localhost", 5434, "belgrade_os")


@pytest.fixture
async def admin_engine():
    eng = create_async_engine(TEST_DB_URL)
    try:
        async with eng.connect() as conn:
            await conn.execute(text("SELECT 1"))
    except Exception as exc:
        await eng.dispose()
        pytest.skip(f"Postgres unreachable at {TEST_DB_URL}: {exc}")
    yield eng
    await eng.dispose()


async def _drop_role(admin_engine, role):
    async with admin_engine.begin() as conn:
        await conn.execute(text(f"DROP OWNED BY {role} CASCADE"))
        await conn.execute(text(f"DROP ROLE IF EXISTS {role}"))


async def test_app_role_is_not_superuser_and_owns_only_its_schemas(admin_engine):
    suffix = uuid.uuid4().hex[:8]
    a, b = f"isoa{suffix}", f"isob{suffix}"
    role_a, role_b = app_role_name(a), app_role_name(b)
    db = make_url(TEST_DB_URL).database
    try:
        for app_id, role in ((a, role_a), (b, role_b)):
            async with admin_engine.begin() as conn:
                await ensure_app_role(conn, role, app_role_password("s", app_id), db)
                await ensure_app_role(conn, role, app_role_password("s", app_id), db)  # idempotent

        async with admin_engine.connect() as conn:
            is_super = (await conn.execute(
                text("SELECT rolsuper FROM pg_roles WHERE rolname = :r"), {"r": role_a})).scalar_one()
            await conn.execute(text("CREATE SCHEMA IF NOT EXISTS platform_only"))
            await conn.execute(text("CREATE TABLE IF NOT EXISTS platform_only.secrets (v TEXT)"))
            await conn.commit()
        assert is_super is False

        eng_a = create_async_engine(app_db_url(TEST_DB_URL, role_a, app_role_password("s", a)))
        eng_b = create_async_engine(app_db_url(TEST_DB_URL, role_b, app_role_password("s", b)))
        try:
            async with eng_a.begin() as conn:
                await conn.execute(text(f"CREATE SCHEMA app_t_{a}"))
                await conn.execute(text(f"CREATE TABLE app_t_{a}.items (name TEXT)"))
            async with eng_b.connect() as conn:
                with pytest.raises(Exception, match="permission denied"):
                    await conn.execute(text(f"SELECT * FROM app_t_{a}.items"))
            async with eng_a.connect() as conn:
                with pytest.raises(Exception, match="permission denied"):
                    await conn.execute(text("SELECT * FROM platform_only.secrets"))
        finally:
            await eng_a.dispose()
            await eng_b.dispose()
    finally:
        async with admin_engine.begin() as conn:
            await conn.execute(text("DROP SCHEMA IF EXISTS platform_only CASCADE"))
        for role in (role_a, role_b):
            await _drop_role(admin_engine, role)


async def test_rotating_the_secret_updates_the_password(admin_engine):
    app_id = f"rot{uuid.uuid4().hex[:8]}"
    role = app_role_name(app_id)
    db = make_url(TEST_DB_URL).database
    try:
        async with admin_engine.begin() as conn:
            await ensure_app_role(conn, role, app_role_password("old", app_id), db)
            await ensure_app_role(conn, role, app_role_password("new", app_id), db)
        eng = create_async_engine(app_db_url(TEST_DB_URL, role, app_role_password("new", app_id)))
        try:
            async with eng.connect() as conn:
                assert (await conn.execute(text("SELECT current_user"))).scalar_one() == role
        finally:
            await eng.dispose()
    finally:
        await _drop_role(admin_engine, role)
```

- [ ] **Step 2: Run to verify failure**

Run: `cd services/platform_controller && ../../venv/bin/python3 -m pytest tests/test_app_db.py -q`
Expected: collection error `ModuleNotFoundError: No module named 'app_db'`

- [ ] **Step 3: Implement**

```python
# services/platform_controller/app_db.py
"""Per-app Postgres roles (audit finding H3).

Each app logs in as its own non-superuser role `app_<id>`. The role may create
schemas in the platform database (the SDK creates `app_<tenant>_<app>` on demand)
and owns what it creates, but has no access to other apps' schemas or `shared`.
Passwords are derived from APP_DB_SECRET, so nothing new is stored.
"""
from __future__ import annotations

import hashlib
import hmac
import re

from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncConnection

_APP_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,59}$")   # "app_" + 59 = 63, Postgres' identifier limit
_DB_NAME_RE = re.compile(r"^[A-Za-z0-9_]{1,63}$")
_PASSWORD_RE = re.compile(r"^[0-9a-f]{64}$")


def app_role_name(app_id: str) -> str:
    if not _APP_ID_RE.match(app_id):
        raise ValueError(f"app_id not usable as a DB role: {app_id!r}")
    return "app_" + app_id.replace("-", "_").lower()


def app_role_password(secret: str, app_id: str) -> str:
    if not secret:
        raise ValueError("APP_DB_SECRET is required to derive app DB passwords")
    return hmac.new(secret.encode(), app_id.encode(), hashlib.sha256).hexdigest()


def app_db_url(platform_url: str, role: str, password: str) -> str:
    url = make_url(platform_url).set(username=role, password=password)
    return url.render_as_string(hide_password=False)


async def ensure_app_role(conn: AsyncConnection, role: str, password: str, database: str) -> None:
    """Create or update the app's role. Identifiers/password are validated, then inlined:
    CREATE/ALTER ROLE and GRANT are utility statements and take no bind parameters."""
    if not role.startswith("app_") or not _DB_NAME_RE.match(role):
        raise ValueError(f"invalid role {role!r}")
    if not _PASSWORD_RE.match(password):
        raise ValueError("password must be a derived 64-char hex digest")
    if not _DB_NAME_RE.match(database):
        raise ValueError(f"invalid database name {database!r}")

    exists = (await conn.execute(
        text("SELECT 1 FROM pg_roles WHERE rolname = :r"), {"r": role})).first()
    if not exists:
        await conn.execute(text(f"CREATE ROLE {role}"))
    await conn.execute(text(
        f"ALTER ROLE {role} WITH LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION "
        f"NOBYPASSRLS PASSWORD '{password}'"
    ))
    await conn.execute(text(f"GRANT CONNECT, CREATE ON DATABASE {database} TO {role}"))
```

- [ ] **Step 4: Run tests — all pass**

Run: `cd services/platform_controller && ../../venv/bin/python3 -m pytest tests/test_app_db.py -q`
Expected: `6 passed` (needs the test Postgres on 5433; otherwise 4 passed, 2 skipped)

- [ ] **Step 5: Commit**

```bash
git add services/platform_controller/app_db.py services/platform_controller/tests/test_app_db.py
git commit -m "feat(platform_controller): per-app non-superuser Postgres roles"
```

---

### Task 2: SDK refuses a tenant schema owned by another role

Why: with per-app roles, app A could `CREATE SCHEMA app_family_b` before app B does (squatting) and then read what B writes there.

**Files:**
- Modify: `sdk/belgrade_sdk/context.py` (the `db` property, after `CREATE SCHEMA IF NOT EXISTS`)
- Test: `sdk/tests/test_db_isolation.py`

- [ ] **Step 1: Write the failing test** (append)

```python
async def test_refuses_schema_owned_by_another_role(engine):
    """A schema squatted by another app's role must not be used — its owner could read our rows."""
    tenant = f"t{uuid.uuid4().hex[:8]}"
    squatter = f"squat{uuid.uuid4().hex[:8]}"
    schema = f"app_{tenant}_isotest"
    try:
        async with engine.begin() as conn:
            await conn.execute(text(f"CREATE ROLE {squatter}"))
            await conn.execute(text(f"CREATE SCHEMA {schema} AUTHORIZATION {squatter}"))
        ctx = _ctx(engine, tenant)
        with pytest.raises(PermissionError, match=schema):
            await ctx.db
        await ctx.cleanup()
    finally:
        async with engine.begin() as conn:
            await conn.execute(text(f"DROP SCHEMA IF EXISTS {schema} CASCADE"))
            await conn.execute(text(f"DROP ROLE IF EXISTS {squatter}"))
```

- [ ] **Step 2: Run to verify failure**

Run: `cd sdk && ../venv/bin/python3 -m pytest tests/test_db_isolation.py -q -k squat`
Expected: FAIL — `DID NOT RAISE <class 'PermissionError'>`

- [ ] **Step 3: Implement** — in `AppContext.db`, replace the schema block:

```python
            if schema_name:
                # Postgres accepts a search_path naming a missing schema and silently
                # falls through to public — create it so tables never leak across tenants.
                await self._db_conn.execute(text(f"CREATE SCHEMA IF NOT EXISTS {schema_name}"))
                # With per-app DB roles another app could have created (squatted) this
                # schema first; as its owner it could read everything we write there.
                owned = (await self._db_conn.execute(
                    text("SELECT pg_get_userbyid(nspowner) = current_user "
                         "FROM pg_namespace WHERE nspname = :s"),
                    {"s": schema_name},
                )).scalar_one()
                if not owned:
                    await self._db_conn.rollback()
                    raise PermissionError(f"schema {schema_name} is owned by another role")
                await self._db_conn.execute(text(f"SET search_path TO {schema_name}, public"))
                await self._db_conn.commit()
```

- [ ] **Step 4: Run all SDK tests**

Run: `cd sdk && ../venv/bin/python3 -m pytest -q`
Expected: all pass (39)

- [ ] **Step 5: Commit**

```bash
git add sdk/belgrade_sdk/context.py sdk/tests/test_db_isolation.py
git commit -m "fix(sdk): refuse a tenant schema owned by another role"
```

---

### Task 3: App runners (`app_runner.py`)

**Files:**
- Create: `services/platform_controller/app_runner.py`
- Test: `services/platform_controller/tests/test_app_runner.py`

- [ ] **Step 1: Write the failing tests**

```python
# services/platform_controller/tests/test_app_runner.py
from __future__ import annotations
import asyncio
import stat
import subprocess
from unittest.mock import MagicMock, patch

import pytest

from app_runner import SubprocessRunner, SystemdRunner


class FakeRun:
    """Stands in for subprocess.run; records argv, returns canned results."""
    def __init__(self, returncode: int = 0, stdout: str = ""):
        self.calls: list[list[str]] = []
        self.returncode, self.stdout = returncode, stdout

    def __call__(self, argv, **kwargs):
        self.calls.append(list(argv))
        return subprocess.CompletedProcess(argv, self.returncode, self.stdout, "")


def test_systemd_start_writes_private_env_file_then_restarts_unit(tmp_path):
    run = FakeRun()
    runner = SystemdRunner(env_dir=tmp_path, run=run)

    asyncio.run(runner.start("shopping", tmp_path, {"BEG_OS_PORT": "9002", "PATH": "/usr/bin"}))

    env_file = tmp_path / "shopping.env"
    assert env_file.read_text() == 'BEG_OS_PORT="9002"\nPATH="/usr/bin"\n'
    assert stat.S_IMODE(env_file.stat().st_mode) == 0o600
    assert run.calls == [["systemctl", "restart", "belgrade-os-app@shopping.service"]]


def test_systemd_env_file_rejects_values_that_could_inject_lines(tmp_path):
    runner = SystemdRunner(env_dir=tmp_path, run=FakeRun())
    with pytest.raises(ValueError):
        asyncio.run(runner.start("shopping", tmp_path, {"X": 'a"\nEVIL=1'}))


def test_systemd_rejects_unsafe_app_id(tmp_path):
    runner = SystemdRunner(env_dir=tmp_path, run=FakeRun())
    with pytest.raises(ValueError):
        asyncio.run(runner.start("x.service other", tmp_path, {}))


def test_systemd_stop_and_status(tmp_path):
    run = FakeRun(returncode=0, stdout="1234\n")
    runner = SystemdRunner(env_dir=tmp_path, run=run)

    assert runner.is_running("shopping") is True
    assert runner.pid("shopping") == 1234
    asyncio.run(runner.stop("shopping"))

    assert ["systemctl", "is-active", "--quiet", "belgrade-os-app@shopping.service"] in run.calls
    assert ["systemctl", "stop", "belgrade-os-app@shopping.service"] in run.calls


def test_systemd_reports_stopped_unit(tmp_path):
    runner = SystemdRunner(env_dir=tmp_path, run=FakeRun(returncode=3, stdout="0\n"))
    assert runner.is_running("shopping") is False
    assert runner.pid("shopping") is None


def test_subprocess_runner_spawns_and_tracks(tmp_path):
    proc = MagicMock(pid=42)
    proc.poll.return_value = None
    runner = SubprocessRunner()
    with patch("app_runner.subprocess.Popen", return_value=proc) as popen, \
         patch("app_runner.open", MagicMock()):
        asyncio.run(runner.start("shopping", tmp_path, {"A": "1"}))

    assert popen.call_args.kwargs["env"] == {"A": "1"}
    assert popen.call_args.args[0][1] == str(tmp_path / "main.py")
    assert runner.is_running("shopping") and runner.pid("shopping") == 42
    proc.poll.return_value = 1
    assert runner.is_running("shopping") is False
```

- [ ] **Step 2: Run to verify failure**

Run: `cd services/platform_controller && ../../venv/bin/python3 -m pytest tests/test_app_runner.py -q`
Expected: `ModuleNotFoundError: No module named 'app_runner'`

- [ ] **Step 3: Implement**

```python
# services/platform_controller/app_runner.py
"""How an app process runs.

SubprocessRunner — child process of the controller (local dev, tests).
SystemdRunner    — sandboxed unit belgrade-os-app@<id>.service with its own
                   DynamicUser (production; audit findings H1/H2). The controller
                   may start/stop only those units, via a polkit rule.
"""
from __future__ import annotations

import asyncio
import os
import re
import signal
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Callable, Dict, Optional

_APP_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


class SubprocessRunner:
    def __init__(self) -> None:
        self._procs: Dict[str, subprocess.Popen] = {}

    async def start(self, app_id: str, app_dir: Path, env: Dict[str, str]) -> None:
        await self.stop(app_id)
        log_file = open(app_dir / "app.log", "a")
        log_file.write(f"\n--- App started at {datetime.now()} ---\n")
        log_file.flush()
        self._procs[app_id] = subprocess.Popen(
            [sys.executable, str(app_dir / "main.py")],
            env=env, stdout=log_file, stderr=log_file, preexec_fn=os.setsid,
        )

    async def stop(self, app_id: str) -> None:
        proc = self._procs.pop(app_id, None)
        if proc is None:
            return
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
            proc.wait(timeout=5)
        except Exception:
            pass

    def is_running(self, app_id: str) -> bool:
        proc = self._procs.get(app_id)
        return proc is not None and proc.poll() is None

    def pid(self, app_id: str) -> Optional[int]:
        proc = self._procs.get(app_id)
        return proc.pid if proc is not None else None


class SystemdRunner:
    def __init__(self, env_dir: Path, run: Callable[..., subprocess.CompletedProcess] = subprocess.run) -> None:
        self.env_dir = Path(env_dir)
        self._run = run

    @staticmethod
    def unit(app_id: str) -> str:
        if not _APP_ID_RE.match(app_id):
            raise ValueError(f"invalid app_id {app_id!r}")
        return f"belgrade-os-app@{app_id}.service"

    def _write_env_file(self, app_id: str, env: Dict[str, str]) -> None:
        lines = []
        for key, value in sorted(env.items()):
            if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", key) or any(c in value for c in '\n\r"\\'):
                raise ValueError(f"env entry not safe for an EnvironmentFile: {key}")
            lines.append(f'{key}="{value}"\n')
        path = self.env_dir / f"{app_id}.env"
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            f.writelines(lines)
        os.chmod(path, 0o600)

    async def start(self, app_id: str, app_dir: Path, env: Dict[str, str]) -> None:
        unit = self.unit(app_id)
        self._write_env_file(app_id, env)
        # restart, not start: a running unit must pick up the rewritten env file.
        await asyncio.to_thread(self._run, ["systemctl", "restart", unit], check=True, capture_output=True, text=True)

    async def stop(self, app_id: str) -> None:
        await asyncio.to_thread(self._run, ["systemctl", "stop", self.unit(app_id)], capture_output=True, text=True)

    def is_running(self, app_id: str) -> bool:
        r = self._run(["systemctl", "is-active", "--quiet", self.unit(app_id)], capture_output=True, text=True)
        return r.returncode == 0

    def pid(self, app_id: str) -> Optional[int]:
        r = self._run(["systemctl", "show", "-p", "MainPID", "--value", self.unit(app_id)],
                      capture_output=True, text=True)
        try:
            pid = int(r.stdout.strip())
        except ValueError:
            return None
        return pid or None
```

- [ ] **Step 4: Run tests — all pass**

Run: `cd services/platform_controller && ../../venv/bin/python3 -m pytest tests/test_app_runner.py -q`
Expected: `6 passed`

- [ ] **Step 5: Commit**

```bash
git add services/platform_controller/app_runner.py services/platform_controller/tests/test_app_runner.py
git commit -m "feat(platform_controller): subprocess and sandboxed systemd app runners"
```

---

### Task 4: Controller uses the runner and per-app DB URLs

**Files:**
- Modify: `services/platform_controller/config.py` (new fields)
- Modify: `services/platform_controller/main.py` (`AppProcess`, `AppSupervisor`, `GET /apps`, module wiring)
- Modify: `services/platform_controller/tests/test_app_process.py` (watchdog tests use a fake runner)
- Test: `services/platform_controller/tests/test_config.py`, `tests/test_app_process.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_config.py`:
```python
def test_app_runner_defaults_to_subprocess(monkeypatch):
    monkeypatch.delenv("CONTROLLER_APP_RUNNER", raising=False)
    c = Config(_env_file=None)
    assert c.app_runner == "subprocess"
    assert c.app_env_dir == "/run/belgrade-os/apps"
    assert c.app_db_secret == ""


def test_app_runner_systemd_from_env(monkeypatch):
    monkeypatch.setenv("CONTROLLER_APP_RUNNER", "systemd")
    monkeypatch.setenv("APP_DB_SECRET", "s")
    c = Config(_env_file=None)
    assert (c.app_runner, c.app_db_secret) == ("systemd", "s")
```

Append to `tests/test_app_process.py`:
```python
class FakeRunner:
    def __init__(self):
        self.started: dict[str, dict] = {}
        self.alive: set[str] = set()

    async def start(self, app_id, app_dir, env):
        self.started[app_id] = env
        self.alive.add(app_id)

    async def stop(self, app_id):
        self.alive.discard(app_id)

    def is_running(self, app_id):
        return app_id in self.alive

    def pid(self, app_id):
        return 4242 if app_id in self.alive else None


def test_supervisor_starts_apps_through_its_runner(tmp_path):
    from main import AppSupervisor
    (tmp_path / "shop").mkdir()
    (tmp_path / "shop" / "main.py").write_text("# stub")
    runner = FakeRunner()
    sup = AppSupervisor(apps_root=tmp_path, runner=runner)

    asyncio.run(sup.discover_and_start())

    assert runner.started["shop"]["BEG_OS_APP_ID"] == "shop"
    assert "shop" in sup.running_apps


def test_app_gets_its_own_db_url_from_the_provisioner(tmp_path):
    from main import AppSupervisor
    (tmp_path / "shop").mkdir()
    (tmp_path / "shop" / "main.py").write_text("# stub")
    runner = FakeRunner()

    async def provision(app_id):
        return f"postgresql+asyncpg://app_{app_id}:pw@localhost:5434/belgrade_os"

    sup = AppSupervisor(apps_root=tmp_path, runner=runner, db_url_for_app=provision)
    asyncio.run(sup.start_app("shop"))

    assert runner.started["shop"]["BEG_OS_DB_URL"].startswith("postgresql+asyncpg://app_shop:")


def test_watchdog_restarts_app_its_runner_reports_dead(tmp_path):
    from main import AppSupervisor
    (tmp_path / "shop").mkdir()
    (tmp_path / "shop" / "main.py").write_text("# stub")
    runner = FakeRunner()
    sup = AppSupervisor(apps_root=tmp_path, runner=runner)

    async def run():
        await sup.start_app("shop")
        runner.alive.discard("shop")          # crashed
        runner.started.clear()
        await sup._watch_tick()
        assert "shop" in runner.started      # restarted
        runner.started.clear()
        await sup._watch_tick()
        assert runner.started == {}          # healthy app is left alone

    asyncio.run(run())
```

Delete `test_watchdog_restarts_dead_app` and `test_watchdog_does_not_restart_live_app` (they poke `AppProcess.process`, which no longer exists; the new watchdog test covers both cases).

- [ ] **Step 2: Run to verify failure**

Run: `cd services/platform_controller && ../../venv/bin/python3 -m pytest -q`
Expected: failures for the 5 new tests (`AttributeError: 'Config' object has no attribute 'app_runner'`, `TypeError: AppSupervisor.__init__() got an unexpected keyword argument 'runner'`)

- [ ] **Step 3: Implement config** — in `config.py` `Config`, add:

```python
    # How apps run: "subprocess" (dev) or "systemd" (sandboxed units, production).
    app_runner: Literal["subprocess", "systemd"] = Field(
        default="subprocess", validation_alias=AliasChoices("CONTROLLER_APP_RUNNER"))
    app_env_dir: str = Field(
        default="/run/belgrade-os/apps", validation_alias=AliasChoices("CONTROLLER_APP_ENV_DIR"))
    # Derives each app's DB role password (app_db.py). Empty = apps use the platform DB user.
    app_db_secret: str = Field(default="", validation_alias=AliasChoices("APP_DB_SECRET"))
```
and `from typing import Literal` at the top.

- [ ] **Step 4: Implement `main.py`**

1. Imports: `from app_runner import SubprocessRunner, SystemdRunner` and `from app_db import app_db_url, app_role_name, app_role_password, ensure_app_role`; add `Awaitable, Callable` to the `typing` import.

2. `AppProcess`: drop `self.process`, `start()` and `stop()`; add `build_env()` returning the env dict (the body of today's `start()` up to the `BEG_OS_MCP_ENABLED` block), with the DB URL passed in:

```python
class AppProcess:
    def __init__(self, app_id: str, path: Path, port: int):
        self.app_id = app_id
        self.path = path
        self.port = port

    # _load_manifest unchanged

    def build_env(self, db_url: str) -> Dict[str, str]:
        manifest = self._load_manifest()
        notification_driver = (
            (manifest.notifications.driver if manifest and manifest.notifications else None)
            or os.getenv("BEG_OS_NOTIFICATION_DRIVER", "ntfy")
        )
        env = _app_base_env(os.environ)
        env["PYTHONPATH"] = os.pathsep.join(
            [str(SDK_ROOT)] + [p for p in env.get("PYTHONPATH", "").split(os.pathsep) if p]
        )
        env["BEG_OS_APP_ID"] = self.app_id
        env["BEG_OS_PORT"] = str(self.port)
        env["BEG_OS_CALLBACK_URL"] = f"http://localhost:{self.port}"
        env["BEG_OS_BRIDGE_URL"] = _cfg.bridge_url
        env["BEG_OS_DB_URL"] = db_url
        env["BEG_OS_REDIS_URL"] = os.getenv("APP_REDIS_URL") or os.getenv("BEG_OS_REDIS_URL", "redis://localhost:6379")
        env["BEG_OS_NOTIFICATION_DRIVER"] = notification_driver
        if manifest and manifest.mcp:
            env["BEG_OS_MCP_ENABLED"] = "true"
        else:
            env.pop("BEG_OS_MCP_ENABLED", None)
        return env

    async def start(self, runner=None, db_url: str = None) -> None:
        """Kept for tests/dev: build env and start with a SubprocessRunner."""
        runner = runner or SubprocessRunner()
        await runner.start(self.app_id, self.path, self.build_env(db_url or DB_URL))
```

(The existing env tests call `asyncio.run(app.start())` with `subprocess.Popen` patched as `main.subprocess.Popen`; change their patch target to `app_runner.subprocess.Popen` and `main.open` to `app_runner.open`.)

3. `AppSupervisor`:

```python
class AppSupervisor:
    def __init__(self, apps_root: Path, runner=None,
                 db_url_for_app: Optional[Callable[[str], Awaitable[str]]] = None):
        self.apps_root = apps_root
        self.runner = runner or SubprocessRunner()
        self.db_url_for_app = db_url_for_app
        self.running_apps: Dict[str, AppProcess] = {}
        self.app_ports: Dict[str, int] = {}
        self.next_port = 9001

    # discover_and_start unchanged

    async def start_app(self, app_id: str):
        # ... container / no-main.py / port allocation logic unchanged ...
        app_process = AppProcess(app_id, app_path, self.app_ports[app_id])
        try:
            db_url = await self.db_url_for_app(app_id) if self.db_url_for_app else DB_URL
            env = app_process.build_env(db_url)
            await self.runner.start(app_id, app_path, env)
        except ValueError as exc:
            logger.error("Skipping app %s — %s", app_id, exc)
            return
        self.running_apps[app_id] = app_process

    async def stop_app(self, app_id: str):
        if app_id in self.running_apps:
            await self.runner.stop(app_id)
            del self.running_apps[app_id]

    async def _watch_tick(self) -> None:
        dead = [a for a in self.running_apps if not self.runner.is_running(a)]
        for app_id in dead:
            logger.warning("app %s is not running — restarting", app_id)
            await self.start_app(app_id)
```

Remove the `if app_id in self.running_apps: await self.stop_app(app_id)` at the top of `start_app` (runners restart idempotently).

4. Module wiring (replace `app_supervisor = AppSupervisor(apps_root=APPS_ROOT)`):

```python
async def _provision_app_db(app_id: str) -> str:
    role = app_role_name(app_id)
    password = app_role_password(_cfg.app_db_secret, app_id)
    async with engine.begin() as conn:
        await ensure_app_role(conn, role, password, make_url(DB_URL).database)
    return app_db_url(DB_URL, role, password)


_runner = (SystemdRunner(env_dir=Path(_cfg.app_env_dir)) if _cfg.app_runner == "systemd"
           else SubprocessRunner())
if not _cfg.app_db_secret:
    logger.warning("APP_DB_SECRET not set — apps share the platform DB user (dev only)")
app_supervisor = AppSupervisor(
    apps_root=APPS_ROOT,
    runner=_runner,
    db_url_for_app=_provision_app_db if _cfg.app_db_secret else None,
)
```
with `from sqlalchemy.engine import make_url`.

5. `GET /apps`:

```python
@app.get("/apps")
async def list_apps():
    return {
        app_id: {"port": proc.port, "pid": app_supervisor.runner.pid(app_id)}
        for app_id, proc in app_supervisor.running_apps.items()
    }
```

- [ ] **Step 5: Run controller tests**

Run: `cd services/platform_controller && ../../venv/bin/python3 -m pytest -q`
Expected: all pass

- [ ] **Step 6: Commit**

```bash
git add services/platform_controller
git commit -m "feat(platform_controller): run apps via pluggable runner with per-app DB roles"
```

---

### Task 5: systemd unit, polkit rule, installer, .env

**Files:**
- Create: `deploy/belgrade-os-app@.service`, `deploy/50-belgrade-os.rules`, `deploy/platform_controller-runtime-dir.conf`
- Modify: `scripts/install-systemd.sh`, `scripts/setup.sh`

- [ ] **Step 1: App unit template**

```ini
# deploy/belgrade-os-app@.service
# One sandboxed unit per app, started/stopped by platform_controller (SystemdRunner).
# Audit findings H1/H2: apps get a throwaway user (not ivan, not in docker), see only
# their own code + SDK + venv, and can reach only localhost (bridge, Redis, Postgres).
# @ROOT@ is substituted by scripts/install-systemd.sh.
[Unit]
Description=Belgrade OS app — %i
After=belgrade-os@bridge.service

[Service]
Type=simple
DynamicUser=yes
EnvironmentFile=/run/belgrade-os/apps/%i.env
WorkingDirectory=@ROOT@/apps/%i
ExecStart=@ROOT@/venv/bin/python3 @ROOT@/apps/%i/main.py
Environment=PYTHONDONTWRITEBYTECODE=1
Restart=on-failure
RestartSec=5
StandardOutput=journal
StandardError=journal
SyslogIdentifier=beg-os-app-%i

# Filesystem: everything under /home hidden except what the app needs (read-only).
ProtectHome=tmpfs
BindReadOnlyPaths=@ROOT@/apps/%i @ROOT@/sdk @ROOT@/venv
ProtectSystem=strict
PrivateTmp=yes
PrivateDevices=yes
InaccessiblePaths=-/run/docker.sock -/var/run/docker.sock

# Processes / kernel
ProtectProc=invisible
ProcSubset=pid
ProtectKernelTunables=yes
ProtectKernelModules=yes
ProtectKernelLogs=yes
ProtectControlGroups=yes
ProtectClock=yes
ProtectHostname=yes
RestrictNamespaces=yes
RestrictRealtime=yes
LockPersonality=yes
SystemCallArchitectures=native
CapabilityBoundingSet=

# Network: localhost only — no LAN, no internet.
RestrictAddressFamilies=AF_INET AF_INET6 AF_UNIX
IPAddressDeny=any
IPAddressAllow=localhost
```

- [ ] **Step 2: polkit rule**

```js
// deploy/50-belgrade-os.rules — @USER@ substituted by scripts/install-systemd.sh.
// The controller (runs as @USER@) may start/stop/restart app units and nothing else.
polkit.addRule(function (action, subject) {
    if (action.id !== "org.freedesktop.systemd1.manage-units" || subject.user !== "@USER@") {
        return polkit.Result.NOT_HANDLED;
    }
    var unit = action.lookup("unit");
    var verb = action.lookup("verb");
    if (unit && /^belgrade-os-app@[A-Za-z0-9_-]+\.service$/.test(unit) &&
        (verb === "start" || verb === "stop" || verb === "restart")) {
        return polkit.Result.YES;
    }
    return polkit.Result.NOT_HANDLED;
});
```

- [ ] **Step 3: Controller runtime-dir drop-in**

```ini
# deploy/platform_controller-runtime-dir.conf
# /run/belgrade-os/apps holds each app's env file (0600, read by PID 1 for EnvironmentFile).
[Service]
RuntimeDirectory=belgrade-os belgrade-os/apps
RuntimeDirectoryMode=0700
# App units keep running across controller restarts and need their env files.
RuntimeDirectoryPreserve=yes
```

- [ ] **Step 4: Installer** — in `scripts/install-systemd.sh`, after the existing drop-in install, add:

```bash
sed -e "s|@ROOT@|$ROOT|g" "$ROOT/deploy/belgrade-os-app@.service" > "$UNIT_DIR/belgrade-os-app@.service"
install -m 644 "$ROOT/deploy/platform_controller-runtime-dir.conf" \
    "$UNIT_DIR/belgrade-os@platform_controller.service.d/runtime-dir.conf"
sed -e "s|@USER@|$RUN_USER|g" "$ROOT/deploy/50-belgrade-os.rules" > /etc/polkit-1/rules.d/50-belgrade-os.rules
chmod 644 /etc/polkit-1/rules.d/50-belgrade-os.rules
```

- [ ] **Step 5: `.env` template** — in `scripts/setup.sh` `generate_env`, after `MCP_DEFAULT_USER_ID=`:

```bash
# App isolation: per-app DB roles derive passwords from this; apps run as sandboxed units on Linux
APP_DB_SECRET=$(openssl rand -hex 32)
CONTROLLER_APP_RUNNER=$( [ "$(uname)" = "Linux" ] && echo systemd || echo subprocess )
```

- [ ] **Step 6: Verify syntax**

Run on the Mac: `bash -n scripts/install-systemd.sh && bash -n scripts/setup.sh`
Run on the Lenovo after `git pull` (no sudo needed):
```bash
d=$(mktemp -d) && sed "s|@ROOT@|$PWD|g" deploy/belgrade-os-app@.service > "$d/belgrade-os-app@.service" \
  && systemd-analyze verify "$d/belgrade-os-app@test.service"
```
Expected: no "Unknown key" / "Invalid" lines (a missing `/run/belgrade-os/apps/test.env` warning is fine).

- [ ] **Step 7: Commit**

```bash
git add deploy scripts
git commit -m "feat(deploy): sandboxed per-app systemd units, polkit rule, installer"
```

---

### Task 6: Deploy, migrate, verify on the Lenovo

- [ ] **Step 1: Full test suite on the Mac** — `make test` (test Postgres on 5433, Redis on 6379). Push.
- [ ] **Step 2: Lenovo** — `git pull`; append to `.env`: `APP_DB_SECRET=<openssl rand -hex 32>` and `CONTROLLER_APP_RUNNER=systemd`.
- [ ] **Step 3: Owner runs (sudo)** — `ssh -t lenovo 'sudo ~/server/projects/belgrade-os/scripts/install-systemd.sh'`
- [ ] **Step 4: Migrate the existing shopping schema** (owned by the platform superuser today):

```bash
ssh lenovo 'docker exec beg-os-db psql -U laurent -d belgrade_os -v ON_ERROR_STOP=1 -c "
  DO \$\$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '"'"'app_shopping'"'"') THEN CREATE ROLE app_shopping; END IF; END \$\$;
  ALTER SCHEMA app_family_shopping OWNER TO app_shopping;
  ALTER TABLE app_family_shopping.items OWNER TO app_shopping;"'
```
(`ALTER TABLE ... OWNER` also moves the `items_id_seq` sequence it owns. The controller sets the role's login/password on start.)

- [ ] **Step 5: Restart the controller** (kill its MainPID; systemd restarts it and, via `KillMode=control-group`, also stops the old child-process apps). On start it provisions `app_shopping`, writes `/run/belgrade-os/apps/shopping.env`, and starts `belgrade-os-app@shopping.service`.
- [ ] **Step 6: Verify isolation** with a throwaway probe app run under the same template (`apps/zz_probe/main.py`, not committed): it tries `open('<ROOT>/.env')`, connects to `/var/run/docker.sock`, `192.168.68.1:80`, `1.1.1.1:443`, and `127.0.0.1:8081`, prints results, exits. Start via `systemctl start belgrade-os-app@zz_probe` (allowed by polkit) after writing its env file; read `journalctl -u belgrade-os-app@zz_probe`. Expected: `.env` → not found, docker.sock → not found/denied, LAN and internet → blocked, bridge → reachable. Then remove the probe.
- [ ] **Step 7: Verify the app** — `systemctl show belgrade-os-app@shopping -p User,MainPID`; `ps -o user= -p <pid>` is not `ivan`; Postgres `SELECT rolsuper FROM pg_roles WHERE rolname='app_shopping'` → `f`; `systemd-analyze security belgrade-os-app@shopping` exposure ≤ 3.0; end-to-end `list_items` through the MCP proxy returns the existing items.
- [ ] **Step 8: Update Obsidian security note** — H1–H3 fixed with the verification evidence.
