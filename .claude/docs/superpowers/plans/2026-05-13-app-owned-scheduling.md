# App-Owned Scheduling Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let apps schedule and cancel their own tool calls from within tool handlers via `ctx.schedule()` / `ctx.unschedule()`, backed by the existing APScheduler infrastructure in Platform Controller.

**Architecture:** Apps write `ScheduleOp` proto messages to a new `tasks:schedule_ops` Redis stream. Platform Controller consumes the stream, persists schedules to `shared.schedules` in Postgres, and registers them with APScheduler. The APScheduler job fires the tool via Bridge exactly as the existing REST-managed scheduler does. Schedule identity is `"{app_id}:{user_id}:{name}"` — unique per user per app, with `user_id` stored as a field so schedules can be filtered without parsing IDs.

**Tech Stack:** Python 3.11, protobuf (grpc_tools), APScheduler 3.x, FastAPI, SQLAlchemy asyncpg, Redis asyncio, pytest-asyncio

---

### Task 1: Add ScheduleOp proto message

**Files:**
- Modify: `proto/belgrade_os.proto`

- [ ] **Step 1: Add the ScheduleOp message to the proto file**

Open `proto/belgrade_os.proto`. Append this section after the `VaultOperation` message block (after line 139):

```proto
// ─── Scheduling ───────────────────────────────────────────────────────────────

// App SDK → Redis Stream "tasks:schedule_ops" → Platform Controller
message ScheduleOp {
  enum OpType {
    OP_TYPE_UNSPECIFIED = 0;
    UPSERT              = 1;
    DELETE              = 2;
  }
  OpType op          = 1;
  string schedule_id = 2;   // "{app_id}:{user_id}:{name}" — APScheduler job id
  string app_id      = 3;
  string user_id     = 4;
  string tenant_id   = 5;
  string cron        = 6;   // 5-field cron expression; empty on DELETE
  string tool_name   = 7;   // e.g. "shopping:summarize"; empty on DELETE
  string params_json = 8;   // JSON object string; "{}" on DELETE
  string trace_id    = 9;
}
```

- [ ] **Step 2: Regenerate all proto outputs**

Run from the repo root:
```bash
make proto
```
Expected: `proto codegen complete` with no errors. This regenerates `gateway/gen/belgrade_os.pb.go`, `sdk/belgrade_sdk/gen/belgrade_os_pb2.py`, `platform_controller/gen/belgrade_os_pb2.py`, and all other `*/gen/belgrade_os_pb2.py` files.

- [ ] **Step 3: Verify the new message is accessible in Python**

```bash
cd sdk
python3 -c "from belgrade_sdk.gen import belgrade_os_pb2; op = belgrade_os_pb2.ScheduleOp(); op.op = belgrade_os_pb2.ScheduleOp.UPSERT; print('ok', op.op)"
```
Expected: `ok 1`

- [ ] **Step 4: Commit**

```bash
git add proto/belgrade_os.proto sdk/belgrade_sdk/gen/belgrade_os_pb2.py platform_controller/gen/belgrade_os_pb2.py runner/gen/belgrade_os_pb2.py inference/gen/belgrade_os_pb2.py notification/gen/belgrade_os_pb2.py vault_service/gen/belgrade_os_pb2.py gateway/gen/belgrade_os.pb.go
git commit -m "feat(proto): add ScheduleOp message for app-owned scheduling"
```

---

### Task 2: SDK — ctx.schedule() and ctx.unschedule()

**Files:**
- Modify: `sdk/belgrade_sdk/defaults.py`
- Modify: `sdk/belgrade_sdk/context.py`
- Create: `sdk/tests/test_schedule.py`

- [ ] **Step 1: Write failing tests**

Create `sdk/tests/test_schedule.py`:

