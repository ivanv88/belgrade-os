.PHONY: proto build test dev clean deps

PROTO_SRC := proto/belgrade_os.proto
export PATH := $(PATH):$(shell go env GOPATH)/bin

# ─── Dependencies (macOS) ─────────────────────────────────────────────────────
deps:
	brew install protobuf go rust
	go install google.golang.org/protobuf/cmd/protoc-gen-go@latest
	pip3 install -r runner/requirements-dev.txt -r inference/requirements-dev.txt

# ─── Proto codegen ────────────────────────────────────────────────────────────
proto: gateway/gen/belgrade_os.pb.go runner/gen/belgrade_os_pb2.py inference/gen/belgrade_os_pb2.py notification/gen/belgrade_os_pb2.py sdk/belgrade_sdk/gen/belgrade_os_pb2.py vault_service/gen/belgrade_os_pb2.py
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
	python3 -m grpc_tools.protoc -Iproto --python_out=runner/gen $(PROTO_SRC)

inference/gen/belgrade_os_pb2.py: $(PROTO_SRC)
	mkdir -p inference/gen
	touch inference/gen/__init__.py
	python3 -m grpc_tools.protoc -Iproto --python_out=inference/gen $(PROTO_SRC)

notification/gen/belgrade_os_pb2.py: $(PROTO_SRC)
	mkdir -p notification/gen
	touch notification/gen/__init__.py
	python3 -m grpc_tools.protoc -Iproto --python_out=notification/gen $(PROTO_SRC)

sdk/belgrade_sdk/gen/belgrade_os_pb2.py: $(PROTO_SRC)
	mkdir -p sdk/belgrade_sdk/gen
	touch sdk/belgrade_sdk/gen/__init__.py
	python3 -m grpc_tools.protoc -Iproto --python_out=sdk/belgrade_sdk/gen $(PROTO_SRC)

vault_service/gen/belgrade_os_pb2.py: $(PROTO_SRC)
	mkdir -p vault_service/gen
	touch vault_service/gen/__init__.py
	python3 -m grpc_tools.protoc -Iproto --python_out=vault_service/gen $(PROTO_SRC)

# Rust codegen runs via bridge/build.rs — no explicit Make target needed.

# ─── Build ────────────────────────────────────────────────────────────────────
build: proto
	cd gateway && go build ./...
	cd bridge && cargo build --release

# ─── Test ─────────────────────────────────────────────────────────────────────
test: proto
	cd gateway && go test ./... -v
	cd runner && python3 -m pytest tests/ -v
	cd inference && python3 -m pytest tests/ -v
	cd notification && python3 -m pytest tests/ -v
	cd bridge && cargo test

# ─── Dev infrastructure ───────────────────────────────────────────────────────
dev:
	docker-compose up -d redis tunnel

# ─── Clean generated artifacts ────────────────────────────────────────────────
clean:
	rm -f gateway/gen/belgrade_os.pb.go
	rm -f runner/gen/belgrade_os_pb2.py runner/gen/belgrade_os_pb2_grpc.py
	rm -f inference/gen/belgrade_os_pb2.py inference/gen/belgrade_os_pb2_grpc.py
	rm -f notification/gen/belgrade_os_pb2.py notification/gen/belgrade_os_pb2_grpc.py
	rm -f sdk/belgrade_sdk/gen/belgrade_os_pb2.py sdk/belgrade_sdk/gen/belgrade_os_pb2_grpc.py
	rm -f vault_service/gen/belgrade_os_pb2.py
	cd bridge && cargo clean
