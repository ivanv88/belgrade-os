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

	"belgrade-os/gateway/appproxy"
	"belgrade-os/gateway/auth"
	"belgrade-os/gateway/redis"
	"belgrade-os/gateway/ui"
)

func main() {
	cfg := LoadConfig()

	cache := auth.NewJWKSCache(cfg.CFTeamDomain)

	rClient, err := redis.NewRedisClient(cfg.RedisURL)
	if err != nil {
		log.Fatalf("redis connect: %v", err)
	}
	pingCtx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	if err := rClient.Ping(pingCtx); err != nil {
		log.Fatalf("redis ping: %v", err)
	}

	h := NewHandler(cache, rClient, cfg.CFAudience, auth.LoadTrustedUsers())
	uiH := ui.NewHandler(cfg.AppsRoot, rClient, cfg.GatewayURL)

	mux := http.NewServeMux()
	// Legacy/internal: direct inference submission. Not the recommended client path.
	// Apps use ctx.inference.request() via the SDK. Retained for dev tooling + Admin App.
	mux.HandleFunc("POST /v1/tasks", h.CreateTask)
	// Subscribe-only SSE for app-owned inference tasks. task_id returned by ctx.inference.request().
	mux.HandleFunc("GET /v1/tasks/{task_id}/stream", h.StreamTask)

	// UI Module Routes
	uiMiddleware := ui.AuthMiddleware(cache, cfg.CFAudience)
	mux.Handle("GET /ui/", uiMiddleware(http.HandlerFunc(uiH.ServeAsset)))

	// Direct app action routes — auth-gated, RBAC-enforced, no inference stream.
	proxyH := appproxy.NewHandler(cfg.BridgeURL, rClient)
	mux.Handle("GET /api/", uiMiddleware(http.HandlerFunc(proxyH.ServeAPI)))
	mux.Handle("POST /api/", uiMiddleware(http.HandlerFunc(proxyH.ServeAPI)))
	mux.Handle("PUT /api/", uiMiddleware(http.HandlerFunc(proxyH.ServeAPI)))
	mux.Handle("DELETE /api/", uiMiddleware(http.HandlerFunc(proxyH.ServeAPI)))
	mux.Handle("PATCH /api/", uiMiddleware(http.HandlerFunc(proxyH.ServeAPI)))

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