```python
from __future__ import annotations
import json
import pytest
from unittest.mock import AsyncMock
from belgrade_sdk.context import AppContext
from belgrade_sdk.gen import belgrade_os_pb2


def _make_ctx(redis_pool=None) -> AppContext:
    return AppContext(
        app_id="shopping",
        user_id="u1",
        tenant_id="t1",
        trace_id="tr-1",
        bridge_url="http://bridge:8081",
        redis_pool=redis_pool,
    )


@pytest.mark.asyncio
async def test_schedule_publishes_upsert_op():
    mock_pool = AsyncMock()
    ctx = _make_ctx(redis_pool=mock_pool)

    await ctx.schedule("daily-summary", "0 9 * * *", "shopping:summarize", params={"limit": 10})

    mock_pool.xadd.assert_called_once()
    stream, fields = mock_pool.xadd.call_args[0]
    assert stream == "tasks:schedule_ops"

    op = belgrade_os_pb2.ScheduleOp()
    op.ParseFromString(fields["data"])
    assert op.op == belgrade_os_pb2.ScheduleOp.UPSERT
    assert op.schedule_id == "shopping:u1:daily-summary"
    assert op.app_id == "shopping"
    assert op.user_id == "u1"
    assert op.tenant_id == "t1"
    assert op.cron == "0 9 * * *"
    assert op.tool_name == "shopping:summarize"
    assert json.loads(op.params_json) == {"limit": 10}
    assert op.trace_id == "tr-1"


@pytest.mark.asyncio
async def test_schedule_defaults_empty_params():
    mock_pool = AsyncMock()
    ctx = _make_ctx(redis_pool=mock_pool)

    await ctx.schedule("nightly", "0 2 * * *", "shopping:reindex")

    _, fields = mock_pool.xadd.call_args[0]
    op = belgrade_os_pb2.ScheduleOp()
    op.ParseFromString(fields["data"])
    assert json.loads(op.params_json) == {}


@pytest.mark.asyncio
async def test_schedule_raises_without_redis():
    ctx = _make_ctx(redis_pool=None)
    with pytest.raises(RuntimeError, match="Redis pool not initialized"):
        await ctx.schedule("daily-summary", "0 9 * * *", "shopping:summarize")


@pytest.mark.asyncio
async def test_unschedule_publishes_delete_op():
    mock_pool = AsyncMock()
    ctx = _make_ctx(redis_pool=mock_pool)

    await ctx.unschedule("daily-summary")

    mock_pool.xadd.assert_called_once()
    stream, fields = mock_pool.xadd.call_args[0]
    assert stream == "tasks:schedule_ops"

    op = belgrade_os_pb2.ScheduleOp()
    op.ParseFromString(fields["data"])
    assert op.op == belgrade_os_pb2.ScheduleOp.DELETE
    assert op.schedule_id == "shopping:u1:daily-summary"
    assert op.app_id == "shopping"
    assert op.user_id == "u1"


@pytest.mark.asyncio
async def test_unschedule_raises_without_redis():
    ctx = _make_ctx(redis_pool=None)
    with pytest.raises(RuntimeError, match="Redis pool not initialized"):
        await ctx.unschedule("daily-summary")
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd sdk
python3 -m pytest tests/test_schedule.py -v
```
Expected: FAIL — `AttributeError: 'AppContext' object has no attribute 'schedule'`

- [ ] **Step 3: Add STREAM_SCHEDULE_OPS to defaults**

In `sdk/belgrade_sdk/defaults.py`, append after `STREAM_NOTIFICATIONS`:

```python
STREAM_SCHEDULE_OPS = "tasks:schedule_ops"
```

- [ ] **Step 4: Add schedule() and unschedule() to AppContext**

In `sdk/belgrade_sdk/context.py`, add the two methods to `AppContext` after the `emit()` method (before `cleanup()`):

