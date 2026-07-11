// Package httpserver assembles the HTTP surface: /mcp behind Bearer auth,
// /metrics behind the metrics token, /health, /version, an HTTP request counter,
// request-id propagation, and the serve loop with graceful shutdown that also
// tears down in-flight bash process groups and closes the audit writer.
package httpserver

import (
	"context"
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"net"
	"net/http"
	"os"
	"os/signal"
	"path/filepath"
	"strconv"
	"strings"
	"syscall"
	"time"

	"github.com/modelcontextprotocol/go-sdk/mcp"

	"github.com/algony-tony/mymcp/go/internal/audit"
	"github.com/algony-tony/mymcp/go/internal/auth"
	"github.com/algony-tony/mymcp/go/internal/config"
	"github.com/algony-tony/mymcp/go/internal/mcpserver"
	"github.com/algony-tony/mymcp/go/internal/metrics"
	"github.com/algony-tony/mymcp/go/internal/tools"
	"github.com/algony-tony/mymcp/go/internal/transfer"
)

// BuildMux wires all routes and returns a handler wrapped with the HTTP request
// counter. version is passed in so tests don't depend on ldflags.
func BuildMux(d tools.Deps, store *auth.TokenStore, auditW *audit.Writer, m *metrics.Metrics, metricsToken, version string) http.Handler {
	mux := http.NewServeMux()

	srv := mcpserver.New(d, auditW, m).Build()
	mcpHandler := mcp.NewStreamableHTTPHandler(
		func(*http.Request) *mcp.Server { return srv },
		&mcp.StreamableHTTPOptions{Stateless: true},
	)
	mux.Handle("/mcp", authMiddleware(store, mcpHandler))

	mux.HandleFunc("GET /health", func(w http.ResponseWriter, _ *http.Request) {
		writeJSON(w, 200, map[string]string{"status": "ok", "version": version})
	})
	mux.HandleFunc("GET /version", func(w http.ResponseWriter, _ *http.Request) {
		writeJSON(w, 200, map[string]string{"version": version})
	})
	mux.HandleFunc("GET /metrics", func(w http.ResponseWriter, r *http.Request) {
		if metricsToken == "" {
			writeJSON(w, 503, map[string]string{"detail": "Metrics disabled: MYMCP_METRICS_TOKEN not configured"})
			return
		}
		if r.Header.Get("Authorization") != "Bearer "+metricsToken {
			writeJSON(w, 401, map[string]string{"detail": "Unauthorized"})
			return
		}
		m.Handler().ServeHTTP(w, r)
	})

	// Transfer endpoints (ticket-only auth; share d.Tickets with the tools).
	(&transfer.Endpoints{
		Tickets: d.Tickets, Audit: auditW, Protected: d.Protected,
		Enabled: d.Cfg.TransferEnabled, OnAuditFail: m.IncAuditFailure,
	}).Register(mux)

	// Admin token CRUD behind the admin token.
	mux.HandleFunc("POST /admin/tokens", adminCreate(store))
	mux.HandleFunc("DELETE /admin/tokens/{token}", adminRevoke(store))
	mux.HandleFunc("GET /admin/tokens", adminList(store))

	return httpMetrics(m, mux)
}

// requireAdmin returns true iff the Bearer token equals the admin token,
// writing the Python-parity 401/403 response otherwise.
func requireAdmin(store *auth.TokenStore, w http.ResponseWriter, r *http.Request) bool {
	const prefix = "Bearer "
	authz := r.Header.Get("Authorization")
	if len(authz) < len(prefix) || authz[:len(prefix)] != prefix {
		writeJSON(w, 401, map[string]string{"detail": "Missing Bearer token"})
		return false
	}
	if authz[len(prefix):] != store.AdminToken() {
		writeJSON(w, 403, map[string]string{"detail": "Admin token required"})
		return false
	}
	return true
}

