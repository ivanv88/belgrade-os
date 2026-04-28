package main

import (
	"bytes"
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"google.golang.org/protobuf/proto"

	belgrade "belgrade-os/gateway/gen"
)

func newTestServer(t *testing.T, jwksURL string, rClient *RedisClient) *httptest.Server {
	t.Helper()
	cache := newTestCache(t, jwksURL)
	h := NewHandler(cache, rClient, "e2e-aud")
	mux := http.NewServeMux()
	mux.HandleFunc("POST /v1/tasks", h.CreateTask)
	return httptest.NewServer(mux)
}

func TestEndToEndPostTask(t *testing.T) {
	key := generateTestKey(t)
	kid := "e2e-kid"
	jwksSrv := serveJWKS(t, &key.PublicKey, kid)
	defer jwksSrv.Close()

	rClient := requireRedis(t)
	srv := newTestServer(t, jwksSrv.URL, rClient)
	defer srv.Close()

	tokenStr := signToken(t, key, kid, "user-e2e", "e2e-aud", time.Now().Add(time.Hour))
	body := `{"prompt":"what should I eat today?","stream":false}`

	req, _ := http.NewRequest(http.MethodPost, srv.URL+"/v1/tasks", bytes.NewBufferString(body))
	req.Header.Set("Cf-Access-Jwt-Assertion", tokenStr)
	req.Header.Set("Content-Type", "application/json")

	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatalf("request failed: %v", err)
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusAccepted {
		t.Fatalf("expected 202, got %d", resp.StatusCode)
	}

	var taskResp taskResponse
	if err := json.NewDecoder(resp.Body).Decode(&taskResp); err != nil {
		t.Fatalf("decode response: %v", err)
	}
	if taskResp.TaskID == "" {
		t.Fatal("expected non-empty task_id")
	}

	// Verify the task was written to Redis stream and is decodable
	ctx := context.Background()
	msgs, err := rClient.rdb.XRevRangeN(ctx, "tasks:inbound", "+", "-", 1).Result()
	if err != nil || len(msgs) == 0 {
		t.Fatalf("read stream: err=%v, len=%d", err, len(msgs))
	}

	raw := msgs[0].Values["data"].(string)
	var task belgrade.Task
	if err := proto.Unmarshal([]byte(raw), &task); err != nil {
		t.Fatalf("unmarshal task: %v", err)
	}
	if task.UserId != "user-e2e" {
		t.Fatalf("expected user-e2e, got %s", task.UserId)
	}
	if !strings.Contains(task.Prompt, "eat today") {
		t.Fatalf("unexpected prompt: %s", task.Prompt)
	}
	if task.TraceId == "" {
		t.Fatal("expected non-empty trace_id on task")
	}
}
