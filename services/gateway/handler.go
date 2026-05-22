package main

import (
	"bytes"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httputil"
	"net/url"
	"strings"
	"time"

	"github.com/google/uuid"

	"belgrade-os/gateway/auth"
	belgrade "belgrade-os/gateway/gen"
	"belgrade-os/gateway/manifest"
	"belgrade-os/gateway/redis"
)

type taskRequest struct {
	Prompt        string `json:"prompt"`
	AppID         string `json:"app_id"`
	Stream        bool   `json:"stream"`
	ExecutionMode string `json:"execution_mode"`
}

type taskResponse struct {
	TaskID  string `json:"task_id"`
	TraceID string `json:"trace_id"`
}

type Handler struct {
	auth         *auth.JWKSCache
	redis        *redis.RedisClient
	audience     string
	trustedUsers auth.TrustedSet
	appsRoot     string
}

func NewHandler(jwks *auth.JWKSCache, rClient *redis.RedisClient, audience string, trusted auth.TrustedSet, appsRoot string) *Handler {
	return &Handler{auth: jwks, redis: rClient, audience: audience, trustedUsers: trusted, appsRoot: appsRoot}
}

// CreateTask is a legacy/internal endpoint for direct inference submission.
// It is NOT the recommended integration path. Apps should use
// ctx.inference.request() from the Belgrade SDK instead.
// Retained for backward compatibility, developer tooling, and future Admin App use.
func (h *Handler) CreateTask(w http.ResponseWriter, r *http.Request) {
	tokenStr := r.Header.Get("Cf-Access-Jwt-Assertion")
	if tokenStr == "" {
		http.Error(w, "missing Cf-Access-Jwt-Assertion header", http.StatusUnauthorized)
		return
	}

	claims, err := auth.ValidateToken(tokenStr, h.auth, h.audience)
	if err != nil {
		http.Error(w, "unauthorized", http.StatusUnauthorized)
		return
	}

	bodyBytes, err := io.ReadAll(http.MaxBytesReader(w, r.Body, 64*1024))
	if err != nil {
		http.Error(w, "request body too large or unreadable", http.StatusBadRequest)
		return
	}
	var req taskRequest
	if err := json.Unmarshal(bodyBytes, &req); err != nil {
		http.Error(w, "invalid JSON", http.StatusBadRequest)
		return
	}

	if req.AppID != "" && h.appsRoot != "" {
		if m, err := manifest.Load(h.appsRoot, req.AppID); err == nil && m.Runtime == manifest.RuntimeContainer {
			if m.Endpoint == "" {
				http.Error(w, "container endpoint not configured", http.StatusBadGateway)
				return
			}
			h.proxyContainerTask(w, r, m.Endpoint, claims.UserID, bodyBytes)
			return
		}
	}

	if req.Prompt == "" {
		http.Error(w, "prompt is required", http.StatusBadRequest)
		return
	}

	var execMode belgrade.ExecutionMode
	if h.trustedUsers.Contains(claims.UserID) {
		execMode = belgrade.ExecutionMode_TRUSTED
	} else {
		execMode = belgrade.ExecutionMode_UNTRUSTED
	}

	taskID := uuid.NewString()
	traceID := uuid.NewString()

	task := &belgrade.Task{
		TaskId:        taskID,
		UserId:        claims.UserID,
		Prompt:        req.Prompt,
		CreatedAtMs:   time.Now().UnixMilli(),
		TraceId:       traceID,
		ExecutionMode: execMode,
	}

	// Subscribe before publish so no ThoughtEvents are missed on the streaming path.
	var evtCh <-chan *belgrade.ThoughtEvent
	if req.Stream {
		evtCh, err = h.redis.SubscribeSSE(r.Context(), taskID)
		if err != nil {
			http.Error(w, "failed to set up stream", http.StatusInternalServerError)
			return
		}
	}

	if err := h.redis.PublishTask(r.Context(), task); err != nil {
		http.Error(w, "failed to queue task", http.StatusInternalServerError)
		return
	}

	if req.Stream {
		streamSSE(w, r, evtCh)
		return
	}

	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusAccepted)
	json.NewEncoder(w).Encode(taskResponse{TaskID: taskID, TraceID: traceID})
}

func (h *Handler) proxyContainerTask(w http.ResponseWriter, r *http.Request, endpoint, userID string, body []byte) {
	target := strings.TrimRight(endpoint, "/") + "/v1/tasks"
	targetURL, err := url.Parse(target)
	if err != nil {
		http.Error(w, "invalid container endpoint", http.StatusBadGateway)
		return
	}
	proxy := &httputil.ReverseProxy{
		Director: func(req *http.Request) {
			req.URL = targetURL
			req.Host = targetURL.Host
			req.Body = io.NopCloser(bytes.NewReader(body))
			req.ContentLength = int64(len(body))
			req.Header.Set("X-User-ID", userID)
			req.Header.Del("Cf-Access-Jwt-Assertion")
			req.Header.Del("Cookie")
			req.Header.Del("Authorization")
		},
		FlushInterval: -1,
		ErrorHandler: func(w http.ResponseWriter, r *http.Request, err error) {
			http.Error(w, "container unavailable", http.StatusBadGateway)
		},
	}
	proxy.ServeHTTP(w, r)
}

// StreamTask is the subscribe-only SSE endpoint for app-owned inference tasks.
// The client passes a task_id previously returned by ctx.inference.request().
// Any authenticated user who holds the task_id UUID may subscribe — the UUID itself
// acts as a capability token (128-bit entropy, not guessable).
func (h *Handler) StreamTask(w http.ResponseWriter, r *http.Request) {
	tokenStr := r.Header.Get("Cf-Access-Jwt-Assertion")
	if tokenStr == "" {
		if cookie, err := r.Cookie("CF_Authorization"); err == nil {
			tokenStr = cookie.Value
		}
	}
	if tokenStr == "" {
		http.Error(w, "missing authentication", http.StatusUnauthorized)
		return
	}
	if _, err := auth.ValidateToken(tokenStr, h.auth, h.audience); err != nil {
		http.Error(w, "unauthorized", http.StatusUnauthorized)
		return
	}

	taskID := r.PathValue("task_id")
	if taskID == "" {
		http.Error(w, "missing task_id", http.StatusBadRequest)
		return
	}

	if h.redis == nil {
		http.Error(w, "failed to set up stream", http.StatusInternalServerError)
		return
	}
	evtCh, err := h.redis.SubscribeSSE(r.Context(), taskID)
	if err != nil {
		http.Error(w, "failed to set up stream", http.StatusInternalServerError)
		return
	}
	streamSSE(w, r, evtCh)
}