```python
    async def schedule(
        self,
        name: str,
        cron: str,
        tool_name: str,
        params: dict | None = None,
    ) -> None:
        """Schedule a recurring tool call via tasks:schedule_ops stream."""
        import json as _json
        from .gen import belgrade_os_pb2

        if not self._redis_pool:
            raise RuntimeError("Redis pool not initialized in AppContext")

        op = belgrade_os_pb2.ScheduleOp()
        op.op = belgrade_os_pb2.ScheduleOp.UPSERT
        op.schedule_id = f"{self.app_id}:{self.user_id or ''}:{name}"
        op.app_id = self.app_id
        op.user_id = self.user_id or ""
        op.tenant_id = self.tenant_id or ""
        op.cron = cron
        op.tool_name = tool_name
        op.params_json = _json.dumps(params or {})
        op.trace_id = self.trace_id or ""

        await self._redis_pool.xadd(
            defaults.STREAM_SCHEDULE_OPS,
            {"data": op.SerializeToString()},
        )

    async def unschedule(self, name: str) -> None:
        """Cancel a scheduled tool call via tasks:schedule_ops stream."""
        from .gen import belgrade_os_pb2

        if not self._redis_pool:
            raise RuntimeError("Redis pool not initialized in AppContext")

        op = belgrade_os_pb2.ScheduleOp()
        op.op = belgrade_os_pb2.ScheduleOp.DELETE
        op.schedule_id = f"{self.app_id}:{self.user_id or ''}:{name}"
        op.app_id = self.app_id
        op.user_id = self.user_id or ""
        op.trace_id = self.trace_id or ""
        op.params_json = "{}"

        await self._redis_pool.xadd(
            defaults.STREAM_SCHEDULE_OPS,
            {"data": op.SerializeToString()},
        )
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
cd sdk
python3 -m pytest tests/test_schedule.py -v
```
Expected: 5 passed

- [ ] **Step 6: Run full SDK test suite to check for regressions**

```bash
cd sdk
python3 -m pytest tests/ -v
```
Expected: all tests pass

- [ ] **Step 7: Commit**

```bash
git add sdk/belgrade_sdk/defaults.py sdk/belgrade_sdk/context.py sdk/tests/test_schedule.py
git commit -m "feat(sdk): add ctx.schedule() and ctx.unschedule()"
```

---

### Task 3: Platform Controller — ScheduleEntry model + DB migration

**Files:**
- Modify: `platform_controller/scheduler.py` — add `app_id` to `ScheduleEntry`
- Modify: `platform_controller/main.py` — add `app_id` column migration + update existing schedule queries

- [ ] **Step 1: Add app_id field to ScheduleEntry**

In `platform_controller/scheduler.py`, update `ScheduleEntry`:

```python
class ScheduleEntry(BaseModel):
    id: str
    app_id: str = ""
    user_id: str
    tenant_id: str
    cron: str
    tool_name: str
    params: Dict = {}
```

- [ ] **Step 2: Add app_id column migration to startup**

In `platform_controller/main.py`, in the `startup_event` function, add this line after the `shared.schedules` CREATE TABLE block (around line 337, after the `app_permissions` CREATE TABLE):

```python
        await conn.execute(text(
            "ALTER TABLE shared.schedules ADD COLUMN IF NOT EXISTS app_id TEXT NOT NULL DEFAULT ''"
        ))
```

The full startup DB block should look like:

```python
    async with engine.begin() as conn:
        await conn.execute(text("CREATE SCHEMA IF NOT EXISTS shared"))
        await conn.execute(text("""
            CREATE TABLE IF NOT EXISTS shared.schedules (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                tenant_id TEXT NOT NULL,
                cron TEXT NOT NULL,
                tool_name TEXT NOT NULL,
                params JSONB DEFAULT '{}',
                updated_at TIMESTAMPTZ DEFAULT NOW()
            )
        """))
        await conn.execute(text(
            "ALTER TABLE shared.schedules ADD COLUMN IF NOT EXISTS app_id TEXT NOT NULL DEFAULT ''"
        ))
        await conn.execute(text("""
            CREATE TABLE IF NOT EXISTS shared.app_permissions (
                user_id TEXT NOT NULL,
                app_id TEXT NOT NULL,
                bundle_id TEXT NOT NULL DEFAULT 'default',
                role TEXT NOT NULL,
                PRIMARY KEY (user_id, app_id, bundle_id)
            )
        """))
```

- [ ] **Step 3: Update existing schedule load query to include app_id**

In `platform_controller/main.py`, in `startup_event`, update the schedule loading query (around line 351):

Replace:
```python
        result = await session.execute(text("SELECT id, user_id, tenant_id, cron, tool_name, params FROM shared.schedules"))
        for row in result.all():
            entry = ScheduleEntry(
                id=row[0],
                user_id=row[1],
                tenant_id=row[2],
                cron=row[3],
                tool_name=row[4],
                params=row[5]
            )
```

