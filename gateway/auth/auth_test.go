package auth

import (
	"encoding/base64"
	"encoding/json"
	"math/big"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"
)

// ─── tests ────────────────────────────────────────────────────────────────────

func TestValidateTokenAccepted(t *testing.T) {
	key := GenerateTestKey(t)
	kid := "kid-valid"
	srv := ServeJWKS(t, &key.PublicKey, kid)
	defer srv.Close()

	cache := NewTestCache(t, srv.URL)
	tokenStr := SignToken(t, key, kid, "user-123", "test-aud", time.Now().Add(time.Hour))

	claims, err := ValidateToken(tokenStr, cache, "test-aud")
	if err != nil {
		t.Fatalf("expected valid token, got: %v", err)
	}
	if claims.UserID != "user-123" {
		t.Fatalf("expected user-123, got %s", claims.UserID)
	}
}

func TestValidateTokenExpiredRejected(t *testing.T) {
	key := GenerateTestKey(t)
	kid := "kid-expired"
	srv := ServeJWKS(t, &key.PublicKey, kid)
	defer srv.Close()

	cache := NewTestCache(t, srv.URL)
	tokenStr := SignToken(t, key, kid, "user-456", "test-aud", time.Now().Add(-time.Hour))

	_, err := ValidateToken(tokenStr, cache, "test-aud")
	if err == nil {
		t.Fatal("expected error for expired token, got nil")
	}
}

func TestValidateTokenWrongAudienceRejected(t *testing.T) {
	key := GenerateTestKey(t)
	kid := "kid-aud"
	srv := ServeJWKS(t, &key.PublicKey, kid)
	defer srv.Close()

	cache := NewTestCache(t, srv.URL)
	tokenStr := SignToken(t, key, kid, "user-789", "wrong-aud", time.Now().Add(time.Hour))

	_, err := ValidateToken(tokenStr, cache, "expected-aud")
	if err == nil {
		t.Fatal("expected error for wrong audience, got nil")
	}
}

func TestJWKSCacheFetchesOnce(t *testing.T) {
	key := GenerateTestKey(t)
	kid := "kid-cache"
	fetchCount := 0

	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		fetchCount++
		n := base64.RawURLEncoding.EncodeToString(key.PublicKey.N.Bytes())
		eBig := big.NewInt(int64(key.PublicKey.E))
		e := base64.RawURLEncoding.EncodeToString(eBig.Bytes())
		json.NewEncoder(w).Encode(map[string]interface{}{
			"keys": []map[string]string{
				{"kid": kid, "kty": "RSA", "alg": "RS256", "use": "sig", "n": n, "e": e},
			},
		})
	}))
	defer srv.Close()

	cache := NewTestCache(t, srv.URL)
	tokenStr := SignToken(t, key, kid, "user-abc", "aud", time.Now().Add(time.Hour))

	for i := 0; i < 3; i++ {
		if _, err := ValidateToken(tokenStr, cache, "aud"); err != nil {
			t.Fatalf("validation %d failed: %v", i, err)
		}
	}
	if fetchCount != 1 {
		t.Fatalf("expected 1 JWKS fetch, got %d", fetchCount)
	}
}
