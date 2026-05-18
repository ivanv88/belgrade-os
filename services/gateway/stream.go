package main

import (
	"encoding/json"
	"fmt"
	"net/http"

	belgrade "belgrade-os/gateway/gen"
)

type sseData struct {
	Type    int32  `json:"type"`
	Content string `json:"content"`
	TaskID  string `json:"task_id"`
	TraceID string `json:"trace_id"`
}

// streamSSE forwards ThoughtEvents from evtCh as SSE.
// evtCh must be subscribed before the task is published to avoid losing early events.
// Returns when a DONE/ERROR event is received or ctx is cancelled.
func streamSSE(w http.ResponseWriter, r *http.Request, evtCh <-chan *belgrade.ThoughtEvent) {
	flusher, ok := w.(http.Flusher)
	if !ok {
		http.Error(w, "streaming not supported", http.StatusInternalServerError)
		return
	}

	w.Header().Set("Content-Type", "text/event-stream")
	w.Header().Set("Cache-Control", "no-cache")
	w.Header().Set("Connection", "keep-alive")
	w.WriteHeader(http.StatusOK)

	for evt := range evtCh {
		payload, _ := json.Marshal(sseData{
			Type:    int32(evt.Type),
			Content: evt.Content,
			TaskID:  evt.TaskId,
			TraceID: evt.TraceId,
		})
		fmt.Fprintf(w, "data: %s\n\n", payload)
		flusher.Flush()

		if evt.Type == belgrade.ThoughtEventType_DONE || evt.Type == belgrade.ThoughtEventType_ERROR {
			return
		}
	}
}