With:
```python
        result = await session.execute(text("SELECT id, app_id, user_id, tenant_id, cron, tool_name, params FROM shared.schedules"))
        for row in result.all():
            entry = ScheduleEntry(
                id=row[0],
                app_id=row[1],
                user_id=row[2],
                tenant_id=row[3],
                cron=row[4],
                tool_name=row[5],
                params=row[6]
            )
```

- [ ] **Step 4: Update /schedules POST to persist app_id**

In `platform_controller/main.py`, in `create_schedule`, replace the INSERT query:

```python
@app.post("/schedules")
async def create_schedule(entry: ScheduleEntry):
    async with SessionLocal() as session:
        await session.execute(text("""
            INSERT INTO shared.schedules (id, app_id, user_id, tenant_id, cron, tool_name, params, updated_at)
            VALUES (:id, :app_id, :user_id, :tenant_id, :cron, :tool_name, :params, NOW())
            ON CONFLICT (id) DO UPDATE SET
                app_id = EXCLUDED.app_id,
                user_id = EXCLUDED.user_id,
                tenant_id = EXCLUDED.tenant_id,
                cron = EXCLUDED.cron,
                tool_name = EXCLUDED.tool_name,
                params = EXCLUDED.params,
                updated_at = NOW()
        """), entry.dict())
        await session.commit()

    await scheduler_manager.add_schedule(entry)
    return {"status": "scheduled", "id": entry.id}
```

- [ ] **Step 5: Verify existing platform_controller tests still pass**

```bash
cd platform_controller
python3 -m pytest tests/ -v
```
Expected: all tests pass

- [ ] **Step 6: Commit**

```bash
git add platform_controller/scheduler.py platform_controller/main.py
git commit -m "feat(platform_controller): add app_id to ScheduleEntry and schedules table"
```

---

### Task 4: Platform Controller — schedule_ops stream consumer

**Files:**
- Modify: `platform_controller/main.py` — add `_process_schedule_op` and `_schedule_ops_consumer_loop`
- Create: `platform_controller/tests/test_schedule_ops.py`

- [ ] **Step 1: Write failing tests**

Create `platform_controller/tests/test_schedule_ops.py`:

```python
from __future__ import annotations
import asyncio
import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch, call
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
import main as ctrl_main
from scheduler import ScheduleEntry


def _build_schedule_op(
    op_type="UPSERT",
    schedule_id="shopping:u1:daily-summary",
    app_id="shopping",
    user_id="u1",
    tenant_id="t1",
    cron="0 9 * * *",
    tool_name="shopping:summarize",
    params_json='{"limit": 10}',
):
    from gen import belgrade_os_pb2
    op = belgrade_os_pb2.ScheduleOp()
    op.op = getattr(belgrade_os_pb2.ScheduleOp, op_type)
    op.schedule_id = schedule_id
    op.app_id = app_id
    op.user_id = user_id
    op.tenant_id = tenant_id
    op.cron = cron
    op.tool_name = tool_name
    op.params_json = params_json
    op.trace_id = "tr-1"
    return op.SerializeToString()


@pytest.mark.asyncio
async def test_process_schedule_op_upsert_calls_add_schedule():
    mock_scheduler = AsyncMock()
    mock_session_cm = AsyncMock()
    mock_session = AsyncMock()
    mock_session_cm.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session_cm.__aexit__ = AsyncMock(return_value=False)

    with patch.object(ctrl_main, "scheduler_manager", mock_scheduler), \
         patch.object(ctrl_main, "SessionLocal", return_value=mock_session_cm):
        await ctrl_main._process_schedule_op(_build_schedule_op("UPSERT"))

    mock_scheduler.add_schedule.assert_called_once()
    entry: ScheduleEntry = mock_scheduler.add_schedule.call_args[0][0]
    assert entry.id == "shopping:u1:daily-summary"
    assert entry.app_id == "shopping"
    assert entry.user_id == "u1"
    assert entry.cron == "0 9 * * *"
    assert entry.tool_name == "shopping:summarize"
    assert entry.params == {"limit": 10}


@pytest.mark.asyncio
async def test_process_schedule_op_delete_calls_remove_schedule():
    mock_scheduler = MagicMock()
    mock_session_cm = AsyncMock()
    mock_session = AsyncMock()
    mock_session_cm.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session_cm.__aexit__ = AsyncMock(return_value=False)

    with patch.object(ctrl_main, "scheduler_manager", mock_scheduler), \
         patch.object(ctrl_main, "SessionLocal", return_value=mock_session_cm):
        await ctrl_main._process_schedule_op(_build_schedule_op("DELETE"))

    mock_scheduler.remove_schedule.assert_called_once_with("shopping:u1:daily-summary")


@pytest.mark.asyncio
async def test_process_schedule_op_invalid_app_id_discards():
    mock_scheduler = MagicMock()

    with patch.object(ctrl_main, "scheduler_manager", mock_scheduler):
        await ctrl_main._process_schedule_op(
            _build_schedule_op("UPSERT", app_id="../../evil")
        )

    mock_scheduler.add_schedule.assert_not_called()


@pytest.mark.asyncio
async def test_process_schedule_op_malformed_proto_raises():
    with pytest.raises(Exception):
        await ctrl_main._process_schedule_op(b"not a proto")
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd platform_controller
python3 -m pytest tests/test_schedule_ops.py -v
```
Expected: FAIL — `AttributeError: module 'main' has no attribute '_process_schedule_op'`

