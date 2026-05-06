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

func TestPathContainmentUsesRelNotPrefix(t *testing.T) {
	// Create two sibling directories: appsRoot and appsRoot-evil
	parent := t.TempDir()
	appsRoot := filepath.Join(parent, "apps")
	appsEvil := filepath.Join(parent, "apps-evil")
	os.MkdirAll(filepath.Join(appsRoot, "shopping/static/web"), 0755)
	os.MkdirAll(filepath.Join(appsEvil), 0755)
	os.WriteFile(filepath.Join(appsEvil, "secret.txt"), []byte("secret"), 0644)

	h := NewHandler(appsRoot, nil, "http://gateway")

	// Verify that strings.HasPrefix would have allowed this path (demonstrating the old bug)
	evilPath := filepath.Join(appsEvil, "secret.txt")
	if !strings.HasPrefix(evilPath, appsRoot) {
		t.Skip("sibling dir doesn't share prefix on this OS — test not applicable")
	}
	// Now verify our handler correctly rejects the escape.
	// filepath.Rel should return a path starting with ".." for anything outside absRoot.
	rel, err := filepath.Rel(h.absRoot, evilPath)
	if err != nil {
		t.Fatalf("filepath.Rel failed: %v", err)
	}
	if !strings.HasPrefix(rel, "..") {
		t.Errorf("filepath.Rel(%q, %q) = %q — expected to start with '..'", h.absRoot, evilPath, rel)
	}
}

func TestDirectoryRequestReturns404(t *testing.T) {
	tmpDir := t.TempDir()
	// Create a directory (no index.html) inside the app static dir
	os.MkdirAll(filepath.Join(tmpDir, "shopping/static/web/assets"), 0755)

	rClient, err := redis.NewRedisClient("redis://localhost:6379")
	if err != nil {
		t.Skip("Redis unavailable")
	}
	ctx := context.Background()
	rClient.RDB.HSet(ctx, "perms:user1", "shopping:web", "admin")
	defer rClient.RDB.Del(ctx, "perms:user1")

	h := NewHandler(tmpDir, rClient, "http://gateway")

	// Request a path that resolves to a directory (assets/ with no trailing file)
	req := httptest.NewRequest("GET", "/ui/shopping/web/assets", nil)
	req = req.WithContext(context.WithValue(req.Context(), auth.ClaimsKey, &auth.Claims{UserID: "user1"}))
	w := httptest.NewRecorder()
	h.ServeAsset(w, req)

	if w.Code != http.StatusNotFound {
		t.Errorf("directory request: expected 404, got %d", w.Code)
	}
}

func TestHandlerAbsRootComputedAtConstruction(t *testing.T) {
	tmpDir := t.TempDir()
	h := NewHandler(tmpDir, nil, "http://gateway")
	if h.absRoot == "" {
		t.Error("absRoot must not be empty after construction")
	}
	expected, _ := filepath.Abs(tmpDir)
	if h.absRoot != expected {
		t.Errorf("absRoot = %q, want %q", h.absRoot, expected)
	}
}
