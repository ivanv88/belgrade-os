package main

import "os"

type Config struct {
	Port           string
	RedisURL       string
	CFTeamDomain   string
	CFAudience     string
	AppsRoot       string
	GatewayURL     string
	TrustedUserIDs string
}

func LoadConfig() Config {
	return Config{
		Port:           getEnv("PORT", "8080"),
		RedisURL:       getEnvOr(os.Getenv("GATEWAY_REDIS_URL"), getEnv("REDIS_URL", "redis://localhost:6379")),
		CFTeamDomain:   getEnv("CF_TEAM_DOMAIN", ""),
		CFAudience:     getEnv("CF_AUDIENCE", ""),
		AppsRoot:       getEnv("APPS_ROOT", "../apps"),
		GatewayURL:     getEnv("GATEWAY_URL", "http://localhost:8080"),
		TrustedUserIDs: getEnv("TRUSTED_USER_IDS", ""),
	}
}

func getEnv(key, fallback string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return fallback
}

// getEnvOr returns value if non-empty, otherwise fallback.
func getEnvOr(value, fallback string) string {
	if value != "" {
		return value
	}
	return fallback
}
