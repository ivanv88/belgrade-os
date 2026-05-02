package auth

import (
	"crypto/rsa"
	"encoding/base64"
	"encoding/json"
	"fmt"
	"math/big"
	"net/http"
	"sync"
	"time"

	"github.com/golang-jwt/jwt/v5"
	"golang.org/x/sync/singleflight"
)

var jwksHTTPClient = &http.Client{Timeout: 10 * time.Second}

type jwk struct {
	Kid string `json:"kid"`
	N   string `json:"n"`
	E   string `json:"e"`
}

type jwksResponse struct {
	Keys []jwk `json:"keys"`
}

// JWKSCache caches Cloudflare RS256 public keys by kid with a 24 h TTL.
type JWKSCache struct {
	mu        sync.RWMutex
	sf        singleflight.Group
	keys      map[string]*rsa.PublicKey
	fetchedAt time.Time
	ttl       time.Duration
	jwksURL   string
}

func NewJWKSCache(teamDomain string) *JWKSCache {
	return &JWKSCache{
		keys:    make(map[string]*rsa.PublicKey),
		ttl:     24 * time.Hour,
		jwksURL: fmt.Sprintf("https://%s.cloudflareaccess.com/cdn-cgi/access/certs", teamDomain),
	}
}

func (c *JWKSCache) GetKey(kid string) (*rsa.PublicKey, error) {
	c.mu.RLock()
	key, ok := c.keys[kid]
	expired := time.Since(c.fetchedAt) > c.ttl
	c.mu.RUnlock()

	if ok && !expired {
		return key, nil
	}
	if _, err, _ := c.sf.Do("refresh", func() (any, error) {
		return nil, c.refresh()
	}); err != nil {
		return nil, err
	}

	c.mu.RLock()
	key, ok = c.keys[kid]
	c.mu.RUnlock()
	if !ok {
		return nil, fmt.Errorf("unknown kid: %s", kid)
	}
	return key, nil
}

func (c *JWKSCache) refresh() error {
	resp, err := jwksHTTPClient.Get(c.jwksURL) //nolint:gosec
	if err != nil {
		return fmt.Errorf("jwks fetch: %w", err)
	}
	defer resp.Body.Close()

	var r jwksResponse
	if err := json.NewDecoder(resp.Body).Decode(&r); err != nil {
		return fmt.Errorf("jwks decode: %w", err)
	}

	keys := make(map[string]*rsa.PublicKey, len(r.Keys))
	for _, k := range r.Keys {
		pub, err := jwkToRSA(k)
		if err != nil {
			continue
		}
		keys[k.Kid] = pub
	}

	c.mu.Lock()
	c.keys = keys
	c.fetchedAt = time.Now()
	c.mu.Unlock()
	return nil
}

func jwkToRSA(k jwk) (*rsa.PublicKey, error) {
	nBytes, err := base64.RawURLEncoding.DecodeString(k.N)
	if err != nil {
		return nil, fmt.Errorf("decode N: %w", err)
	}
	eBytes, err := base64.RawURLEncoding.DecodeString(k.E)
	if err != nil {
		return nil, fmt.Errorf("decode E: %w", err)
	}
	e := int(new(big.Int).SetBytes(eBytes).Int64())
	return &rsa.PublicKey{N: new(big.Int).SetBytes(nBytes), E: e}, nil
}

// Claims holds the validated identity extracted from a Cloudflare Access JWT.
type Claims struct {
	UserID   string
	Audience jwt.ClaimStrings
}

type contextKey string

const ClaimsKey contextKey = "claims"

func ValidateToken(tokenStr string, cache *JWKSCache, audience string) (*Claims, error) {
	token, err := jwt.Parse(tokenStr, func(t *jwt.Token) (interface{}, error) {
		if _, ok := t.Method.(*jwt.SigningMethodRSA); !ok {
			return nil, fmt.Errorf("unexpected signing method: %v", t.Header["alg"])
		}
		kid, ok := t.Header["kid"].(string)
		if !ok {
			return nil, fmt.Errorf("missing kid in token header")
		}
		return cache.GetKey(kid)
	}, jwt.WithAudience(audience))

	if err != nil {
		return nil, fmt.Errorf("token validation: %w", err)
	}

	mapClaims, ok := token.Claims.(jwt.MapClaims)
	if !ok || !token.Valid {
		return nil, fmt.Errorf("invalid token claims")
	}

	sub, _ := mapClaims["sub"].(string)
	if sub == "" {
		return nil, fmt.Errorf("missing sub claim")
	}

	aud, _ := mapClaims.GetAudience()
	return &Claims{UserID: sub, Audience: aud}, nil
}