- [ ] **Step 3: Implement _process_schedule_op and _schedule_ops_consumer_loop**

In `platform_controller/main.py`, add these two functions after `process_untrusted_call` and before `_untrusted_consumer_loop`. Import `from sqlalchemy import text` is already present.

```python
async def _process_schedule_op(data: bytes) -> None:
    from gen import belgrade_os_pb2

    op = belgrade_os_pb2.ScheduleOp()
    op.ParseFromString(data)

    if op.app_id and not _APP_ID_RE.match(op.app_id):
        logger.error("invalid app_id in ScheduleOp: %r — discarding", op.app_id)
        return

    if op.op == belgrade_os_pb2.ScheduleOp.UPSERT:
        entry = ScheduleEntry(
            id=op.schedule_id,
            app_id=op.app_id,
            user_id=op.user_id,
            tenant_id=op.tenant_id,
            cron=op.cron,
            tool_name=op.tool_name,
            params=json.loads(op.params_json) if op.params_json else {},
        )
        async with SessionLocal() as session:
            await session.execute(text("""
                INSERT INTO shared.schedules (id, app_id, user_id, tenant_id, cron, tool_name, params, updated_at)
                VALUES (:id, :app_id, :user_id, :tenant_id, :cron, :tool_name, :params, NOW())
                ON CONFLICT (id) DO UPDATE SET
                    cron = EXCLUDED.cron,
                    tool_name = EXCLUDED.tool_name,
                    params = EXCLUDED.params,
                    updated_at = NOW()
            """), entry.dict())
            await session.commit()
        await scheduler_manager.add_schedule(entry)
        logger.info("schedule upserted id=%s tool=%s cron=%s", op.schedule_id, op.tool_name, op.cron)

    elif op.op == belgrade_os_pb2.ScheduleOp.DELETE:
        async with SessionLocal() as session:
            await session.execute(
                text("DELETE FROM shared.schedules WHERE id = :id"),
                {"id": op.schedule_id},
            )
            await session.commit()
        scheduler_manager.remove_schedule(op.schedule_id)
        logger.info("schedule deleted id=%s", op.schedule_id)


async def _schedule_ops_consumer_loop(redis_url: str) -> None:
    import redis.asyncio as aioredis

    STREAM = "tasks:schedule_ops"
    GROUP = "schedule-ops-runners"
    CONSUMER = "platform-controller"

    rdb = aioredis.from_url(redis_url, decode_responses=False)
    try:
        await rdb.xgroup_create(STREAM, GROUP, id="0", mkstream=True)
    except Exception:
        pass  # BUSYGROUP on restart

    logger.info("schedule ops consumer started stream=%s group=%s", STREAM, GROUP)
    while True:
        try:
            results = await rdb.xreadgroup(
                groupname=GROUP,
                consumername=CONSUMER,
                streams={STREAM: ">"},
                count=1,
                block=2000,
            )
            if not results:
                continue
            _stream, messages = results[0]
            for msg_id, fields in messages:
                data = fields.get(b"data")
                if data is None:
                    await rdb.xack(STREAM, GROUP, msg_id)
                    continue
                try:
                    await _process_schedule_op(data)
                    await rdb.xack(STREAM, GROUP, msg_id)
                except Exception:
                    logger.exception(
                        "unhandled error msg=%s — not ACKed, will retry on restart", msg_id
                    )
        except Exception as exc:
            if "ConnectionError" in type(exc).__name__:
                logger.error("schedule ops consumer lost Redis connection, retrying in 5s")
                await asyncio.sleep(5)
            else:
                logger.exception("schedule ops consumer unexpected error")
                await asyncio.sleep(1)
```

