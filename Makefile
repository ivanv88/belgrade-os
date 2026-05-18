.PHONY: proto build test dev start stop clean deps

PROTO_SRC := proto/belgrade_os.proto
export PATH := $(PATH):$(shell go env GOPATH)/bin

# ─── Dependencies (macOS) ─────────────────────────────────────────────────────
deps:
	brew install protobuf go rust
	go install google.golang.org/protobuf/cmd/protoc-gen-go@latest
	pip3 install -r sdk/requirements.txt \
	             -r services/runner/requirements-dev.txt \
	             -r services/inference/requirements-dev.txt \
	             -r services/notification/requirements-dev.txt \
	             -r services/vault_service/requirements-dev.txt \
	             -r services/platform_controller/requirements-dev.txt \
	             -r services/mcp_server/requirements.txt

# ─── Proto codegen ────────────────────────────────────────────────────────────
proto: services/gateway/gen/belgrade_os.pb.go services/runner/gen/belgrade_os_pb2.py services/inference/gen/belgrade_os_pb2.py services/notification/gen/belgrade_os_pb2.py sdk/belgrade_sdk/gen/belgrade_os_pb2.py services/vault_service/gen/belgrade_os_pb2.py services/platform_controller/gen/belgrade_os_pb2.py
	@echo "proto codegen complete"

services/gateway/gen/belgrade_os.pb.go: $(PROTO_SRC)
	mkdir -p services/gateway/gen
	protoc -Iproto \
	  --go_out=services/gateway/gen \
	  --go_opt=paths=source_relative \
	  $(PROTO_SRC)

services/runner/gen/belgrade_os_pb2.py: $(PROTO_SRC)
	mkdir -p services/runner/gen
	touch services/runner/gen/__init__.py
	python3 -m grpc_tools.protoc -Iproto --python_out=services/runner/gen $(PROTO_SRC)

services/inference/gen/belgrade_os_pb2.py: $(PROTO_SRC)
	mkdir -p services/inference/gen
	touch services/inference/gen/__init__.py
	python3 -m grpc_tools.protoc -Iproto --python_out=services/inference/gen $(PROTO_SRC)

services/notification/gen/belgrade_os_pb2.py: $(PROTO_SRC)
	mkdir -p services/notification/gen
	touch services/notification/gen/__init__.py
	python3 -m grpc_tools.protoc -Iproto --python_out=services/notification/gen $(PROTO_SRC)

sdk/belgrade_sdk/gen/belgrade_os_pb2.py: $(PROTO_SRC)
	mkdir -p sdk/belgrade_sdk/gen
	touch sdk/belgrade_sdk/gen/__init__.py
	python3 -m grpc_tools.protoc -Iproto --python_out=sdk/belgrade_sdk/gen $(PROTO_SRC)

services/vault_service/gen/belgrade_os_pb2.py: $(PROTO_SRC)
	mkdir -p services/vault_service/gen
	touch services/vault_service/gen/__init__.py
	python3 -m grpc_tools.protoc -Iproto --python_out=services/vault_service/gen $(PROTO_SRC)

services/platform_controller/gen/belgrade_os_pb2.py: $(PROTO_SRC)
	mkdir -p services/platform_controller/gen
	touch services/platform_controller/gen/__init__.py
	python3 -m grpc_tools.protoc -Iproto --python_out=services/platform_controller/gen $(PROTO_SRC)

# Rust codegen runs via services/bridge/build.rs — no explicit Make target needed.

# ─── Build ────────────────────────────────────────────────────────────────────
build: proto
	cd services/gateway && go build ./...
	cd services/bridge && cargo build --release

# ─── Test ─────────────────────────────────────────────────────────────────────
test: proto
	cd services/gateway && go test ./... -v
	cd services/runner && python3 -m pytest tests/ -v
	cd services/inference && python3 -m pytest tests/ -v
	cd services/notification && python3 -m pytest tests/ -v
	cd services/vault_service && python3 -m pytest tests/ -v
	cd services/platform_controller && python3 -m pytest tests/ -v
	cd services/mcp_server && python3 -m pytest tests/ -v
	cd services/watchdog && python3 -m pytest tests/ -v
	cd services/bridge && cargo test

# ─── Dev infrastructure ───────────────────────────────────────────────────────
dev:
	docker-compose up -d redis db docker-socket-proxy tunnel

start: dev
	./scripts/start.sh

stop:
	./scripts/stop.sh
	docker-compose down

# ─── Clean generated artifacts ────────────────────────────────────────────────
clean:
	rm -f services/gateway/gen/belgrade_os.pb.go
	rm -f services/runner/gen/belgrade_os_pb2.py services/runner/gen/belgrade_os_pb2_grpc.py
	rm -f services/inference/gen/belgrade_os_pb2.py services/inference/gen/belgrade_os_pb2_grpc.py
	rm -f services/notification/gen/belgrade_os_pb2.py services/notification/gen/belgrade_os_pb2_grpc.py
	rm -f sdk/belgrade_sdk/gen/belgrade_os_pb2.py sdk/belgrade_sdk/gen/belgrade_os_pb2_grpc.py
	rm -f services/vault_service/gen/belgrade_os_pb2.py
	rm -f services/platform_controller/gen/belgrade_os_pb2.py
	cd services/bridge && cargo clean
