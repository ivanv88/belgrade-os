package appproxy

import (
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"strings"
	"time"

	"belgrade-os/gateway/auth"
	"belgrade-os/gateway/manifest"
	"belgrade-os/gateway/redis"
)

var ErrAppNotFound = errors.New("app not registered")

// hopByHopHeaders are stripped from both inbound and outbound proxy requests.
var hopByHopHeaders = []string{
	"Connection", "Keep-Alive", "Proxy-Authenticate", "Proxy-Authorization",
	"Te", "Trailers", "Transfer-Encoding", "Upgrade",
}

type AppProxyHandler struct {
	bridgeURL    string
	redis        *redis.RedisClient
	bridgeClient *http.Client
	appClient    *http.Client
	appsRoot     string
}

func NewHandler(bridgeURL string, rClient *redis.RedisClient, appsRoot string) *AppProxyHandler {
	return &AppProxyHandler{
		bridgeURL:    strings.TrimRight(bridgeURL, "/"),
		redis:        rClient,
		bridgeClient: &http.Client{Timeout: 5 * time.Second},
		appClient:    &http.Client{Timeout: 30 * time.Second},
		appsRoot:     appsRoot,
	}
}

// ServeAPI handles /api/{app_id}/{path...} for all HTTP methods.
func (h *AppProxyHandler) ServeAPI(w http.ResponseWriter, r *http.Request) {
	claims, ok := r.Context().Value(auth.ClaimsKey).(*auth.Claims)
	if !ok {
		http.Error(w, "unauthorized", http.StatusUnauthorized)
		return
	}

	path := strings.TrimPrefix(r.URL.Path, "/api/")
	parts := strings.SplitN(path, "/", 2)
	if len(parts) == 0 || parts[0] == "" {
		http.NotFound(w, r)
		return
	}
	appID := parts[0]
	subPath := ""
	if len(parts) == 2 {
		subPath = parts[1]
	}

	// RBAC check: does the user have "{appID}:api" permission?
	if err := h.checkPermission(r, claims.UserID, appID); err != nil {
		http.Error(w, "forbidden", http.StatusForbidden)
		return
	}

	// Container apps declare their own endpoint in manifest — skip bridge lookup.
	if h.appsRoot != "" {
		if m, err := manifest.Load(h.appsRoot, appID); err == nil && m.Runtime == manifest.RuntimeContainer {
			if m.Endpoint == "" {
				http.Error(w, "container endpoint not configured", http.StatusBadGateway)
				return
			}
			h.proxyRequest(w, r, buildTargetURL(m.Endpoint, subPath, r.URL.RawQuery), claims.UserID)
			return
		}
	}

	callbackURL, err := h.fetchCallbackURL(r, appID)
	if errors.Is(err, ErrAppNotFound) {
		http.NotFound(w, r)
		return
	}
	if err != nil {
		http.Error(w, "gateway error", http.StatusBadGateway)
		return
	}

	targetURL := strings.TrimRight(callbackURL, "/")
	if subPath != "" {
		targetURL = targetURL + "/" + subPath
	}
	if r.URL.RawQuery != "" {
		targetURL = targetURL + "?" + r.URL.RawQuery
	}

	h.proxyRequest(w, r, targetURL, claims.UserID)
}

func (h *AppProxyHandler) checkPermission(r *http.Request, userID, appID string) error {
	if h.redis == nil {
		return fmt.Errorf("redis not configured")
	}
	// RedisClient.GetPermission checks HGet perms:{userID} {appID}:{bundleID}
	val, err := h.redis.GetPermission(r.Context(), userID, appID, "api")
	if err != nil || val == "" {
		return fmt.Errorf("no permission")
	}
	return nil
}

func (h *AppProxyHandler) fetchCallbackURL(r *http.Request, appID string) (string, error) {
	req, err := http.NewRequestWithContext(r.Context(), http.MethodGet,
		fmt.Sprintf("%s/v1/apps/%s", h.bridgeURL, appID), nil)
	if err != nil {
		return "", fmt.Errorf("build bridge request: %w", err)
	}

	resp, err := h.bridgeClient.Do(req)
	if err != nil {
		return "", fmt.Errorf("bridge unavailable: %w", err)
	}
	defer resp.Body.Close()

	if resp.StatusCode == http.StatusNotFound {
		return "", ErrAppNotFound
	}
	if resp.StatusCode != http.StatusOK {
		return "", fmt.Errorf("bridge error: HTTP %d", resp.StatusCode)
	}

	var info struct {
		CallbackURL string `json:"callback_url"`
	}
	if err := json.NewDecoder(resp.Body).Decode(&info); err != nil {
		return "", fmt.Errorf("bridge response decode: %w", err)
	}

	// Defense-in-depth: validate scheme even though Bridge enforces it at registration.
	parsed, parseErr := url.Parse(info.CallbackURL)
	if parseErr != nil || (parsed.Scheme != "http" && parsed.Scheme != "https") || parsed.Host == "" {
		return "", fmt.Errorf("bridge returned callback_url with disallowed scheme or empty host: %q", info.CallbackURL)
	}
	return info.CallbackURL, nil
}

func (h *AppProxyHandler) proxyRequest(w http.ResponseWriter, r *http.Request, targetURL, userID string) {
	outReq, err := http.NewRequestWithContext(r.Context(), r.Method, targetURL, r.Body)
	if err != nil {
		http.Error(w, "proxy error", http.StatusInternalServerError)
		return
	}

	outReq.Header = r.Header.Clone()
	outReq.Header.Set("X-User-ID", userID)

	// Strip Cloudflare auth credentials — apps must not receive or trust them.
	outReq.Header.Del("Cf-Access-Jwt-Assertion")
	outReq.Header.Del("Cookie")
	outReq.Header.Del("Authorization")

	// Strip hop-by-hop headers.
	for _, hdr := range hopByHopHeaders {
		outReq.Header.Del(hdr)
	}

	resp, err := h.appClient.Do(outReq)
	if err != nil {
		http.Error(w, "app unavailable", http.StatusBadGateway)
		return
	}
	defer resp.Body.Close()

	for key, vals := range resp.Header {
		if isHopByHop(key) {
			continue
		}
		for _, v := range vals {
			w.Header().Add(key, v)
		}
	}
	w.WriteHeader(resp.StatusCode)
	io.Copy(w, resp.Body) //nolint:errcheck
}

func isHopByHop(header string) bool {
	for _, h := range hopByHopHeaders {
		if strings.EqualFold(header, h) {
			return true
		}
	}
	return false
}

func buildTargetURL(base, subPath, rawQuery string) string {
	u := strings.TrimRight(base, "/")
	if subPath != "" {
		u = u + "/" + subPath
	}
	if rawQuery != "" {
		u = u + "?" + rawQuery
	}
	return u
}
