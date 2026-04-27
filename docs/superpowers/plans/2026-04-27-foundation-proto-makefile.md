# Belgrade OS Foundation — Proto Contract & Makefile

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Establish the shared message contract between all five Belgrade OS services via a single `.proto` file, wire language-specific codegen for Go / Python / Rust, scaffold service directories, and add Redis to docker-compose.

**Architecture:** Distributed monorepo — five services in subdirectories (`gateway/` Go, `runner/` Python, `inference/` Python, `bridge/` Rust), all communicating via Redis Streams (tasks & tool calls) and Redis Pub/Sub (SSE events). `proto/belgrade_os.proto` is the single source of truth for every message shape. No gRPC transport is defined here — proto is used only for serialization, not RPC.

**Tech Stack:** Protocol Buffers 3, `protoc`, `protoc-gen-go v1.34`, `grpcio-tools` (Python), `prost v0.12` (Rust), GNU Make, Docker Compose 3.

> **All tasks run from the repo root:** `/Users/ivanvladisavljevic/Projects/belgrade-os/`

---

## File Map

| File | Action | Purpose |
|---|---|---|
| `proto/belgrade_os.proto` | Create | Single contract — all inter-service message types |
| `gateway/go.mod` | Create | Go module for Edge Gateway |
| `gateway/gen/proto_test.go` | Create | Go smoke test — verifies codegen produces usable structs |
| `runner/requirements.txt` | Create | Python deps for Resource Runner |
| `runner/gen/__init__.py` | Create | Makes gen/ a Python package |
| `runner/tests/__init__.py` | Create | Pytest discovery |
| `runner/tests/test_proto.py` | Create | Python smoke test for generated messages |
| `inference/requirements.txt` | Create | Python deps for Inference Controller |
| `inference/gen/__init__.py` | Create | Makes gen/ a Python package |
| `inference/tests/__init__.py` | Create | Pytest discovery |
| `inference/tests/test_proto.py` | Create | Same smoke tests as runner |
| `bridge/Cargo.toml` | Create | Rust crate for Capability Bridge |
| `bridge/build.rs` | Create | prost-build codegen step |
| `bridge/src/lib.rs` | Create | Generated proto inclusion + Rust smoke tests |
| `Makefile` | Create | `deps`, `proto`, `build`, `test`, `dev`, `clean` |
| `docker-compose.yml` | Modify | Add Redis 7 service |
| `.gitignore` | Modify | Ignore gen/ dirs and build artifacts |

---

## Prerequisites — Install Toolchain

Before Task 1, install tools on your macOS dev machine. These are not committed.

```bash
# From repo root
brew install protobuf go rust
go install google.golang.org/protobuf/cmd/protoc-gen-go@latest
export PATH="$PATH:$(go env GOPATH)/bin"

source venv/bin/activate   # or create: python3 -m venv venv && source venv/bin/activate
pip install "grpcio-tools==1.64.1" "protobuf==4.25.3" "pytest==8.2.2"

# Verify every tool
protoc --version           # libprotoc 26.x or higher
go version                 # go1.22.x or higher
cargo --version            # cargo 1.77.x or higher
protoc-gen-go --version    # protoc-gen-go v1.34.x
python -m grpc_tools.protoc --version  # libprotoc 26.x
```

---

### Task 1: Repository Skeleton + .gitignore

**Files:**
- Modify: `.gitignore`
- Create directories: `proto/`, `gateway/`, `runner/`, `inference/`, `bridge/`

- [ ] **Step 1: Create service directories**

```bash
mkdir -p proto gateway runner/tests runner/gen inference/tests inference/gen bridge/src
```

- [ ] **Step 2: Verify directories exist**

```bash
ls -d proto gateway runner inference bridge
```

Expected:
```
bridge  gateway  inference  proto  runner
```

- [ ] **Step 3: Append to .gitignore**

Open `.gitignore` and add at the bottom:

```
# Proto generated code
gateway/gen/
runner/gen/
inference/gen/

# Go
gateway/bin/
gateway/*.test

# Rust
bridge/target/

# Python service caches
runner/__pycache__/
runner/.pytest_cache/
inference/__pycache__/
inference/.pytest_cache/

# Redis data
data/redis/
```

- [ ] **Step 4: Verify .gitignore has new entries**

