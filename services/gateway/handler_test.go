package main

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"belgrade-os/gateway/auth"
	"belgrade-os/gateway/redis"
)

func newTestHandler(t *testing.T, jwksURL string, rClient *redis.RedisClient) *Handler {
	t.Helper()
	cache := auth.NewTestCache(t, jwksURL)
	return NewHandler(cache, rClient, "test-aud", auth.TrustedSet{})
}

func requireRedis(t *testing.T) *redis.RedisClient {
	t.Helper()
	c, err := redis.NewRedisClient("redis://localhost:6379")
	if err != nil {
		t.Skipf("redis unavailable: %v", err)
	}
	return c
}

func TestCreateTaskReturns202WithTaskID(t *testing.T) {
	key := auth.GenerateTestKey(t)
	kid := "handler-kid-valid"
	srv := auth.ServeJWKS(t, &key.PublicKey, kid)
	defer srv.Close()
	rClient := requireRedis(t)
	defer rClient.Close()

	h := newTestHandler(t, srv.URL, rClient)
	tokenStr := auth.SignToken(t, key, kid, "user-handler-1", "test-aud", time.Now().Add(time.Hour))

	body := `{"prompt":"make a meal plan","stream":false}`
	req := httptest.NewRequest(http.MethodPost, "/v1/tasks", strings.NewReader(body))
	req.Header.Set("Cf-Access-Jwt-Assertion", tokenStr)
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()

	h.CreateTask(w, req)

	if w.Code != http.StatusAccepted {
		t.Fatalf("expected 202, got %d: %s", w.Code, w.Body.String())
	}

	var resp taskResponse
	if err := json.NewDecoder(w.Body).Decode(&resp); err != nil {
		t.Fatalf("decode response: %v", err)
	}
	if resp.TaskID == "" {
		t.Fatal("expected non-empty task_id")
	}
	if resp.TraceID == "" {
		t.Fatal("expected non-empty trace_id")
	}
}

func TestCreateTaskMissingAuthHeader(t *testing.T) {
	cache := auth.NewTestCache(t, "http://localhost:0")
	h := NewHandler(cache, nil, "aud", auth.TrustedSet{})

	req := httptest.NewRequest(http.MethodPost, "/v1/tasks", strings.NewReader(`{"prompt":"hi"}`))
	w := httptest.NewRecorder()
	h.CreateTask(w, req)

	if w.Code != http.StatusUnauthorized {
		t.Fatalf("expected 401, got %d", w.Code)
	}
}

func TestCreateTaskMissingPrompt(t *testing.T) {
	key := auth.GenerateTestKey(t)
	kid := "handler-kid-prompt"
	srv := auth.ServeJWKS(t, &key.PublicKey, kid)
	defer srv.Close()

	cache := auth.NewTestCache(t, srv.URL)
	h := NewHandler(cache, nil, "test-aud", auth.TrustedSet{})
	tokenStr := auth.SignToken(t, key, kid, "user-prompt-test", "test-aud", time.Now().Add(time.Hour))

	req := httptest.NewRequest(http.MethodPost, "/v1/tasks", strings.NewReader(`{}`))
	req.Header.Set("Cf-Access-Jwt-Assertion", tokenStr)
	w := httptest.NewRecorder()
	h.CreateTask(w, req)

	if w.Code != http.StatusBadRequest {
		t.Fatalf("expected 400, got %d", w.Code)
	}
}

func TestCreateTaskInvalidToken(t *testing.T) {
	key := auth.GenerateTestKey(t)
	kid := "handler-kid-invalid"
	srv := auth.ServeJWKS(t, &key.PublicKey, kid)
	defer srv.Close()

	cache := auth.NewTestCache(t, srv.URL)
	h := NewHandler(cache, nil, "test-aud", auth.TrustedSet{})

	req := httptest.NewRequest(http.MethodPost, "/v1/tasks", strings.NewReader(`{"prompt":"hi"}`))
	req.Header.Set("Cf-Access-Jwt-Assertion", "not.a.jwt")
	w := httptest.NewRecorder()
	h.CreateTask(w, req)

	if w.Code != http.StatusUnauthorized {
		t.Fatalf("expected 401, got %d", w.Code)
	}
}

func TestHandlerTrustSetWiring(t *testing.T) {
	trusted := auth.ParseTrustedUsers("alice@example.com")
	h := NewHandler(nil, nil, "test-audience", trusted)
	if !h.trustedUsers.Contains("alice@example.com") {
		t.Fatal("alice should be trusted")
	}
	if h.trustedUsers.Contains("mallory@example.com") {
		t.Fatal("mallory should not be trusted")
	}
}

func TestStreamTaskReturns401WithoutAuth(t *testing.T) {
	// Direct call is valid here: auth check (401) happens before path value extraction.
	cache := auth.NewTestCache(t, "http://localhost:0")
	h := NewHandler(cache, nil, "aud", auth.TrustedSet{})

	req := httptest.NewRequest(http.MethodGet, "/v1/tasks/some-task-id/stream", nil)
	w := httptest.NewRecorder()
	h.StreamTask(w, req)

	if w.Code != http.StatusUnauthorized {
		t.Fatalf("expected 401, got %d", w.Code)
	}
}

func TestStreamTaskPathValuePopulatedByMux(t *testing.T) {
	// r.PathValue("task_id") only works through ServeMux. This test verifies the mux
	// populates the path value: auth passes, task_id is non-empty, next step is
	// SubscribeSSE which fails (nil redis) → 500, NOT 400 (which would mean empty task_id).
	key := auth.GenerateTestKey(t)
	kid := "stream-mux-kid"
	srv := auth.ServeJWKS(t, &key.PublicKey, kid)
	defer srv.Close()

	cache := auth.NewTestCache(t, srv.URL)
	h := NewHandler(cache, nil, "test-aud", auth.TrustedSet{}) // nil redis intentional
	tokenStr := auth.SignToken(t, key, kid, "user-mux", "test-aud", time.Now().Add(time.Hour))

	mux := http.NewServeMux()
	mux.HandleFunc("GET /v1/tasks/{task_id}/stream", h.StreamTask)

	req := httptest.NewRequest(http.MethodGet, "/v1/tasks/my-task-uuid-123/stream", nil)
	req.Header.Set("Cf-Access-Jwt-Assertion", tokenStr)
	w := httptest.NewRecorder()
	mux.ServeHTTP(w, req)

	// 400 would mean task_id was empty (path value not populated by mux) — that's the bug.
	// nil redis → SubscribeSSE fails → 500 means path value WAS populated correctly.
	if w.Code == http.StatusBadRequest {
		t.Fatal("r.PathValue('task_id') not populated by mux — got 400 (empty task_id)")
	}
	if w.Code != http.StatusInternalServerError {
		t.Fatalf("expected 500 (nil redis → subscribe fails), got %d", w.Code)
	}
}