- [ ] **Step 4: Start the consumer in startup_event**

In `platform_controller/main.py`, in `startup_event`, add after the untrusted consumer task line (around line 365):

```python
    asyncio.create_task(_schedule_ops_consumer_loop(REDIS_URL))
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
cd platform_controller
python3 -m pytest tests/test_schedule_ops.py -v
```
Expected: 4 passed

- [ ] **Step 6: Run full platform_controller test suite**

```bash
cd platform_controller
python3 -m pytest tests/ -v
```
Expected: all tests pass

- [ ] **Step 7: Commit**

```bash
git add platform_controller/main.py
git commit -m "feat(platform_controller): add schedule_ops stream consumer"
```

---

### Task 5: Platform Controller — schedule management endpoints

**Files:**
- Modify: `platform_controller/main.py` — update `list_schedules`, add `delete_app_schedules`
- Modify: `platform_controller/tests/test_schedule_ops.py` — add endpoint tests

- [ ] **Step 1: Write failing tests**

Append to `platform_controller/tests/test_schedule_ops.py`:

```python
from fastapi.testclient import TestClient


def _make_test_client():
    import os
    os.environ.setdefault("CONTROLLER_API_TOKEN", "test-token")
    return TestClient(ctrl_main.app)


@pytest.mark.asyncio
async def test_list_schedules_filter_by_app_id():
    mock_session_cm = AsyncMock()
    mock_session = AsyncMock()
    mock_session_cm.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session_cm.__aexit__ = AsyncMock(return_value=False)

    mock_row = MagicMock()
    mock_row._mapping = {
        "id": "shopping:u1:daily-summary",
        "app_id": "shopping",
        "user_id": "u1",
        "tenant_id": "t1",
        "cron": "0 9 * * *",
        "tool_name": "shopping:summarize",
        "params": {"limit": 10},
    }
    mock_result = MagicMock()
    mock_result.all.return_value = [mock_row]
    mock_session.execute = AsyncMock(return_value=mock_result)

    with patch.object(ctrl_main, "SessionLocal", return_value=mock_session_cm):
        client = _make_test_client()
        resp = client.get("/schedules?app_id=shopping")

    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["app_id"] == "shopping"


@pytest.mark.asyncio
async def test_delete_app_schedules_removes_all():
    mock_scheduler = MagicMock()
    mock_session_cm = AsyncMock()
    mock_session = AsyncMock()
    mock_session_cm.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session_cm.__aexit__ = AsyncMock(return_value=False)

    mock_result = MagicMock()
    mock_result.all.return_value = [("shopping:u1:daily-summary",), ("shopping:u2:nightly",)]
    mock_session.execute = AsyncMock(return_value=mock_result)

    with patch.object(ctrl_main, "scheduler_manager", mock_scheduler), \
         patch.object(ctrl_main, "SessionLocal", return_value=mock_session_cm):
        client = _make_test_client()
        resp = client.delete(
            "/apps/shopping/schedules",
            headers={"Authorization": "Bearer test-token"},
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["cancelled"] == 2
    assert body["app_id"] == "shopping"
    assert mock_scheduler.remove_schedule.call_count == 2


@pytest.mark.asyncio
async def test_delete_app_schedules_returns_zero_when_none():
    mock_scheduler = MagicMock()
    mock_session_cm = AsyncMock()
    mock_session = AsyncMock()
    mock_session_cm.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session_cm.__aexit__ = AsyncMock(return_value=False)

    mock_result = MagicMock()
    mock_result.all.return_value = []
    mock_session.execute = AsyncMock(return_value=mock_result)

    with patch.object(ctrl_main, "scheduler_manager", mock_scheduler), \
         patch.object(ctrl_main, "SessionLocal", return_value=mock_session_cm):
        client = _make_test_client()
        resp = client.delete(
            "/apps/shopping/schedules",
            headers={"Authorization": "Bearer test-token"},
        )

    assert resp.status_code == 200
    assert resp.json()["cancelled"] == 0


@pytest.mark.asyncio
async def test_delete_app_schedules_rejects_invalid_app_id():
    client = _make_test_client()
    resp = client.delete(
        "/apps/../evil/schedules",
        headers={"Authorization": "Bearer test-token"},
    )
    assert resp.status_code in (400, 404)


@pytest.mark.asyncio
async def test_delete_app_schedules_requires_token():
    client = _make_test_client()
    resp = client.delete("/apps/shopping/schedules")
    assert resp.status_code == 403
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd platform_controller
python3 -m pytest tests/test_schedule_ops.py -v -k "endpoint or filter or delete_app"
```
Expected: FAIL — no `delete_app_schedules` route exists and `list_schedules` doesn't accept `app_id`

