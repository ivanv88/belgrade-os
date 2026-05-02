package main

import (
	"bytes"
	"context"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/google/uuid"
	"google.golang.org/protobuf/proto"

	belgrade "belgrade-os/gateway/gen"
)

// sseRecorder implements http.ResponseWriter + http.Flusher for testing.
type sseRecorder struct {
	header http.Header
	buf    bytes.Buffer
	code   int
}

func (r *sseRecorder) Header() http.Header         { return r.header }
func (r *sseRecorder) Write(b []byte) (int, error) { return r.buf.Write(b) }
func (r *sseRecorder) WriteHeader(code int)        { r.code = code }
func (r *sseRecorder) Flush()                      {}

func TestStreamSSEWritesEventsAndStopsOnDone(t *testing.T) {
	c := requireRedis(t)
	defer c.Close()
	taskID := "sse-stream-" + uuid.NewString()
	rec := &sseRecorder{header: make(http.Header)}

	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	req := httptest.NewRequest(http.MethodPost, "/v1/tasks", nil).WithContext(ctx)

	evtCh, err := c.SubscribeSSE(ctx, taskID)
	if err != nil {
		t.Fatalf("subscribe: %v", err)
	}

	done := make(chan struct{})
	go func() {
		streamSSE(rec, req, evtCh)
		close(done)
	}()

	for _, evt := range []*belgrade.ThoughtEvent{
		{TaskId: taskID, Content: "first chunk", Type: belgrade.ThoughtEventType_RESPONSE_CHUNK},
		{TaskId: taskID, Content: "", Type: belgrade.ThoughtEventType_DONE},
	} {
		data, _ := proto.Marshal(evt)
		c.RDB.Publish(context.Background(), "sse:"+taskID, data)
		time.Sleep(20 * time.Millisecond)
	}

	select {
	case <-done:
	case <-time.After(4 * time.Second):
		t.Fatal("streamSSE did not return after DONE event")
	}

	body := rec.buf.String()
	if !strings.Contains(body, "first chunk") {
		t.Fatalf("expected 'first chunk' in SSE body, got: %s", body)
	}
	if rec.header.Get("Content-Type") != "text/event-stream" {
		t.Fatalf("expected text/event-stream, got %s", rec.header.Get("Content-Type"))
	}
	if rec.code != http.StatusOK {
		t.Fatalf("expected 200, got %d", rec.code)
	}
}

func TestStreamSSECancelContextCleansUp(t *testing.T) {
	c := requireRedis(t)
	defer c.Close()
	taskID := "sse-cancel-" + uuid.NewString()
	rec := &sseRecorder{header: make(http.Header)}

	ctx, cancel := context.WithCancel(context.Background())
	req := httptest.NewRequest(http.MethodPost, "/", nil).WithContext(ctx)

	evtCh, err := c.SubscribeSSE(ctx, taskID)
	if err != nil {
		t.Fatalf("subscribe: %v", err)
	}

	done := make(chan struct{})
	go func() {
		streamSSE(rec, req, evtCh)
		close(done)
	}()

	cancel()

	select {
	case <-done:
	case <-time.After(2 * time.Second):
		t.Fatal("streamSSE goroutine did not exit after context cancellation")
	}
}
