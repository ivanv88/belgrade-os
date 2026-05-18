package appproxy_test

import (
	"context"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"belgrade-os/gateway/appproxy"
	"belgrade-os/gateway/auth"
	"belgrade-os/gateway/redis"
)

func injectClaims(r *http.Request, userID string) *http.Request {
	claims := &auth.Claims{UserID: userID}
	return r.WithContext(context.WithValue(r.Context(), auth.ClaimsKey, claims))
}

func makeBridge(t *testing.T, appID, callbackURL string) *httptest.Server {
	t.Helper()
	return httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/v1/apps/"+appID {
			json.NewEncoder(w).Encode(map[string]string{
				"app_id": appID, "callback_url": callbackURL,
			})
			return
		}
		http.NotFound(w, r)
	}))
}

func TestServeAPIReturns401WithoutClaims(t *testing.T) {
	h := appproxy.NewHandler("http://bridge", nil)
	req := httptest.NewRequest(http.MethodPost, "/api/shopping/add_item", strings.NewReader(`{}`))
	w := httptest.NewRecorder()
	h.ServeAPI(w, req)
	if w.Code != http.StatusUnauthorized {
		t.Fatalf("expected 401, got %d", w.Code)
	}
}

func TestServeAPIReturns404ForEmptyAppID(t *testing.T) {
	h := appproxy.NewHandler("http://bridge", nil)
	req := injectClaims(httptest.NewRequest(http.MethodPost, "/api/", nil), "user1")
	w := httptest.NewRecorder()
	h.ServeAPI(w, req)
	if w.Code != http.StatusNotFound {
		t.Fatalf("expected 404, got %d", w.Code)
	}
}

func TestServeAPIReturns403WhenRBACFails(t *testing.T) {
	rClient, err := redis.NewRedisClient("redis://localhost:6379")
	if err != nil {
		t.Skipf("redis unavailable: %v", err)
	}
	defer rClient.Close()

	h := appproxy.NewHandler("http://bridge", rClient)
	req := injectClaims(
		httptest.NewRequest(http.MethodPost, "/api/shopping/add_item", strings.NewReader(`{}`)),
		"no-perms-user",
	)
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	h.ServeAPI(w, req)
	if w.Code != http.StatusForbidden {
		t.Fatalf("expected 403, got %d", w.Code)
	}
}

func TestServeAPIReturns404WhenAppNotRegistered(t *testing.T) {
	rClient, err := redis.NewRedisClient("redis://localhost:6379")
	if err != nil {
		t.Skipf("redis unavailable: %v", err)
	}
	defer rClient.Close()

	ctx := context.Background()
	rClient.RDB.HSet(ctx, "perms:user3", "shopping:api", "member")
	defer rClient.RDB.Del(ctx, "perms:user3")

	bridge := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		http.NotFound(w, r)
	}))
	defer bridge.Close()

	h := appproxy.NewHandler(bridge.URL, rClient)
	req := injectClaims(
		httptest.NewRequest(http.MethodPost, "/api/shopping/add_item", strings.NewReader(`{}`)),
		"user3",
	)
	w := httptest.NewRecorder()
	h.ServeAPI(w, req)
	if w.Code != http.StatusNotFound {
		t.Fatalf("expected 404, got %d", w.Code)
	}
}

func TestServeAPIProxiesRequestToApp(t *testing.T) {
	rClient, err := redis.NewRedisClient("redis://localhost:6379")
	if err != nil {
		t.Skipf("redis unavailable: %v", err)
	}
	defer rClient.Close()

	ctx := context.Background()
	rClient.RDB.HSet(ctx, "perms:user4", "shopping:api", "member")
	defer rClient.RDB.Del(ctx, "perms:user4")

	var receivedUserID string
	appServer := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		receivedUserID = r.Header.Get("X-User-ID")
		body, _ := io.ReadAll(r.Body)
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusOK)
		w.Write(body)
	}))
	defer appServer.Close()

	bridge := makeBridge(t, "shopping", appServer.URL)
	defer bridge.Close()

	h := appproxy.NewHandler(bridge.URL, rClient)
	req := injectClaims(
		httptest.NewRequest(http.MethodPost, "/api/shopping/add_item", strings.NewReader(`{"item":"milk"}`)),
		"user4",
	)
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	h.ServeAPI(w, req)

	if w.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d: %s", w.Code, w.Body.String())
	}
	if receivedUserID != "user4" {
		t.Fatalf("expected X-User-ID=user4, got %q", receivedUserID)
	}
	if !strings.Contains(w.Body.String(), "milk") {
		t.Fatalf("expected echoed body to contain 'milk', got %q", w.Body.String())
	}
}

func TestServeAPIStripsAuthHeaders(t *testing.T) {
	rClient, err := redis.NewRedisClient("redis://localhost:6379")
	if err != nil {
		t.Skipf("redis unavailable: %v", err)
	}
	defer rClient.Close()

	ctx := context.Background()
	rClient.RDB.HSet(ctx, "perms:user5", "shopping:api", "member")
	defer rClient.RDB.Del(ctx, "perms:user5")

	var receivedJWT, receivedCookie, receivedAuth string
	appServer := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		receivedJWT = r.Header.Get("Cf-Access-Jwt-Assertion")
		receivedCookie = r.Header.Get("Cookie")
		receivedAuth = r.Header.Get("Authorization")
		w.WriteHeader(http.StatusOK)
	}))
	defer appServer.Close()

	bridge := makeBridge(t, "shopping", appServer.URL)
	defer bridge.Close()

	h := appproxy.NewHandler(bridge.URL, rClient)
	req := injectClaims(
		httptest.NewRequest(http.MethodPost, "/api/shopping/do", nil),
		"user5",
	)
	req.Header.Set("Cf-Access-Jwt-Assertion", "secret-jwt")
	req.Header.Set("Cookie", "CF_Authorization=cookie-token; session=abc")
	req.Header.Set("Authorization", "Bearer some-token")
	w := httptest.NewRecorder()
	h.ServeAPI(w, req)

	if receivedJWT != "" {
		t.Fatalf("Cf-Access-Jwt-Assertion must be stripped, got %q", receivedJWT)
	}
	if receivedCookie != "" {
		t.Fatalf("Cookie must be stripped, got %q", receivedCookie)
	}
	if receivedAuth != "" {
		t.Fatalf("Authorization must be stripped, got %q", receivedAuth)
	}
}