- [ ] **Step 3: Update list_schedules to accept app_id and user_id filter params**

In `platform_controller/main.py`, replace the existing `list_schedules` function:

```python
@app.get("/schedules")
async def list_schedules(app_id: Optional[str] = None, user_id: Optional[str] = None):
    async with SessionLocal() as session:
        if app_id and user_id:
            result = await session.execute(
                text("SELECT id, app_id, user_id, tenant_id, cron, tool_name, params FROM shared.schedules WHERE app_id = :app_id AND user_id = :user_id"),
                {"app_id": app_id, "user_id": user_id},
            )
        elif app_id:
            result = await session.execute(
                text("SELECT id, app_id, user_id, tenant_id, cron, tool_name, params FROM shared.schedules WHERE app_id = :app_id"),
                {"app_id": app_id},
            )
        else:
            result = await session.execute(
                text("SELECT id, app_id, user_id, tenant_id, cron, tool_name, params FROM shared.schedules")
            )
        return [dict(row._mapping) for row in result.all()]
```

- [ ] **Step 4: Add delete_app_schedules endpoint**

In `platform_controller/main.py`, add after `delete_schedule`:

```python
@app.delete("/apps/{app_id}/schedules")
async def delete_app_schedules(app_id: str, _: None = Depends(_require_token)):
    if not _APP_ID_RE.match(app_id):
        raise HTTPException(status_code=400, detail="invalid app_id: must match ^[a-zA-Z0-9_-]{1,64}$")
    async with SessionLocal() as session:
        result = await session.execute(
            text("SELECT id FROM shared.schedules WHERE app_id = :app_id"),
            {"app_id": app_id},
        )
        ids = [row[0] for row in result.all()]
        for schedule_id in ids:
            scheduler_manager.remove_schedule(schedule_id)
        await session.execute(
            text("DELETE FROM shared.schedules WHERE app_id = :app_id"),
            {"app_id": app_id},
        )
        await session.commit()
    logger.info("deleted %d schedules for app_id=%s", len(ids), app_id)
    return {"cancelled": len(ids), "app_id": app_id}
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
cd platform_controller
python3 -m pytest tests/test_schedule_ops.py -v
```
Expected: all tests pass (including tests from Task 4)

- [ ] **Step 6: Run full platform_controller test suite**

```bash
cd platform_controller
python3 -m pytest tests/ -v
```
Expected: all tests pass

- [ ] **Step 7: Commit**

```bash
git add platform_controller/main.py platform_controller/tests/test_schedule_ops.py
git commit -m "feat(platform_controller): schedule management endpoints + app bulk cancel"
```

---

### Final verification

- [ ] **Run all service test suites**

```bash
cd sdk && python3 -m pytest tests/ -v && cd ../platform_controller && python3 -m pytest tests/ -v
```
Expected: all tests pass across both services
