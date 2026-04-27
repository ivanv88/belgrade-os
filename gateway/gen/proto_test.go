package belgrade_os_test

import (
	"testing"

	belgrade "belgrade-os/gateway/gen"
)

func TestTaskMessage(t *testing.T) {
	task := &belgrade.Task{
		TaskId:      "task-001",
		UserId:      "user-1",
		Prompt:      "What's for dinner?",
		CreatedAtMs: 1_700_000_000_000,
		TraceId:     "trace-abc",
	}
	if task.GetTaskId() != "task-001" {
		t.Fatalf("expected task-001, got %q", task.GetTaskId())
	}
	if task.GetTraceId() != "trace-abc" {
		t.Fatalf("expected trace-abc, got %q", task.GetTraceId())
	}
}

func TestToolCallMessage(t *testing.T) {
	call := &belgrade.ToolCall{
		CallId:    "call-001",
		TaskId:    "task-001",
		ToolName:  "shopping:add_item",
		InputJson: `{"item": "milk", "qty": 2}`,
		TraceId:   "trace-abc",
	}
	if call.GetToolName() != "shopping:add_item" {
		t.Fatalf("expected shopping:add_item, got %q", call.GetToolName())
	}
	if call.GetTraceId() != "trace-abc" {
		t.Fatalf("expected trace-abc, got %q", call.GetTraceId())
	}
}

func TestToolResultFailure(t *testing.T) {
	result := &belgrade.ToolResult{
		CallId:     "call-001",
		TaskId:     "task-001",
		Success:    false,
		Error:      "app crashed",
		DurationMs: 42,
	}
	if result.GetSuccess() {
		t.Fatal("expected success=false")
	}
	if result.GetError() != "app crashed" {
		t.Fatalf("unexpected error: %q", result.GetError())
	}
	if result.GetDurationMs() != 42 {
		t.Fatalf("expected duration 42, got %d", result.GetDurationMs())
	}
}

func TestThoughtEventType(t *testing.T) {
	ev := &belgrade.ThoughtEvent{
		TaskId:  "task-001",
		UserId:  "user-1",
		Type:    belgrade.ThoughtEventType_RESPONSE_CHUNK,
		Content: "pasta is great",
		TraceId: "trace-abc",
	}
	if ev.GetType() != belgrade.ThoughtEventType_RESPONSE_CHUNK {
		t.Fatalf("expected RESPONSE_CHUNK, got %v", ev.GetType())
	}
	if ev.GetTraceId() != "trace-abc" {
		t.Fatalf("expected trace-abc, got %q", ev.GetTraceId())
	}
}

func TestThoughtEventDone(t *testing.T) {
	ev := &belgrade.ThoughtEvent{
		Type: belgrade.ThoughtEventType_DONE,
	}
	if ev.GetType() != belgrade.ThoughtEventType_DONE {
		t.Fatalf("expected DONE, got %v", ev.GetType())
	}
}