func adminCreate(store *auth.TokenStore) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		if !requireAdmin(store, w, r) {
			return
		}
		var body struct {
			Name string `json:"name"`
			Role string `json:"role"`
		}
		body.Role = "ro"
		if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
			writeJSON(w, 400, map[string]string{"detail": "invalid JSON body"})
			return
		}
		tok, err := store.CreateToken(body.Name, body.Role)
		if err != nil {
			writeJSON(w, 400, map[string]string{"detail": err.Error()})
			return
		}
		writeJSON(w, 200, map[string]string{"token": tok, "name": body.Name, "role": body.Role})
	}
}

func adminRevoke(store *auth.TokenStore) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		if !requireAdmin(store, w, r) {
			return
		}
		token := r.PathValue("token")
		found, err := store.RevokeToken(token)
		if err != nil {
			// Never confirm a revocation that was not persisted (SOC): a lost
			// write would resurrect the credential on restart.
			writeJSON(w, 500, map[string]string{"detail": err.Error()})
			return
		}
		if !found {
			writeJSON(w, 404, map[string]string{"detail": "Token not found"})
			return
		}
		writeJSON(w, 200, map[string]string{"revoked": token})
	}
}

func adminList(store *auth.TokenStore) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		if !requireAdmin(store, w, r) {
			return
		}
		writeJSON(w, 200, store.ListTokens())
	}
}

type statusRecorder struct {
	http.ResponseWriter
	status int
}

func (s *statusRecorder) WriteHeader(code int) {
	s.status = code
	s.ResponseWriter.WriteHeader(code)
}

// httpMetrics records mymcp_http_requests_total{path,method,status}. ServeMux
// stores the matched route in r.Pattern, but for method-prefixed patterns
// ("GET /health") it includes the method; src/mymcp/server.py:_path_label emits
// the bare route path ("/health"), so we strip the leading "METHOD " to keep the
// label value identical to the Python core. Unmatched requests → "<unmatched>",
// keeping cardinality bounded against scanners.
func httpMetrics(m *metrics.Metrics, next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		rec := &statusRecorder{ResponseWriter: w, status: 200}
		next.ServeHTTP(rec, r)
		path := r.Pattern
		if i := strings.IndexByte(path, ' '); i >= 0 {
			path = path[i+1:] // drop the "METHOD " prefix ServeMux records
		}
		if path == "" {
			path = "<unmatched>"
		}
		m.HTTPRequests.WithLabelValues(path, r.Method, strconv.Itoa(rec.status)).Inc()
	})
}

func authMiddleware(store *auth.TokenStore, next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		const prefix = "Bearer "
		authz := r.Header.Get("Authorization")
		if len(authz) < len(prefix) || authz[:len(prefix)] != prefix {
			writeJSON(w, 401, map[string]string{"detail": "Missing Bearer token"})
			return
		}
		info := store.Validate(authz[len(prefix):])
		if info == nil {
			writeJSON(w, 401, map[string]string{"detail": "Invalid or disabled token"})
			return
		}
		ip, _, err := net.SplitHostPort(r.RemoteAddr)
		if err != nil {
			ip = "unknown"
		}
		ctx := mcpserver.WithAuthInfo(r.Context(), mcpserver.AuthInfo{
			TokenName: info.Name, Role: info.Role, IP: ip, RequestID: requestID(r),
		})
		next.ServeHTTP(w, r.WithContext(ctx))
	})
}

// requestID honours an inbound X-Request-ID (rejecting control chars) else
// generates one — parity with RequestIdMiddleware.
func requestID(r *http.Request) string {
	if rid := r.Header.Get("X-Request-ID"); rid != "" && !strings.ContainsAny(rid, "\r\n\x00") {
		return rid
	}
	return genRequestID()
}

func genRequestID() string {
	b := make([]byte, 16)
	_, _ = rand.Read(b)
	return hex.EncodeToString(b)
}

func writeJSON(w http.ResponseWriter, code int, body any) {
	raw, _ := json.Marshal(body)
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(code)
	w.Write(raw)
}

// NeedTempTokens ports the _maybe_set_temp_tokens decision.
func NeedTempTokens() bool {
	if config.DiscoveredEnvFile() != "" {
		return false
	}
	return os.Getenv("MYMCP_ADMIN_TOKEN") == ""
}

