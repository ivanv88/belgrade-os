.PHONY: proto build test dev clean deps

PROTO_SRC := proto/belgrade_os.proto
export PATH := $(PATH):$(shell go env GOPATH)/bin

# ─── Dependencies (macOS) ─────────────────────────────────────────────────────
deps:
	brew install protobuf go rust
	go install google.golang.org/protobuf/cmd/protoc-gen-go@latest
	pip3 install "grpcio-tools==1.64.1" "protobuf==4.25.3" "pytest==8.2.2"

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
	python3 -m grpc_tools.protoc -Iproto --python_out=runner/gen $(PROTO_SRC)

inference/gen/belgrade_os_pb2.py: $(PROTO_SRC)
	mkdir -p inference/gen
	touch inference/gen/__init__.py
	python3 -m grpc_tools.protoc -Iproto --python_out=inference/gen $(PROTO_SRC)

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
	cd bridge && cargo test

# ─── Dev infrastructure ───────────────────────────────────────────────────────
dev:
	docker-compose up -d redis cloudflared

# ─── Clean generated artifacts ────────────────────────────────────────────────
clean:
	rm -f gateway/gen/belgrade_os.pb.go
	rm -f runner/gen/belgrade_os_pb2.py runner/gen/belgrade_os_pb2_grpc.py
	rm -f inference/gen/belgrade_os_pb2.py inference/gen/belgrade_os_pb2_grpc.py
	cd bridge && cargo clean
