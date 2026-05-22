package manifest

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
)

type Runtime string

const (
	RuntimePlatformProcess Runtime = "platform_process"
	RuntimeContainer       Runtime = "container"
)

type BundleConfig struct {
	Path         string `json:"path"`
	Entry        string `json:"entry"`
	RequiredRole string `json:"required_role"`
}

type UIConfig struct {
	Enabled bool                    `json:"enabled"`
	Bundles map[string]BundleConfig `json:"bundles"`
}

type Manifest struct {
	AppID    string    `json:"app_id"`
	Runtime  Runtime   `json:"runtime"`
	Endpoint string    `json:"endpoint"`
	UI       *UIConfig `json:"ui"`
}

// Load reads and parses manifest.json for appID from appsRoot.
func Load(appsRoot, appID string) (*Manifest, error) {
	data, err := os.ReadFile(filepath.Join(appsRoot, appID, "manifest.json"))
	if err != nil {
		return nil, fmt.Errorf("manifest not found: %w", err)
	}
	var m Manifest
	if err := json.Unmarshal(data, &m); err != nil {
		return nil, fmt.Errorf("invalid manifest JSON: %w", err)
	}
	return &m, nil
}
