package main

import (
	"encoding/json"
	"net/http"
	"time"

	"github.com/google/uuid"

	belgrade "belgrade-os/gateway/gen"
)

type taskRequest struct {
	Prompt string `json:"prompt"`
	AppID  string `json:"app_id"`
	Stream bool   `json:"stream"`
}

type taskResponse struct {
	TaskID  string `json:"task_id"`
	TraceID string `json:"trace_id"`
}

type Handler struct {
	auth     *JWKSCache
	redis    *RedisClient
	audience string
}

func NewHandler(auth *JWKSCache, redis *RedisClient, audience string) *Handler {
	return &Handler{auth: auth, redis: redis, audience: audience}
}

func (h *Handler) CreateTask(w http.ResponseWriter, r *http.Request) {
	var req taskRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		http.Error(w, "invalid JSON", http.StatusBadRequest)
		return
	}

	tokenStr := r.Header.Get("Cf-Access-Jwt-Assertion")
	if tokenStr == "" {
		http.Error(w, "missing Cf-Access-Jwt-Assertion header", http.StatusUnauthorized)
		return
	}

	if req.Prompt == "" {
		http.Error(w, "prompt is required", http.StatusBadRequest)
		return
	}

	claims, err := ValidateToken(tokenStr, h.auth, h.audience)
	if err != nil {
		http.Error(w, "unauthorized", http.StatusUnauthorized)
		return
	}

	taskID := uuid.NewString()
	traceID := uuid.NewString()

	task := &belgrade.Task{
		TaskId:      taskID,
		UserId:      claims.UserID,
		Prompt:      req.Prompt,
		CreatedAtMs: time.Now().UnixMilli(),
		TraceId:     traceID,
	}

	if err := h.redis.PublishTask(r.Context(), task); err != nil {
		http.Error(w, "failed to queue task", http.StatusInternalServerError)
		return
	}

	if req.Stream {
		streamSSE(w, r, h.redis, taskID)
		return
	}

	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusAccepted)
	json.NewEncoder(w).Encode(taskResponse{TaskID: taskID, TraceID: traceID})
}
