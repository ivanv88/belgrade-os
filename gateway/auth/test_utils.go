package auth

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

func GenerateTestKey(t *testing.T) *rsa.PrivateKey {
	t.Helper()
	key, err := rsa.GenerateKey(rand.Reader, 2048)
	if err != nil {
		t.Fatalf("generate RSA key: %v", err)
	}
	return key
}

func ServeJWKS(t *testing.T, pub *rsa.PublicKey, kid string) *httptest.Server {
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

func SignToken(t *testing.T, key *rsa.PrivateKey, kid, sub, aud string, exp time.Time) string {
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

func NewTestCache(t *testing.T, url string) *JWKSCache {
	t.Helper()
	return &JWKSCache{
		keys:    make(map[string]*rsa.PublicKey),
		ttl:     24 * time.Hour,
		jwksURL: url,
	}
}
