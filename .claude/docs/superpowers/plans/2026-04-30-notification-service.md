# Notification Service Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a standalone notification service that consumes a Redis stream and dispatches notifications via a pluggable driver abstraction, with ntfy as the first driver; apps publish notifications through the SDK without knowing about delivery infrastructure.

**Architecture:** The notification service is a Python worker (same pattern as `runner/`) that XREADGROUPs from `tasks:notifications`. A `NotificationDriver` ABC defines the interface; `NtfyDriver` is the first implementation. Driver selection happens at startup from config — adding Firebase means adding a new class and a config value, no other changes. The SDK's `ctx.notify()` XADDs a `NotificationRequest` proto to the stream; the Platform Controller stamps `BEG_OS_NOTIFICATION_DRIVER` onto each app's env by reading it from `manifest.json` (falling back to global default). Apps are entirely unaware of which driver handles their notifications.

**Tech Stack:** Python, `redis-py`, `httpx`, `pydantic-settings`, `protobuf`, `pytest-asyncio`

---

## Redis Data Model

| Stream | Producer | Consumer |
|---|---|---|
| `tasks:notifications` | SDK `ctx.notify()` | Notification service (consumer group `notification-workers`) |

Each message has a single `data` field containing a serialised `NotificationRequest` proto.

---

## File Map

```
proto/belgrade_os.proto          — add NotificationPriority enum + NotificationRequest message
Makefile                         — add proto codegen targets for notification/ and sdk/

notification/
├── gen/                         — generated (gitignored), rebuilt with make proto
│   ├── __init__.py
│   └── belgrade_os_pb2.py
├── drivers/
│   ├── __init__.py              — NotificationDriver export + get_driver() factory
│   ├── base.py                  — NotificationDriver ABC
│   └── ntfy.py                  — NtfyDriver: HTTP POST to ntfy server
├── tests/
│   ├── __init__.py
│   ├── test_config.py
│   ├── test_redis_client.py
│   ├── test_drivers.py
│   └── test_worker.py
├── config.py                    — pydantic-settings Config
├── redis_client.py              — RedisClient (tasks:notifications stream)
├── worker.py                    — process_notification()
├── main.py                      — consumer loop entry point
├── pytest.ini
├── requirements.txt
└── requirements-dev.txt

sdk/belgrade_sdk/
├── gen/                         — generated (gitignored), rebuilt with make proto
│   ├── __init__.py
│   └── belgrade_os_pb2.py
├── context.py                   — replace HTTP notify() with Redis XADD
└── app.py                       — read BEG_OS_NOTIFICATION_DRIVER + BEG_OS_REDIS_URL

platform_controller/
└── main.py                      — AppProcess.start(): read manifest.json, inject driver env var
```

---

## Task 1: Proto — NotificationRequest + Makefile

**Files:**
- Modify: `proto/belgrade_os.proto`
- Modify: `Makefile`

- [x] **Step 1: Add NotificationPriority enum and NotificationRequest message to proto**
- [x] **Step 2: Add codegen targets to Makefile**
- [x] **Step 3: Regenerate proto**
- [x] **Step 4: Verify the new message is accessible**
- [x] **Step 5: Commit**

---

## Task 2: Notification Service — config + redis_client

**Files:**
- Create: `notification/config.py`
- Create: `notification/redis_client.py`
- Create: `notification/requirements.txt`
- Create: `notification/requirements-dev.txt`
- Create: `notification/pytest.ini`
- Create: `notification/tests/__init__.py`
- Create: `notification/tests/test_config.py`
- Create: `notification/tests/test_redis_client.py`

- [x] **Step 1: Write failing config tests**
- [x] **Step 2: Run to confirm failure**
- [x] **Step 3: Create notification/config.py**
- [x] **Step 4: Create supporting files**
- [x] **Step 5: Install deps and run config tests**
- [x] **Step 6: Write failing redis_client tests**
- [x] **Step 7: Run to confirm failure**
- [x] **Step 8: Create notification/redis_client.py**
- [x] **Step 9: Run all tests so far**
- [x] **Step 10: Commit**

---

## Task 3: Notification Service — driver abstraction + NtfyDriver

**Files:**
- Create: `notification/drivers/__init__.py`
- Create: `notification/drivers/base.py`
- Create: `notification/drivers/ntfy.py`
- Create: `notification/tests/test_drivers.py`

- [x] **Step 1: Write failing driver tests**
- [x] **Step 2: Run to confirm failure**
- [x] **Step 3: Create notification/drivers/base.py**
- [x] **Step 4: Create notification/drivers/ntfy.py**
- [x] **Step 5: Create notification/drivers/__init__.py**
- [x] **Step 6: Run all tests**
- [x] **Step 7: Commit**

---

## Task 4: Notification Service — worker + main

**Files:**
- Create: `notification/worker.py`
- Create: `notification/main.py`
- Create: `notification/tests/test_worker.py`

- [x] **Step 1: Write failing worker tests**
- [x] **Step 2: Run to confirm failure**
- [x] **Step 3: Create notification/worker.py**
- [x] **Step 4: Create notification/main.py**
- [x] **Step 5: Run full notification test suite**
- [x] **Step 6: Commit**

---

## Task 5: SDK — ctx.notify() via Redis

**Files:**
- Modify: `sdk/belgrade_sdk/context.py`
- Modify: `sdk/belgrade_sdk/app.py`
- Modify: `sdk/requirements.txt`

- [x] **Step 1: Update sdk/requirements.txt**
- [x] **Step 2: Regenerate proto for SDK (if not done in Task 1)**
- [x] **Step 3: Update sdk/belgrade_sdk/app.py**
- [x] **Step 4: Replace ctx.notify() in sdk/belgrade_sdk/context.py**
- [x] **Step 5: Verify the SDK imports cleanly**
- [x] **Step 6: Verify notify() publishes the correct proto shape**
- [x] **Step 7: Commit**

---

## Task 6: Platform Controller — manifest injection

**Files:**
- Modify: `platform_controller/main.py`

- [x] **Step 1: Write failing test**
- [x] **Step 2: Run test to confirm failure**
- [x] **Step 3: Update AppProcess in platform_controller/main.py**
- [x] **Step 4: Run tests**
- [x] **Step 5: Commit**
