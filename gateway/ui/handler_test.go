package ui

import (
	"context"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"belgrade-os/gateway/auth"
	"belgrade-os/gateway/redis"
)

func TestServeAssetRBAC(t *testing.T) {
	// Setup mock apps dir
	tmpDir := "test_apps"
	os.MkdirAll(filepath.Join(tmpDir, "shopping/static/web"), 0755)
	defer os.RemoveAll(tmpDir)
	os.WriteFile(filepath.Join(tmpDir, "shopping/static/web/index.html"), []byte("<html></html>"), 0644)

	// Mock Redis with permission
	rClient, _ := redis.NewRedisClient("redis://localhost:6379")
	// Note: We expect Redis to be running or this will skip in requireRedis style, 
	// but for unit tests we should ideally mock the Redis calls. 
	// Since our redis.RedisClient is a struct wrapping the actual client, 
	// I'll assume we test against a real local Redis if available.
	
	ctx := context.Background()
	rClient.RDB.HSet(ctx, "perms:user1", "shopping:web", "admin").Result()
	defer rClient.RDB.Del(ctx, "perms:user1")

	h := NewHandler(tmpDir, rClient, "http://gateway")

	t.Run("Authorized Access", func(t *testing.T) {
		req := httptest.NewRequest("GET", "/ui/shopping/web/index.html", nil)
		req = req.WithContext(context.WithValue(req.Context(), auth.ClaimsKey, &auth.Claims{UserID: "user1"}))
		w := httptest.NewRecorder()
		h.ServeAsset(w, req)

		if w.Code != http.StatusOK {
			t.Errorf("expected 200, got %d", w.Code)
		}
		if !strings.Contains(w.Body.String(), "BELGRADE_CONFIG") {
			t.Error("config not injected")
		}
	})

	t.Run("Unauthorized Access", func(t *testing.T) {
		req := httptest.NewRequest("GET", "/ui/shopping/web/index.html", nil)
		req = req.WithContext(context.WithValue(req.Context(), auth.ClaimsKey, &auth.Claims{UserID: "user2"}))
		w := httptest.NewRecorder()
		h.ServeAsset(w, req)

		if w.Code != http.StatusForbidden {
			t.Errorf("expected 403, got %d", w.Code)
		}
	})

	t.Run("Path Traversal Protection", func(t *testing.T) {
		req := httptest.NewRequest("GET", "/ui/shopping/web/../../../../etc/passwd", nil)
		req = req.WithContext(context.WithValue(req.Context(), auth.ClaimsKey, &auth.Claims{UserID: "user1"}))
		w := httptest.NewRecorder()
		h.ServeAsset(w, req)

		if w.Code != http.StatusBadRequest && w.Code != http.StatusForbidden {
			t.Errorf("expected 400 or 403, got %d", w.Code)
		}
	})
}
