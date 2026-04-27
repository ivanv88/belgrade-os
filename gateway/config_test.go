package main

import (
	"os"
	"testing"
)

func TestLoadConfigDefaults(t *testing.T) {
	os.Unsetenv("PORT")
	os.Unsetenv("REDIS_URL")
	os.Unsetenv("CF_TEAM_DOMAIN")
	os.Unsetenv("CF_AUDIENCE")
	cfg := LoadConfig()
	if cfg.Port != "8080" {
		t.Fatalf("expected port 8080, got %s", cfg.Port)
	}
	if cfg.RedisURL != "redis://localhost:6379" {
		t.Fatalf("expected redis://localhost:6379, got %s", cfg.RedisURL)
	}
}

func TestLoadConfigFromEnv(t *testing.T) {
	t.Setenv("PORT", "9090")
	t.Setenv("REDIS_URL", "redis://myhost:6379")
	t.Setenv("CF_TEAM_DOMAIN", "myteam")
	t.Setenv("CF_AUDIENCE", "myaud")
	cfg := LoadConfig()
	if cfg.Port != "9090" {
		t.Fatalf("expected 9090, got %s", cfg.Port)
	}
	if cfg.RedisURL != "redis://myhost:6379" {
		t.Fatalf("expected redis://myhost:6379, got %s", cfg.RedisURL)
	}
	if cfg.CFTeamDomain != "myteam" {
		t.Fatalf("expected myteam, got %s", cfg.CFTeamDomain)
	}
	if cfg.CFAudience != "myaud" {
		t.Fatalf("expected myaud, got %s", cfg.CFAudience)
	}
}
