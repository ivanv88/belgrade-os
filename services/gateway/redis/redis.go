package redis

import (
	"context"
	"fmt"

	goredis "github.com/redis/go-redis/v9"
	"google.golang.org/protobuf/proto"

	belgrade "belgrade-os/gateway/gen"
)

type RedisClient struct {
	RDB *goredis.Client
}

func NewRedisClient(url string) (*RedisClient, error) {
	opts, err := goredis.ParseURL(url)
	if err != nil {
		return nil, fmt.Errorf("parse redis URL: %w", err)
	}
	return &RedisClient{RDB: goredis.NewClient(opts)}, nil
}

func (c *RedisClient) Ping(ctx context.Context) error {
	return c.RDB.Ping(ctx).Err()
}

func (c *RedisClient) Close() error {
	return c.RDB.Close()
}

func (c *RedisClient) PublishTask(ctx context.Context, task *belgrade.Task) error {
	data, err := proto.Marshal(task)
	if err != nil {
		return fmt.Errorf("marshal task: %w", err)
	}
	return c.RDB.XAdd(ctx, &goredis.XAddArgs{
		Stream: "tasks:inbound",
		MaxLen: 1000,
		Approx: true,
		Values: map[string]interface{}{"data": data},
	}).Err()
}

func (c *RedisClient) GetPermission(ctx context.Context, userID, appID, bundleID string) (string, error) {
	key := fmt.Sprintf("perms:%s", userID)
	field := fmt.Sprintf("%s:%s", appID, bundleID)
	return c.RDB.HGet(ctx, key, field).Result()
}

// SubscribeSSE subscribes to sse:{taskID} and emits decoded ThoughtEvents.
func (c *RedisClient) SubscribeSSE(ctx context.Context, taskID string) (<-chan *belgrade.ThoughtEvent, error) {
	ch := make(chan *belgrade.ThoughtEvent, 16)
	pubsub := c.RDB.Subscribe(ctx, fmt.Sprintf("sse:%s", taskID))

	if _, err := pubsub.Receive(ctx); err != nil {
		pubsub.Close()
		return nil, fmt.Errorf("subscribe sse:%s: %w", taskID, err)
	}

	go func() {
		defer close(ch)
		defer pubsub.Close()
		msgCh := pubsub.Channel()
		for {
			select {
			case <-ctx.Done():
				return
			case msg, ok := <-msgCh:
				if !ok {
					return
				}
				var evt belgrade.ThoughtEvent
				if err := proto.Unmarshal([]byte(msg.Payload), &evt); err != nil {
					continue
				}
				select {
				case ch <- &evt:
				case <-ctx.Done():
					return
				}
			}
		}
	}()
	return ch, nil
}
