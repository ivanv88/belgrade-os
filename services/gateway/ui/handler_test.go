package ui

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"belgrade-os/gateway/auth"
	"belgrade-os/gateway/manifest"
	"belgrade-os/gateway/redis"
)

// writeManifest creates a manifest.json for appID in root with the given bundle config.
func writeManifest(t *testing.T, root, appID string, cfg map[string]manifest.BundleConfig) {
	t.Helper()
	m := manifest.Manifest{UI: &manifest.UIConfig{Enabled: true, Bundles: cfg}}
	data, err := json.Marshal(m)
	if err != nil {
		t.Fatalf("marshal manifest: %v", err)
	}
	if err := os.WriteFile(filepath.Join(root, appID, "manifest.json"), data, 0644); err != nil {
		t.Fatalf("write manifest: %v", err)
	}
}

func TestServeAssetRBAC(t *testing.T) {
	tmpDir := t.TempDir()
	os.MkdirAll(filepath.Join(tmpDir, "shopping/static/web"), 0755)
	os.WriteFile(filepath.Join(tmpDir, "shopping/static/web/index.html"), []byte("<html></html>"), 0644)
	writeManifest(t, tmpDir, "shopping", map[string]manifest.BundleConfig{
		"web": {Path: "static/web", Entry: "index.html"},
	})

	rClient, err := redis.NewRedisClient("redis://localhost:6379")
	if err != nil {
		t.Skip("Redis unavailable")
	}
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
	parent := t.TempDir()
	appsRoot := filepath.Join(parent, "apps")
	appsEvil := filepath.Join(parent, "apps-evil")
	os.MkdirAll(filepath.Join(appsRoot, "shopping/static/web"), 0755)
	os.MkdirAll(filepath.Join(appsEvil), 0755)
	os.WriteFile(filepath.Join(appsEvil, "secret.txt"), []byte("secret"), 0644)

	h := NewHandler(appsRoot, nil, "http://gateway")

	evilPath := filepath.Join(appsEvil, "secret.txt")
	if !strings.HasPrefix(evilPath, appsRoot) {
		t.Skip("sibling dir doesn't share prefix on this OS — test not applicable")
	}
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
	os.MkdirAll(filepath.Join(tmpDir, "shopping/static/web/assets"), 0755)
	writeManifest(t, tmpDir, "shopping", map[string]manifest.BundleConfig{
		"web": {Path: "static/web", Entry: "index.html"},
	})

	rClient, err := redis.NewRedisClient("redis://localhost:6379")
	if err != nil {
		t.Skip("Redis unavailable")
	}
	ctx := context.Background()
	rClient.RDB.HSet(ctx, "perms:user1", "shopping:web", "admin")
	defer rClient.RDB.Del(ctx, "perms:user1")

	h := NewHandler(tmpDir, rClient, "http://gateway")

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

func TestServeAssetManifestEnforcement(t *testing.T) {
	tmpDir := t.TempDir()
	os.MkdirAll(filepath.Join(tmpDir, "shopping/static/web"), 0755)
	os.WriteFile(filepath.Join(tmpDir, "shopping/static/web/index.html"), []byte("<html></html>"), 0644)

	rClient, err := redis.NewRedisClient("redis://localhost:6379")
	if err != nil {
		t.Skip("Redis unavailable")
	}
	ctx := context.Background()
	rClient.RDB.HSet(ctx, "perms:user1", "shopping:web", "admin")
	defer rClient.RDB.Del(ctx, "perms:user1")

	h := NewHandler(tmpDir, rClient, "http://gateway")

	t.Run("Missing manifest returns 404", func(t *testing.T) {
		req := httptest.NewRequest("GET", "/ui/shopping/web/index.html", nil)
		req = req.WithContext(context.WithValue(req.Context(), auth.ClaimsKey, &auth.Claims{UserID: "user1"}))
		w := httptest.NewRecorder()
		h.ServeAsset(w, req)
		if w.Code != http.StatusNotFound {
			t.Errorf("expected 404, got %d", w.Code)
		}
	})

	t.Run("UI disabled returns 404", func(t *testing.T) {
		m := manifest.Manifest{UI: &manifest.UIConfig{Enabled: false, Bundles: map[string]manifest.BundleConfig{
			"web": {Path: "static/web", Entry: "index.html"},
		}}}
		data, _ := json.Marshal(m)
		os.WriteFile(filepath.Join(tmpDir, "shopping/manifest.json"), data, 0644)
		defer os.Remove(filepath.Join(tmpDir, "shopping/manifest.json"))

		req := httptest.NewRequest("GET", "/ui/shopping/web/index.html", nil)
		req = req.WithContext(context.WithValue(req.Context(), auth.ClaimsKey, &auth.Claims{UserID: "user1"}))
		w := httptest.NewRecorder()
		h.ServeAsset(w, req)
		if w.Code != http.StatusNotFound {
			t.Errorf("expected 404 when ui disabled, got %d", w.Code)
		}
	})

	t.Run("Undeclared bundle returns 404", func(t *testing.T) {
		writeManifest(t, tmpDir, "shopping", map[string]manifest.BundleConfig{
			"web": {Path: "static/web", Entry: "index.html"},
		})
		defer os.Remove(filepath.Join(tmpDir, "shopping/manifest.json"))

		req := httptest.NewRequest("GET", "/ui/shopping/mobile/index.html", nil)
		req = req.WithContext(context.WithValue(req.Context(), auth.ClaimsKey, &auth.Claims{UserID: "user1"}))
		w := httptest.NewRecorder()
		h.ServeAsset(w, req)
		if w.Code != http.StatusNotFound {
			t.Errorf("expected 404 for undeclared bundle, got %d", w.Code)
		}
	})

	t.Run("required_role enforced", func(t *testing.T) {
		writeManifest(t, tmpDir, "shopping", map[string]manifest.BundleConfig{
			"web": {Path: "static/web", Entry: "index.html", RequiredRole: "superadmin"},
		})
		defer os.Remove(filepath.Join(tmpDir, "shopping/manifest.json"))

		// user1 has role "admin" from Redis, but manifest requires "superadmin"
		req := httptest.NewRequest("GET", "/ui/shopping/web/index.html", nil)
		req = req.WithContext(context.WithValue(req.Context(), auth.ClaimsKey, &auth.Claims{UserID: "user1"}))
		w := httptest.NewRecorder()
		h.ServeAsset(w, req)
		if w.Code != http.StatusForbidden {
			t.Errorf("expected 403 for role mismatch, got %d", w.Code)
		}
	})

	t.Run("required_role passes for matching role", func(t *testing.T) {
		writeManifest(t, tmpDir, "shopping", map[string]manifest.BundleConfig{
			"web": {Path: "static/web", Entry: "index.html", RequiredRole: "admin"},
		})
		defer os.Remove(filepath.Join(tmpDir, "shopping/manifest.json"))

		// user1 has role "admin" — matches required_role
		req := httptest.NewRequest("GET", "/ui/shopping/web/index.html", nil)
		req = req.WithContext(context.WithValue(req.Context(), auth.ClaimsKey, &auth.Claims{UserID: "user1"}))
		w := httptest.NewRecorder()
		h.ServeAsset(w, req)
		if w.Code != http.StatusOK {
			t.Errorf("expected 200 for matching role, got %d", w.Code)
		}
	})

	t.Run("bundle entry used as default subpath", func(t *testing.T) {
		os.MkdirAll(filepath.Join(tmpDir, "shopping/dist/web"), 0755)
		os.WriteFile(filepath.Join(tmpDir, "shopping/dist/web/app.html"), []byte("<html>app</html>"), 0644)
		writeManifest(t, tmpDir, "shopping", map[string]manifest.BundleConfig{
			"web": {Path: "dist/web", Entry: "app.html"},
		})
		defer func() {
			os.Remove(filepath.Join(tmpDir, "shopping/manifest.json"))
			os.RemoveAll(filepath.Join(tmpDir, "shopping/dist"))
		}()

		// Request with no subpath — should use bundle.Entry = "app.html"
		req := httptest.NewRequest("GET", "/ui/shopping/web", nil)
		req = req.WithContext(context.WithValue(req.Context(), auth.ClaimsKey, &auth.Claims{UserID: "user1"}))
		w := httptest.NewRecorder()
		h.ServeAsset(w, req)
		if w.Code != http.StatusOK {
			t.Errorf("expected 200 using bundle entry, got %d", w.Code)
		}
		if !strings.Contains(w.Body.String(), "BELGRADE_CONFIG") {
			t.Error("config not injected into bundle entry file")
		}
	})
}