```bash
grep "gateway/gen" .gitignore && grep "bridge/target" .gitignore
```

Expected: both lines print.

- [ ] **Step 5: Commit**

```bash
git add .gitignore
git commit -m "chore: scaffold distributed monorepo structure + update gitignore"
```

---

### Task 2: proto/belgrade_os.proto

**Files:**
- Create: `proto/belgrade_os.proto`

- [ ] **Step 1: Confirm proto file does not exist**

```bash
ls proto/
```

Expected: empty or no such file.

- [ ] **Step 2: Write proto/belgrade_os.proto**

```protobuf
syntax = "proto3";

package belgrade_os;

option go_package = "belgrade-os/gateway/gen;belgrade_os";

// ─── Core workflow ────────────────────────────────────────────────────────────

// Dropped by Edge Gateway into Redis Stream "tasks:inbound"
message Task {
  string task_id       = 1;  // UUID v4
  string user_id       = 2;  // resolved from JWT sub claim
  string prompt        = 3;
  int64  created_at_ms = 4;  // Unix epoch milliseconds
}

// MCP tool definition — namespaced by Capability Bridge (e.g. "shopping:add_item")
message Tool {
  string name              = 1;
  string description       = 2;
  string input_schema_json = 3;  // JSON Schema object serialised as a string
  string app_id            = 4;  // origin app (e.g. "shopping")
}

// Tool call: Inference Controller → Redis Stream "tasks:tool_calls" → Resource Runner
message ToolCall {
  string call_id    = 1;  // UUID v4, unique per invocation
  string task_id    = 2;  // parent Task.task_id
  string tool_name  = 3;  // namespaced: "shopping:add_item"
  string input_json = 4;  // JSON object matching Tool.input_schema_json
}

// Tool result: Resource Runner → Redis Stream "tasks:tool_results" → Inference Controller
message ToolResult {
  string call_id     = 1;
  string task_id     = 2;
  bool   success     = 3;
  string output_json = 4;  // non-empty on success=true
  string error       = 5;  // non-empty on success=false
}

// ─── Streaming ────────────────────────────────────────────────────────────────

// Inference Controller → Redis Pub/Sub "sse:{task_id}" → Gateway → browser SSE
message ThoughtEvent {
  string           task_id = 1;
  string           user_id = 2;
  ThoughtEventType type    = 3;
  string           content = 4;
}

enum ThoughtEventType {
  THINKING       = 0;
  TOOL_USE       = 1;
  RESPONSE_CHUNK = 2;
  DONE           = 3;
  ERROR          = 4;
}

// ─── Capability Bridge ────────────────────────────────────────────────────────

// Resource Runner → Capability Bridge when an app is loaded or reloaded
message AppToolsRegistration {
  string        app_id = 1;
  repeated Tool tools  = 2;
}

// Capability Bridge → Inference Controller in response to a tool-list query
message ToolListResponse {
  repeated Tool tools = 1;
}

// ─── Resource Runner ──────────────────────────────────────────────────────────

// Stored in Redis key "lease:{worker_id}" during tool execution
message WorkerLease {
  string worker_id     = 1;
  string task_id       = 2;
  string call_id       = 3;
  int64  leased_at_ms  = 4;
  int64  expires_at_ms = 5;
}
```

- [ ] **Step 3: Validate the proto file compiles**

```bash
protoc -Iproto --descriptor_set_out=/dev/null proto/belgrade_os.proto
```

Expected: no output, exit code 0. If you see an error, check for typos in field names or missing semicolons.

- [ ] **Step 4: Commit**

```bash
git add proto/belgrade_os.proto
git commit -m "feat: define belgrade_os proto contract — 8 inter-service message types"
```

---

### Task 3: Go codegen — gateway/

**Files:**
- Create: `gateway/go.mod`
- Create: `gateway/gen/proto_test.go`

- [ ] **Step 1: Write the failing test**

Create `gateway/gen/proto_test.go`:

