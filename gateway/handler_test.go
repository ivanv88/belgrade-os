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
	return NewHandler(cache, rClient, "test-aud")
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
	h := NewHandler(cache, nil, "aud")

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
	h := NewHandler(cache, nil, "test-aud")
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
	h := NewHandler(cache, nil, "test-aud")

	req := httptest.NewRequest(http.MethodPost, "/v1/tasks", strings.NewReader(`{"prompt":"hi"}`))
	req.Header.Set("Cf-Access-Jwt-Assertion", "not.a.jwt")
	w := httptest.NewRecorder()
	h.CreateTask(w, req)

	if w.Code != http.StatusUnauthorized {
		t.Fatalf("expected 401, got %d", w.Code)
	}
}
