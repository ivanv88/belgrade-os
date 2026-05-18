package ui

import (
	"fmt"
	"net/http"
	"os"
	"path/filepath"
	"strings"

	"belgrade-os/gateway/auth"
	"belgrade-os/gateway/redis"
)

type Handler struct {
	AppsRoot   string
	absRoot    string // resolved once at construction; never changes
	Redis      *redis.RedisClient
	GatewayURL string
}

func NewHandler(appsRoot string, redis *redis.RedisClient, gatewayURL string) *Handler {
	abs, err := filepath.Abs(appsRoot)
	if err != nil {
		// If the working directory is unresolvable, the process is in an
		// undefined state. Panic is preferable to silently serving any file.
		panic(fmt.Sprintf("ui: cannot resolve appsRoot %q: %v", appsRoot, err))
	}
	return &Handler{
		AppsRoot:   appsRoot,
		absRoot:    abs,
		Redis:      redis,
		GatewayURL: gatewayURL,
	}
}

func (h *Handler) ServeAsset(w http.ResponseWriter, r *http.Request) {
	// Identity is expected in context from middleware
	claims, ok := r.Context().Value(auth.ClaimsKey).(*auth.Claims)
	if !ok {
		http.Error(w, "unauthorized", http.StatusUnauthorized)
		return
	}

	path := strings.TrimPrefix(r.URL.Path, "/ui/")
	if path == "" {
		http.NotFound(w, r)
		return
	}
	parts := strings.Split(path, "/")

	appID := parts[0]
	var bundleID string
	var subPath string

	if len(parts) < 2 || parts[1] == "" {
		// Default to 'web' bundle
		bundleID = "web"
		subPath = "index.html"
	} else {
		bundleID = parts[1]
		subPath = strings.Join(parts[2:], "/")
		if subPath == "" {
			subPath = "index.html"
		}
	}

	// Path traversal protection
	if strings.Contains(appID, "..") || strings.Contains(bundleID, "..") || strings.Contains(subPath, "..") {
		http.Error(w, "invalid path", http.StatusBadRequest)
		return
	}

	// RBAC Check
	role, err := h.Redis.GetPermission(r.Context(), claims.UserID, appID, bundleID)
	if err != nil {
		http.Error(w, "forbidden", http.StatusForbidden)
		return
	}

	filePath := filepath.Join(h.AppsRoot, appID, "static", bundleID, subPath)

	// Verify it's within AppsRoot
	absPath, err := filepath.Abs(filePath)
	if err != nil {
		http.Error(w, "internal error", http.StatusInternalServerError)
		return
	}

	// Use filepath.Rel instead of strings.HasPrefix to prevent sibling-directory bypass.
	rel, err := filepath.Rel(h.absRoot, absPath)
	if err != nil || strings.HasPrefix(rel, "..") {
		http.Error(w, "forbidden", http.StatusForbidden)
		return
	}

	// Reject directories — http.ServeFile would render a listing.
	info, err := os.Stat(filePath)
	if err != nil || info.IsDir() {
		http.NotFound(w, r)
		return
	}

	// For HTML files, inject config
	if strings.HasSuffix(subPath, ".html") {
		h.serveHTMLWithConfig(w, r, filePath, appID, bundleID, claims.UserID, role)
		return
	}

	http.ServeFile(w, r, filePath)
}

func (h *Handler) serveHTMLWithConfig(w http.ResponseWriter, r *http.Request, filePath, appID, bundleID, userID, role string) {
	content, err := os.ReadFile(filePath)
	if err != nil {
		http.Error(w, "not found", http.StatusNotFound)
		return
	}

	configScript := fmt.Sprintf(`
<script id="belgrade-config">
  window.BELGRADE_CONFIG = {
    "gateway_url": %q,
    "app_id": %q,
    "bundle_id": %q,
    "user_id": %q,
    "role": %q
  };
</script>
`, h.GatewayURL, appID, bundleID, userID, role)

	html := string(content)
	// Inject before </head> or <body>
	if strings.Contains(html, "</head>") {
		html = strings.Replace(html, "</head>", configScript+"</head>", 1)
	} else if strings.Contains(html, "<body>") {
		html = strings.Replace(html, "<body>", "<body>"+configScript, 1)
	} else {
		html = configScript + html
	}

	w.Header().Set("Content-Type", "text/html")
	fmt.Fprint(w, html)
}