```go
package belgrade_os_test

import (
	"testing"

	belgrade "belgrade-os/gateway/gen"
)

func TestTaskMessage(t *testing.T) {
	task := &belgrade.Task{
		TaskId:      "task-001",
		UserId:      "user-1",
		Prompt:      "What's for dinner?",
		CreatedAtMs: 1_700_000_000_000,
	}
	if task.GetTaskId() != "task-001" {
		t.Fatalf("expected task-001, got %q", task.GetTaskId())
	}
	if task.GetPrompt() != "What's for dinner?" {
		t.Fatalf("unexpected prompt: %q", task.GetPrompt())
	}
}

func TestToolCallMessage(t *testing.T) {
	call := &belgrade.ToolCall{
		CallId:    "call-001",
		TaskId:    "task-001",
		ToolName:  "shopping:add_item",
		InputJson: `{"item": "milk", "qty": 2}`,
	}
	if call.GetToolName() != "shopping:add_item" {
		t.Fatalf("expected shopping:add_item, got %q", call.GetToolName())
	}
}

func TestToolResultFailure(t *testing.T) {
	result := &belgrade.ToolResult{
		CallId:  "call-001",
		TaskId:  "task-001",
		Success: false,
		Error:   "app crashed",
	}
	if result.GetSuccess() {
		t.Fatal("expected success=false")
	}
	if result.GetError() != "app crashed" {
		t.Fatalf("unexpected error: %q", result.GetError())
	}
}

func TestThoughtEventType(t *testing.T) {
	ev := &belgrade.ThoughtEvent{
		TaskId:  "task-001",
		UserId:  "user-1",
		Type:    belgrade.ThoughtEventType_RESPONSE_CHUNK,
		Content: "pasta is great",
	}
	if ev.GetType() != belgrade.ThoughtEventType_RESPONSE_CHUNK {
		t.Fatalf("expected RESPONSE_CHUNK, got %v", ev.GetType())
	}
}
```

- [ ] **Step 2: Initialize Go module**

```bash
cd gateway && go mod init belgrade-os/gateway && cd ..
```

Expected: creates `gateway/go.mod`.

- [ ] **Step 3: Run test to verify it fails**

```bash
cd gateway && go test ./gen/... 2>&1 | head -10
```

Expected: FAIL — `cannot find package "belgrade-os/gateway/gen"` (generated file does not exist yet).

- [ ] **Step 4: Run Go proto codegen**

```bash
mkdir -p gateway/gen
protoc -Iproto \
  --go_out=gateway/gen \
  --go_opt=paths=source_relative \
  proto/belgrade_os.proto
```

Verify generated file exists:

```bash
ls gateway/gen/
```

Expected: `belgrade_os.pb.go  proto_test.go`

- [ ] **Step 5: Add protobuf Go dependency**

```bash
cd gateway && go get google.golang.org/protobuf@v1.34.0 && go mod tidy && cd ..
```

- [ ] **Step 6: Run test to verify it passes**

```bash
cd gateway && go test ./gen/... -v
```

Expected:
```
--- PASS: TestTaskMessage (0.00s)
--- PASS: TestToolCallMessage (0.00s)
--- PASS: TestToolResultFailure (0.00s)
--- PASS: TestThoughtEventType (0.00s)
PASS
```

- [ ] **Step 7: Commit**

```bash
git add gateway/go.mod gateway/go.sum gateway/gen/proto_test.go
git commit -m "feat: Go codegen skeleton — gateway/gen + 4 smoke tests passing"
```

Note: `gateway/gen/belgrade_os.pb.go` is git-ignored (generated); only `proto_test.go` and `go.mod` are committed.

---

### Task 4: Python codegen — runner/ and inference/

**Files:**
- Create: `runner/requirements.txt`
- Create: `runner/gen/__init__.py`
- Create: `runner/tests/__init__.py`
- Create: `runner/tests/test_proto.py`
- Create: `inference/requirements.txt`
- Create: `inference/gen/__init__.py`
- Create: `inference/tests/__init__.py`
- Create: `inference/tests/test_proto.py`

- [ ] **Step 1: Write the failing tests**

Create `runner/tests/test_proto.py`:

