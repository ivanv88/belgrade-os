package main

import (
	"context"
	"errors"
	"fmt"
	"log"
	"net/http"
	"os"
	"os/signal"
	"syscall"
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
	srv := &http.Server{
		Addr:              addr,
		Handler:           mux,
		ReadHeaderTimeout: 10 * time.Second,
	}

	go func() {
		log.Printf("gateway listening on %s", addr)
		if err := srv.ListenAndServe(); !errors.Is(err, http.ErrServerClosed) {
			log.Fatalf("listen: %v", err)
		}
	}()

	quit := make(chan os.Signal, 1)
	signal.Notify(quit, syscall.SIGINT, syscall.SIGTERM)
	<-quit
	log.Println("shutting down...")

	// Give in-flight handlers (including SSE streams) 30 s to finish cleanly.
	// If they don't, Close() cancels request contexts and forces them out.
	shutCtx, shutCancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer shutCancel()
	if err := srv.Shutdown(shutCtx); err != nil {
		log.Printf("graceful shutdown incomplete: %v — forcing close", err)
		srv.Close()
	}

	// Redis is closed after HTTP is down: no live SSE subscriptions remain.
	if err := rClient.Close(); err != nil {
		log.Printf("redis close: %v", err)
	}
	log.Println("gateway stopped")
}
