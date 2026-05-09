package main

import (
	"encoding/json"
	"net/http"
	"time"

	"github.com/google/uuid"

	"belgrade-os/gateway/auth"
	belgrade "belgrade-os/gateway/gen"
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
}

func NewHandler(jwks *auth.JWKSCache, rClient *redis.RedisClient, audience string, trusted auth.TrustedSet) *Handler {
	return &Handler{auth: jwks, redis: rClient, audience: audience, trustedUsers: trusted}
}

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

	r.Body = http.MaxBytesReader(w, r.Body, 64*1024)
	var req taskRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		http.Error(w, "invalid JSON", http.StatusBadRequest)
		return
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
