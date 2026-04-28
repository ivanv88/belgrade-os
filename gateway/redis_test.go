package main

import (
	"context"
	"testing"
	"time"

	"google.golang.org/protobuf/proto"

	belgrade "belgrade-os/gateway/gen"
)

func requireRedis(t *testing.T) *RedisClient {
	t.Helper()
	c, err := NewRedisClient("redis://localhost:6379")
	if err != nil {
		t.Skipf("redis unavailable: %v", err)
	}
	ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
	defer cancel()
	if err := c.rdb.Ping(ctx).Err(); err != nil {
		c.rdb.Close()
		t.Skipf("redis ping failed: %v", err)
	}
	t.Cleanup(func() { c.rdb.Close() })
	return c
}

func TestPublishTask(t *testing.T) {
	c := requireRedis(t)
	ctx := context.Background()

	task := &belgrade.Task{
		TaskId:      "redis-test-task-1",
		UserId:      "user-1",
		Prompt:      "hello redis",
		CreatedAtMs: 1700000000000,
		TraceId:     "trace-redis-1",
	}
	if err := c.PublishTask(ctx, task); err != nil {
		t.Fatalf("publish: %v", err)
	}

	msgs, err := c.rdb.XRevRangeN(ctx, "tasks:inbound", "+", "-", 1).Result()
	if err != nil || len(msgs) == 0 {
		t.Fatalf("read stream: err=%v, len=%d", err, len(msgs))
	}

	raw, ok := msgs[0].Values["data"].(string)
	if !ok {
		t.Fatalf("expected string value for 'data', got %T", msgs[0].Values["data"])
	}
	var got belgrade.Task
	if err := proto.Unmarshal([]byte(raw), &got); err != nil {
		t.Fatalf("unmarshal: %v", err)
	}
	if got.TaskId != "redis-test-task-1" {
		t.Fatalf("expected redis-test-task-1, got %s", got.TaskId)
	}
	if got.TraceId != "trace-redis-1" {
		t.Fatalf("expected trace-redis-1, got %s", got.TraceId)
	}
}

func TestSubscribeSSEReceivesEvent(t *testing.T) {
	c := requireRedis(t)
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()

	taskID := "sub-test-task-redis"
	evtCh, err := c.SubscribeSSE(ctx, taskID)
	if err != nil {
		t.Fatalf("subscribe: %v", err)
	}

	sent := &belgrade.ThoughtEvent{
		TaskId:  taskID,
		UserId:  "user-1",
		Type:    belgrade.ThoughtEventType_RESPONSE_CHUNK,
		Content: "hello from redis",
		TraceId: "trace-sub-1",
	}
	data, _ := proto.Marshal(sent)
	c.rdb.Publish(ctx, "sse:"+taskID, data)

	select {
	case got := <-evtCh:
		if got.Content != "hello from redis" {
			t.Fatalf("expected 'hello from redis', got %q", got.Content)
		}
		if got.TraceId != "trace-sub-1" {
			t.Fatalf("expected trace-sub-1, got %s", got.TraceId)
		}
	case <-time.After(3 * time.Second):
		t.Fatal("timeout waiting for SSE event from Redis Pub/Sub")
	}
}
