package main

import "os"

type Config struct {
	Port         string
	RedisURL     string
	CFTeamDomain string
	CFAudience   string
	AppsRoot     string
	GatewayURL   string
}

func LoadConfig() Config {
	return Config{
		Port:         getEnv("PORT", "8080"),
		RedisURL:     getEnv("REDIS_URL", "redis://localhost:6379"),
		CFTeamDomain: getEnv("CF_TEAM_DOMAIN", ""),
		CFAudience:   getEnv("CF_AUDIENCE", ""),
		AppsRoot:     getEnv("APPS_ROOT", "../apps"),
		GatewayURL:   getEnv("GATEWAY_URL", "http://localhost:8080"),
	}
}

func getEnv(key, fallback string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return fallback
}
