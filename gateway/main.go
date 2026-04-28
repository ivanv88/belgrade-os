package main

import (
	"context"
	"fmt"
	"log"
	"net/http"
	"time"
)

func main() {
	cfg := LoadConfig()

	cache := NewJWKSCache(cfg.CFTeamDomain)

	rClient, err := NewRedisClient(cfg.RedisURL)
	if err != nil {
		log.Fatalf("redis connect: %v", err)
	}
	pingCtx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	if err := rClient.Ping(pingCtx); err != nil {
		log.Fatalf("redis ping: %v", err)
	}

	h := NewHandler(cache, rClient, cfg.CFAudience)

	mux := http.NewServeMux()
	mux.HandleFunc("POST /v1/tasks", h.CreateTask)

	addr := fmt.Sprintf(":%s", cfg.Port)
	log.Printf("gateway listening on %s", addr)
	srv := &http.Server{
		Addr:              addr,
		Handler:           mux,
		ReadHeaderTimeout: 10 * time.Second,
	}
	log.Fatal(srv.ListenAndServe())
}