// Serve runs the server until SIGTERM/SIGINT, then kills in-flight bash process
// groups, shuts down gracefully, and flushes token store + audit writer.
func Serve(hostFlag string, portFlag int, version string) error {
	var tempRW string
	if NeedTempTokens() {
		if os.Getenv("MYMCP_TOKEN_FILE") == "" {
			os.Setenv("MYMCP_TOKEN_FILE",
				filepath.Join(os.TempDir(), fmt.Sprintf("mymcp-temp-%d.json", os.Getpid())))
		}
		adminTok, err := auth.GenerateToken()
		if err != nil {
			return err
		}
		rwTok, err := auth.GenerateToken()
		if err != nil {
			return err
		}
		os.Setenv("MYMCP_ADMIN_TOKEN", adminTok)
		fmt.Fprintf(os.Stderr, "[mymcp] temp admin token: %s\n", adminTok)
		fmt.Fprintf(os.Stderr, "[mymcp] temp rw token:    %s\n", rwTok)
		fmt.Fprintln(os.Stderr, "[mymcp] tokens are in-memory; they vanish on exit.")
		tempRW = rwTok
	}

	cfg, err := config.Load()
	if err != nil {
		return err
	}
	if cfg.AdminToken == "" {
		return fmt.Errorf("MYMCP_ADMIN_TOKEN environment variable is required")
	}
	store, err := auth.NewTokenStore(cfg.TokenFile, cfg.AdminToken)
	if err != nil {
		return err
	}
	if tempRW != "" {
		store.AddEphemeral(tempRW, "temp-rw", "rw")
	}

	auditW, err := audit.New(cfg.AuditEnabled, cfg.AuditLogDir, cfg.AuditMaxBytes, cfg.AuditBackupCount)
	if err != nil {
		return err
	}
	m := metrics.New(func() float64 { return float64(tools.InflightCount()) })

	d := tools.Deps{Cfg: cfg, Protected: tools.ProtectedFromConfig(cfg), Tickets: transfer.NewTicketStore()}
	host, port := cfg.Host, cfg.Port
	if hostFlag != "" {
		host = hostFlag
	}
	if portFlag != 0 {
		port = portFlag
	}
	server := &http.Server{
		Addr:              fmt.Sprintf("%s:%d", host, port),
		Handler:           BuildMux(d, store, auditW, m, cfg.MetricsToken, version),
		ReadHeaderTimeout: 10 * time.Second,
		IdleTimeout:       120 * time.Second,
	}

	errCh := make(chan error, 1)
	go func() { errCh <- server.ListenAndServe() }()
	fmt.Fprintf(os.Stderr, "[mymcp] serving on %s\n", server.Addr)

	sigCh := make(chan os.Signal, 1)
	signal.Notify(sigCh, syscall.SIGTERM, syscall.SIGINT)
	defer signal.Stop(sigCh)
	select {
	case err := <-errCh:
		return err
	case <-sigCh:
	}

	// TERM/grace/KILL in-flight bash process groups so their handlers unblock,
	// mirroring the Python CLI signal handler.
	tools.ShutdownInflight(cfg.ShutdownGraceSec)

	ctx, cancel := context.WithTimeout(context.Background(),
		time.Duration(cfg.ShutdownGraceSec)*time.Second)
	defer cancel()
	shutdownErr := server.Shutdown(ctx)
	if errors.Is(shutdownErr, context.DeadlineExceeded) {
		fmt.Fprintln(os.Stderr, "[mymcp] shutdown grace period exceeded; forcing exit")
		shutdownErr = nil
	}
	if err := store.Flush(); err != nil {
		fmt.Fprintf(os.Stderr, "[mymcp] token store flush failed: %v\n", err)
	}
	if err := auditW.Close(); err != nil {
		fmt.Fprintf(os.Stderr, "[mymcp] audit close failed: %v\n", err)
	}
	return shutdownErr
}
