package manifest_test

import (
	"encoding/json"
	"os"
	"path/filepath"
	"testing"

	"belgrade-os/gateway/manifest"
)

func writeManifest(t *testing.T, root, appID string, m *manifest.Manifest) {
	t.Helper()
	dir := filepath.Join(root, appID)
	if err := os.MkdirAll(dir, 0755); err != nil {
		t.Fatalf("mkdir: %v", err)
	}
	data, err := json.Marshal(m)
	if err != nil {
		t.Fatalf("marshal: %v", err)
	}
	if err := os.WriteFile(filepath.Join(dir, "manifest.json"), data, 0644); err != nil {
		t.Fatalf("write manifest: %v", err)
	}
}

func TestLoadReturnsManifestForValidJSON(t *testing.T) {
	root := t.TempDir()
	m := &manifest.Manifest{
		AppID:   "shopping",
		Runtime: manifest.RuntimePlatformProcess,
		UI: &manifest.UIConfig{
			Enabled: true,
			Bundles: map[string]manifest.BundleConfig{
				"web": {Path: "static/web", Entry: "index.html"},
			},
		},
	}
	writeManifest(t, root, "shopping", m)

	got, err := manifest.Load(root, "shopping")
	if err != nil {
		t.Fatalf("Load: %v", err)
	}
	if got.AppID != "shopping" {
		t.Errorf("AppID = %q, want %q", got.AppID, "shopping")
	}
	if got.UI == nil || !got.UI.Enabled {
		t.Error("expected UI.Enabled = true")
	}
}

func TestLoadReturnsErrorForMissingFile(t *testing.T) {
	root := t.TempDir()
	_, err := manifest.Load(root, "nonexistent")
	if err == nil {
		t.Fatal("expected error for missing manifest, got nil")
	}
}

func TestLoadReturnsErrorForInvalidJSON(t *testing.T) {
	root := t.TempDir()
	dir := filepath.Join(root, "badapp")
	if err := os.MkdirAll(dir, 0755); err != nil {
		t.Fatalf("mkdir: %v", err)
	}
	if err := os.WriteFile(filepath.Join(dir, "manifest.json"), []byte("not valid json {{{"), 0644); err != nil {
		t.Fatalf("write: %v", err)
	}

	_, err := manifest.Load(root, "badapp")
	if err == nil {
		t.Fatal("expected error for invalid JSON, got nil")
	}
}

func TestContainerManifestHasRuntimeAndEndpoint(t *testing.T) {
	root := t.TempDir()
	m := &manifest.Manifest{
		AppID:    "mycontainer",
		Runtime:  manifest.RuntimeContainer,
		Endpoint: "http://localhost:9090",
	}
	writeManifest(t, root, "mycontainer", m)

	got, err := manifest.Load(root, "mycontainer")
	if err != nil {
		t.Fatalf("Load: %v", err)
	}
	if got.Runtime != manifest.RuntimeContainer {
		t.Errorf("Runtime = %q, want %q", got.Runtime, manifest.RuntimeContainer)
	}
	if got.Endpoint != "http://localhost:9090" {
		t.Errorf("Endpoint = %q, want %q", got.Endpoint, "http://localhost:9090")
	}
}

func TestPlatformProcessManifestHasDefaultRuntime(t *testing.T) {
	root := t.TempDir()
	// Write JSON without a runtime field — should unmarshal to zero value ""
	dir := filepath.Join(root, "myapp")
	if err := os.MkdirAll(dir, 0755); err != nil {
		t.Fatalf("mkdir: %v", err)
	}
	raw := `{"app_id":"myapp"}`
	if err := os.WriteFile(filepath.Join(dir, "manifest.json"), []byte(raw), 0644); err != nil {
		t.Fatalf("write: %v", err)
	}

	got, err := manifest.Load(root, "myapp")
	if err != nil {
		t.Fatalf("Load: %v", err)
	}
	// When runtime field is absent, Runtime is the zero value (empty string),
	// which is not RuntimeContainer — that's the important invariant.
	if got.Runtime == manifest.RuntimeContainer {
		t.Errorf("expected non-container runtime for app without runtime field, got %q", got.Runtime)
	}
}