```python
from __future__ import annotations
from gen import belgrade_os_pb2


def test_task_message() -> None:
    task = belgrade_os_pb2.Task()
    task.task_id = "task-001"
    task.user_id = "user-1"
    task.prompt = "What's for dinner?"
    task.created_at_ms = 1_700_000_000_000
    assert task.task_id == "task-001"
    assert task.prompt == "What's for dinner?"


def test_tool_call_message() -> None:
    call = belgrade_os_pb2.ToolCall()
    call.call_id = "call-001"
    call.task_id = "task-001"
    call.tool_name = "shopping:add_item"
    call.input_json = '{"item": "milk"}'
    assert call.tool_name == "shopping:add_item"


def test_thought_event_type() -> None:
    ev = belgrade_os_pb2.ThoughtEvent()
    ev.task_id = "task-001"
    ev.user_id = "user-1"
    ev.type = belgrade_os_pb2.RESPONSE_CHUNK
    ev.content = "pasta is great"
    assert ev.type == belgrade_os_pb2.RESPONSE_CHUNK


def test_tool_result_failure() -> None:
    result = belgrade_os_pb2.ToolResult()
    result.call_id = "call-001"
    result.task_id = "task-001"
    result.success = False
    result.error = "app crashed"
    assert not result.success
    assert result.error == "app crashed"


def test_worker_lease_fields() -> None:
    lease = belgrade_os_pb2.WorkerLease()
    lease.worker_id = "worker-1"
    lease.task_id = "task-001"
    lease.call_id = "call-001"
    lease.leased_at_ms = 1_700_000_000_000
    lease.expires_at_ms = 1_700_000_060_000
    assert lease.expires_at_ms > lease.leased_at_ms
```

Copy the exact same content to `inference/tests/test_proto.py`.

- [ ] **Step 2: Create requirements and __init__ files**

```bash
# runner
cat > runner/requirements.txt << 'EOF'
grpcio-tools==1.64.1
protobuf==4.25.3
pytest==8.2.2
EOF

touch runner/gen/__init__.py runner/tests/__init__.py

# inference
cat > inference/requirements.txt << 'EOF'
grpcio-tools==1.64.1
protobuf==4.25.3
pytest==8.2.2
EOF

touch inference/gen/__init__.py inference/tests/__init__.py
```

- [ ] **Step 3: Run tests to verify they fail**

```bash
cd runner && python -m pytest tests/test_proto.py -v 2>&1 | head -10
```

Expected: FAIL — `ModuleNotFoundError: No module named 'gen.belgrade_os_pb2'`

