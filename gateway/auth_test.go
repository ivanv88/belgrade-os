package main

import (
	"crypto/rand"
	"crypto/rsa"
	"encoding/base64"
	"encoding/json"
	"math/big"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"

	"github.com/golang-jwt/jwt/v5"
)

// ─── helpers ─────────────────────────────────────────────────────────────────

func generateTestKey(t *testing.T) *rsa.PrivateKey {
	t.Helper()
	key, err := rsa.GenerateKey(rand.Reader, 2048)
	if err != nil {
		t.Fatalf("generate RSA key: %v", err)
	}
	return key
}

func serveJWKS(t *testing.T, pub *rsa.PublicKey, kid string) *httptest.Server {
	t.Helper()
	n := base64.RawURLEncoding.EncodeToString(pub.N.Bytes())
	eBig := big.NewInt(int64(pub.E))
	e := base64.RawURLEncoding.EncodeToString(eBig.Bytes())
	body := map[string]interface{}{
		"keys": []map[string]string{
			{"kid": kid, "kty": "RSA", "alg": "RS256", "use": "sig", "n": n, "e": e},
		},
	}
	return httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		json.NewEncoder(w).Encode(body)
	}))
}

func signToken(t *testing.T, key *rsa.PrivateKey, kid, sub, aud string, exp time.Time) string {
	t.Helper()
	claims := jwt.MapClaims{
		"sub": sub,
		"aud": aud,
		"iss": "https://test.cloudflareaccess.com",
		"iat": time.Now().Unix(),
		"exp": exp.Unix(),
	}
	tok := jwt.NewWithClaims(jwt.SigningMethodRS256, claims)
	tok.Header["kid"] = kid
	signed, err := tok.SignedString(key)
	if err != nil {
		t.Fatalf("sign token: %v", err)
	}
	return signed
}

func newTestCache(t *testing.T, url string) *JWKSCache {
	t.Helper()
	return &JWKSCache{
		keys:    make(map[string]*rsa.PublicKey),
		ttl:     24 * time.Hour,
		jwksURL: url,
	}
}

// ─── tests ────────────────────────────────────────────────────────────────────

func TestValidateTokenAccepted(t *testing.T) {
	key := generateTestKey(t)
	kid := "kid-valid"
	srv := serveJWKS(t, &key.PublicKey, kid)
	defer srv.Close()

	cache := newTestCache(t, srv.URL)
	tokenStr := signToken(t, key, kid, "user-123", "test-aud", time.Now().Add(time.Hour))

	claims, err := ValidateToken(tokenStr, cache, "test-aud")
	if err != nil {
		t.Fatalf("expected valid token, got: %v", err)
	}
	if claims.UserID != "user-123" {
		t.Fatalf("expected user-123, got %s", claims.UserID)
	}
}

func TestValidateTokenExpiredRejected(t *testing.T) {
	key := generateTestKey(t)
	kid := "kid-expired"
	srv := serveJWKS(t, &key.PublicKey, kid)
	defer srv.Close()

	cache := newTestCache(t, srv.URL)
	tokenStr := signToken(t, key, kid, "user-456", "test-aud", time.Now().Add(-time.Hour))

	_, err := ValidateToken(tokenStr, cache, "test-aud")
	if err == nil {
		t.Fatal("expected error for expired token, got nil")
	}
}

func TestValidateTokenWrongAudienceRejected(t *testing.T) {
	key := generateTestKey(t)
	kid := "kid-aud"
	srv := serveJWKS(t, &key.PublicKey, kid)
	defer srv.Close()

	cache := newTestCache(t, srv.URL)
	tokenStr := signToken(t, key, kid, "user-789", "wrong-aud", time.Now().Add(time.Hour))

	_, err := ValidateToken(tokenStr, cache, "expected-aud")
	if err == nil {
		t.Fatal("expected error for wrong audience, got nil")
	}
}

func TestJWKSCacheFetchesOnce(t *testing.T) {
	key := generateTestKey(t)
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

	cache := newTestCache(t, srv.URL)
	tokenStr := signToken(t, key, kid, "user-abc", "aud", time.Now().Add(time.Hour))

	for i := 0; i < 3; i++ {
		if _, err := ValidateToken(tokenStr, cache, "aud"); err != nil {
			t.Fatalf("validation %d failed: %v", i, err)
		}
	}
	if fetchCount != 1 {
		t.Fatalf("expected 1 JWKS fetch, got %d", fetchCount)
	}
}
