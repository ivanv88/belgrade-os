# App-Owned Scheduling Design

## Goal

Let apps schedule and cancel their own tool calls from within tool handlers, using the existing APScheduler infrastructure in Platform Controller, without any direct HTTP dependency between the SDK and Platform Controller.

## Architecture

Apps write `ScheduleOp` proto messages to the `tasks:schedule_ops` Redis stream. Platform Controller consumes the stream and delegates to the existing `SchedulerManager`, which persists schedules in Postgres and fires them on cron via APScheduler.

```
App tool handler
  └─ ctx.schedule() / ctx.unschedule()
       └─ XADD tasks:schedule_ops  ──►  Platform Controller consumer
                                              └─ SchedulerManager.add_schedule()
                                              └─ SchedulerManager.remove_schedule()
                                              └─ shared.schedules (Postgres)
```

## Proto Contract

New message added to `proto/belgrade_os.proto`:

```proto
message ScheduleOp {
  enum OpType {
    UPSERT = 0;
    DELETE = 1;
  }
  OpType op = 1;
  string schedule_id = 2;   // "{app_id}:{name}" — e.g. "shopping:daily-summary"
  string app_id = 3;
  string user_id = 4;
  string tenant_id = 5;
  string cron = 6;          // standard 5-field cron; empty on DELETE
  string tool_name = 7;     // fully-qualified e.g. "shopping:summarize"; empty on DELETE
  string params_json = 8;   // JSON object; "{}" on DELETE
  string trace_id = 9;
}
```

## SDK API

Two new methods on `AppContext` in `sdk/belgrade_sdk/context.py`:

```python
await ctx.schedule("daily-summary", "0 9 * * *", "shopping:summarize", params={"limit": 10})
await ctx.unschedule("daily-summary")
```

- `schedule(name, cron, tool_name, params={})` — builds a `ScheduleOp(op=UPSERT)` and XADDs to `tasks:schedule_ops`. The `schedule_id` is `f"{app_id}:{name}"`. `app_id`, `user_id`, `tenant_id`, and `trace_id` are injected from context.
- `unschedule(name)` — builds a `ScheduleOp(op=DELETE)` with `schedule_id = f"{app_id}:{name}"` and XADDs to `tasks:schedule_ops`.
- Both raise `RuntimeError` if `_redis_pool` is not initialized.
- Methods live directly on `AppContext` (not behind a sub-adapter).

## Model Update

`ScheduleEntry` in `platform_controller/scheduler.py` gets a new field:

```python
class ScheduleEntry(BaseModel):
    id: str
    user_id: str
    tenant_id: str
    cron: str
    tool_name: str
    params: Dict = {}
    app_id: str = ""   # new — scopes schedule to originating app
```

## Database Migration

`shared.schedules` gets a new column:

```sql
ALTER TABLE shared.schedules ADD COLUMN IF NOT EXISTS app_id TEXT NOT NULL DEFAULT '';
```

Run at Platform Controller startup alongside the existing `CREATE TABLE IF NOT EXISTS` block.

## Platform Controller Consumer

New `_schedule_ops_consumer_loop(redis_url)` in `platform_controller/main.py`:

- Stream: `tasks:schedule_ops`, group: `schedule-ops-runners`, consumer: `platform-controller`
- Started as `asyncio.create_task()` in `startup_event`, same as the untrusted consumer.
- On `UPSERT`: builds a `ScheduleEntry` from the op fields, calls `scheduler_manager.add_schedule(entry)`, persists to `shared.schedules` (upsert by `id`).
- On `DELETE`: calls `scheduler_manager.remove_schedule(schedule_id)`, deletes from `shared.schedules WHERE id = :id`.
- Invalid `app_id` (fails `_APP_ID_RE`) → log error, ACK, discard.
- Reconnects on Redis `ConnectionError` with 5s backoff.

## Schedule Management Endpoints

Extending the existing `/schedules` routes in Platform Controller:

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/schedules` | List all schedules (existing) |
| `GET` | `/schedules?app_id={app_id}` | Filter schedules by app |
| `DELETE` | `/schedules/{schedule_id}` | Cancel one schedule (existing) |
| `DELETE` | `/apps/{app_id}/schedules` | Cancel all schedules for an app |

`DELETE /apps/{app_id}/schedules`:
1. Queries `shared.schedules WHERE app_id = :app_id`
2. Calls `scheduler_manager.remove_schedule(id)` for each
3. Bulk deletes from `shared.schedules`
4. Protected by `_require_token`
5. Returns `{"cancelled": N, "app_id": app_id}`

`GET /schedules?app_id` adds an optional query param to the existing handler; if absent, returns all (existing behaviour preserved).

## Error Handling

- Malformed proto in stream → log, ACK, discard (don't block the consumer)
- APScheduler failure on add/remove → log error, still ACK (prevents infinite retry on bad cron expressions)
- `DELETE /apps/{app_id}/schedules` with no schedules → returns `{"cancelled": 0, "app_id": app_id}` (not 404)

## Testing

- SDK: unit tests for `ctx.schedule()` and `ctx.unschedule()` — assert proto fields and stream write, mock `_redis_pool`
- Platform Controller: unit tests for the consumer logic — UPSERT calls `add_schedule`, DELETE calls `remove_schedule`, invalid `app_id` is discarded
- Platform Controller: test `DELETE /apps/{app_id}/schedules` — removes all matching schedules, returns correct count
- Platform Controller: test `GET /schedules?app_id` filter

## Out of Scope

- App force stop / process lifecycle
- Schedule retry/backoff on tool execution failure
- Firebase notification driver
