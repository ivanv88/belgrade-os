package ui

import (
	"context"
	"net/http"

	"belgrade-os/gateway/auth"
)

func AuthMiddleware(cache *auth.JWKSCache, audience string) func(http.Handler) http.Handler {
	return func(next http.Handler) http.Handler {
		return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			tokenStr := r.Header.Get("Cf-Access-Jwt-Assertion")
			if tokenStr == "" {
				// Also check for CF_Authorization cookie if header is missing (for browser loads)
				if cookie, err := r.Cookie("CF_Authorization"); err == nil {
					tokenStr = cookie.Value
				}
			}

			if tokenStr == "" {
				http.Error(w, "missing authentication", http.StatusUnauthorized)
				return
			}

			claims, err := auth.ValidateToken(tokenStr, cache, audience)
			if err != nil {
				http.Error(w, "unauthorized", http.StatusUnauthorized)
				return
			}

			ctx := context.WithValue(r.Context(), auth.ClaimsKey, claims)
			next.ServeHTTP(w, r.WithContext(ctx))
		})
	}
}