- [ ] **Step 4: Run Python codegen for runner/**

```bash
python -m grpc_tools.protoc \
  -Iproto \
  --python_out=runner/gen \
  proto/belgrade_os.proto
```

Expected: creates `runner/gen/belgrade_os_pb2.py`.

- [ ] **Step 5: Run Python codegen for inference/**

```bash
python -m grpc_tools.protoc \
  -Iproto \
  --python_out=inference/gen \
  proto/belgrade_os.proto
```

Expected: creates `inference/gen/belgrade_os_pb2.py`.

- [ ] **Step 6: Run runner tests**

```bash
cd runner && python -m pytest tests/test_proto.py -v
```

Expected:
```
PASSED tests/test_proto.py::test_task_message
PASSED tests/test_proto.py::test_tool_call_message
PASSED tests/test_proto.py::test_thought_event_type
PASSED tests/test_proto.py::test_tool_result_failure
PASSED tests/test_proto.py::test_worker_lease_fields
5 passed
```

- [ ] **Step 7: Run inference tests**

```bash
cd inference && python -m pytest tests/test_proto.py -v
```

Expected: same 5 tests PASSED.

- [ ] **Step 8: Commit**

```bash
git add runner/ inference/
git commit -m "feat: Python codegen skeletons — runner + inference, 5 smoke tests each"
```

Note: `runner/gen/belgrade_os_pb2.py` and `inference/gen/belgrade_os_pb2.py` are git-ignored.

---

### Task 5: Rust codegen — bridge/

**Files:**
- Create: `bridge/Cargo.toml`
- Create: `bridge/build.rs`
- Create: `bridge/src/lib.rs`

- [ ] **Step 1: Write the failing test**

Create `bridge/src/lib.rs` with only the test module (no `include!` yet — it will fail to compile):

```rust
#[cfg(test)]
mod tests {
    #[test]
    fn test_proto_placeholder() {
        // Will be replaced once build.rs generates the real module
        panic!("replace with real proto tests after build.rs is added");
    }
}
```

- [ ] **Step 2: Create bridge/Cargo.toml**

```toml
[package]
name = "bridge"
version = "0.1.0"
edition = "2021"

[dependencies]
prost = "0.12"

[build-dependencies]
prost-build = "0.12"
```

- [ ] **Step 3: Run cargo test to see the placeholder fail**

```bash
cd bridge && cargo test 2>&1 | tail -10
```

Expected: FAIL — `test tests::test_proto_placeholder ... FAILED` (panics with "replace with real proto tests").

- [ ] **Step 4: Create bridge/build.rs**

```rust
fn main() -> Result<(), Box<dyn std::error::Error>> {
    prost_build::compile_protos(
        &["../proto/belgrade_os.proto"],
        &["../proto"],
    )?;
    Ok(())
}
```

- [ ] **Step 5: Replace bridge/src/lib.rs with real tests**

```rust
pub mod belgrade_os {
    include!(concat!(env!("OUT_DIR"), "/belgrade_os.rs"));
}

#[cfg(test)]
mod tests {
    use super::belgrade_os::{Task, ToolCall, ToolResult, ThoughtEvent, ThoughtEventType};

    #[test]
    fn test_task_fields() {
        let task = Task {
            task_id: "task-001".to_string(),
            user_id: "user-1".to_string(),
            prompt: "What's for dinner?".to_string(),
            created_at_ms: 1_700_000_000_000,
        };
        assert_eq!(task.task_id, "task-001");
        assert_eq!(task.prompt, "What's for dinner?");
    }

    #[test]
    fn test_tool_call_fields() {
        let call = ToolCall {
            call_id: "call-001".to_string(),
            task_id: "task-001".to_string(),
            tool_name: "shopping:add_item".to_string(),
            input_json: r#"{"item": "milk"}"#.to_string(),
        };
        assert_eq!(call.tool_name, "shopping:add_item");
    }

    #[test]
    fn test_tool_result_failure() {
        let result = ToolResult {
            call_id: "call-001".to_string(),
            task_id: "task-001".to_string(),
            success: false,
            output_json: String::new(),
            error: "app crashed".to_string(),
        };
        assert!(!result.success);
        assert_eq!(result.error, "app crashed");
    }

    #[test]
    fn test_thought_event_done() {
        let ev = ThoughtEvent {
            task_id: "task-001".to_string(),
            user_id: "user-1".to_string(),
            r#type: ThoughtEventType::Done as i32,
            content: String::new(),
        };
        assert_eq!(ev.r#type, ThoughtEventType::Done as i32);
    }
}
```

- [ ] **Step 6: Run cargo test to verify it passes**

```bash
cd bridge && cargo test -- --nocapture
```

Expected:
```
running 4 tests
test tests::test_task_fields ... ok
test tests::test_tool_call_fields ... ok
test tests::test_tool_result_failure ... ok
test tests::test_thought_event_done ... ok

test result: ok. 4 passed; 0 failed; 0 ignored
```

- [ ] **Step 7: Commit**

```bash
cd bridge
git add Cargo.toml Cargo.lock build.rs src/lib.rs
cd ..
git commit -m "feat: Rust codegen skeleton — bridge/ prost + 4 smoke tests passing"
```

---

### Task 6: Makefile

**Files:**
- Create: `Makefile`

- [ ] **Step 1: Confirm no Makefile exists**

```bash
ls Makefile 2>&1
```

Expected: `ls: Makefile: No such file or directory`

- [ ] **Step 2: Write Makefile**

```makefile
.PHONY: proto build test dev clean deps

PROTO_SRC := proto/belgrade_os.proto

# ─── Dependencies (macOS) ─────────────────────────────────────────────────────
deps:
	brew install protobuf go rust
	go install google.golang.org/protobuf/cmd/protoc-gen-go@latest
	pip install "grpcio-tools==1.64.1" "protobuf==4.25.3" "pytest==8.2.2"

# ─── Proto codegen ────────────────────────────────────────────────────────────
proto: gateway/gen/belgrade_os.pb.go runner/gen/belgrade_os_pb2.py inference/gen/belgrade_os_pb2.py
	@echo "proto codegen complete"

gateway/gen/belgrade_os.pb.go: $(PROTO_SRC)
	mkdir -p gateway/gen
	protoc -Iproto \
	  --go_out=gateway/gen \
	  --go_opt=paths=source_relative \
	  $(PROTO_SRC)

runner/gen/belgrade_os_pb2.py: $(PROTO_SRC)
	mkdir -p runner/gen
	touch runner/gen/__init__.py
	python -m grpc_tools.protoc -Iproto --python_out=runner/gen $(PROTO_SRC)

inference/gen/belgrade_os_pb2.py: $(PROTO_SRC)
	mkdir -p inference/gen
	touch inference/gen/__init__.py
	python -m grpc_tools.protoc -Iproto --python_out=inference/gen $(PROTO_SRC)

# Rust codegen runs via bridge/build.rs — no explicit Make target needed.

# ─── Build ────────────────────────────────────────────────────────────────────
build: proto
	cd gateway && go build ./...
	cd bridge && cargo build --release

# ─── Test ─────────────────────────────────────────────────────────────────────
test: proto
	cd gateway && go test ./... -v
	cd runner && python -m pytest tests/ -v
	cd inference && python -m pytest tests/ -v
	cd bridge && cargo test

# ─── Dev infrastructure ───────────────────────────────────────────────────────
dev:
	docker-compose up -d redis cloudflared

# ─── Clean generated artifacts ────────────────────────────────────────────────
clean:
	rm -rf gateway/gen runner/gen inference/gen
	cd bridge && cargo clean
```

- [ ] **Step 3: Run make clean then make proto end-to-end**

```bash
make clean && make proto
```

Expected:
```
rm -rf gateway/gen runner/gen inference/gen
...
proto codegen complete
```

Verify all three generated files exist:

```bash
ls gateway/gen/ runner/gen/ inference/gen/
```

Expected:
```
gateway/gen/:
belgrade_os.pb.go

runner/gen/:
__init__.py  belgrade_os_pb2.py

inference/gen/:
__init__.py  belgrade_os_pb2.py
```

- [ ] **Step 4: Run make test**

```bash
make test
```

Expected: all Go, Python (runner), Python (inference), and Rust tests PASS.

- [ ] **Step 5: Commit**

```bash
git add Makefile
git commit -m "feat: Makefile — proto/build/test/dev/clean/deps targets"
```

---

### Task 7: docker-compose.yml — add Redis

**Files:**
- Modify: `docker-compose.yml`

- [ ] **Step 1: Read current docker-compose.yml**

```bash
cat docker-compose.yml
```

- [ ] **Step 2: Add Redis service**

In `docker-compose.yml`, add the following service block after the `db:` service (keep all existing services unchanged):

```yaml
  redis:
    image: redis:7-alpine
    container_name: belgrade-redis
    restart: always
    command: redis-server --appendonly yes
    ports:
      - "6379:6379"
    volumes:
      - ./data/redis:/data
```

- [ ] **Step 3: Verify Redis appears in service list**

```bash
docker-compose config --services
```

Expected output includes: `db`, `redis`, `tunnel`

- [ ] **Step 4: Start Redis and confirm it responds**

```bash
docker-compose up -d redis
docker-compose exec redis redis-cli ping
```

Expected: `PONG`

- [ ] **Step 5: Stop Redis**

```bash
docker-compose stop redis
```

- [ ] **Step 6: Commit**

```bash
git add docker-compose.yml
git commit -m "feat: add Redis 7 to docker-compose — Streams + Pub/Sub backbone"
```

---

## Checklist

- [x] Service directories scaffolded + .gitignore updated — Task 1 (`6cd55ba`, `bfe4d08`)
- [x] `proto/belgrade_os.proto` with 8 message types + validates with protoc — Task 2 (`33b965d`, `8d69d19`, `fc3bc8d` — also added `trace_id` on Task/ToolCall/ThoughtEvent, `duration_ms` on ToolResult, `UNSPECIFIED` enum sentinel)
- [x] Go codegen + 5 smoke tests passing in `gateway/gen/` — Task 3 (`657c174`, `a457944` — also fixed gitignore nested pattern)
- [x] Python codegen + 6 smoke tests passing in `runner/` and `inference/` — Task 4 (`41615ac`, `2fbc6a5`, `d0e5e9f` — split runtime/dev requirements, conftest path fix, package collision fix)
- [ ] Rust codegen via prost + 4 smoke tests passing in `bridge/` — Task 5
- [ ] Makefile with proto/build/test/dev/clean/deps — Task 6
- [ ] Redis 7 in docker-compose, PONG confirmed — Task 7
