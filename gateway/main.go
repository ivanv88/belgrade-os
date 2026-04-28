package main

import (
	"fmt"
	"log"
	"net/http"
)

func main() {
	cfg := LoadConfig()

	cache := NewJWKSCache(cfg.CFTeamDomain)

	rClient, err := NewRedisClient(cfg.RedisURL)
	if err != nil {
		log.Fatalf("redis connect: %v", err)
	}

	h := NewHandler(cache, rClient, cfg.CFAudience)

	mux := http.NewServeMux()
	mux.HandleFunc("POST /v1/tasks", h.CreateTask)

	addr := fmt.Sprintf(":%s", cfg.Port)
	log.Printf("gateway listening on %s", addr)
	log.Fatal(http.ListenAndServe(addr, mux))
}
