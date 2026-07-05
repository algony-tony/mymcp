// Package httpserver assembles the HTTP surface: /mcp behind Bearer auth
// (401 bodies identical to the Python core), /health, /version, and the
// serve loop with graceful shutdown.
package httpserver

import (
	"context"
	"encoding/json"
	"fmt"
	"net"
	"net/http"
	"os"
	"os/signal"
	"path/filepath"
	"syscall"
	"time"

	"github.com/modelcontextprotocol/go-sdk/mcp"

	"github.com/algony-tony/mymcp/go/internal/auth"
	"github.com/algony-tony/mymcp/go/internal/config"
	"github.com/algony-tony/mymcp/go/internal/mcpserver"
	"github.com/algony-tony/mymcp/go/internal/tools"
)

// BuildMux wires all routes. version is passed in so tests don't depend on ldflags.
func BuildMux(d tools.Deps, store *auth.TokenStore, version string) *http.ServeMux {
	mux := http.NewServeMux()

	srv := mcpserver.BuildServer(d)
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
	return mux
}

func authMiddleware(store *auth.TokenStore, next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		authz := r.Header.Get("Authorization")
		const prefix = "Bearer "
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
			TokenName: info.Name, Role: info.Role, IP: ip,
		})
		next.ServeHTTP(w, r.WithContext(ctx))
	})
}

func writeJSON(w http.ResponseWriter, code int, body any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(code)
	json.NewEncoder(w).Encode(body)
}

// NeedTempTokens ports the _maybe_set_temp_tokens decision: no discovered
// env file and no MYMCP_ADMIN_TOKEN in the environment.
func NeedTempTokens() bool {
	if config.DiscoveredEnvFile() != "" {
		return false
	}
	return os.Getenv("MYMCP_ADMIN_TOKEN") == ""
}

// Serve runs the server until SIGTERM/SIGINT, then shuts down gracefully and
// flushes the token store. host/port override config when non-zero.
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

	d := tools.Deps{Cfg: cfg, Protected: tools.ProtectedFromConfig(cfg)}
	host, port := cfg.Host, cfg.Port
	if hostFlag != "" {
		host = hostFlag
	}
	if portFlag != 0 {
		port = portFlag
	}
	server := &http.Server{
		Addr:    fmt.Sprintf("%s:%d", host, port),
		Handler: BuildMux(d, store, version),
	}

	errCh := make(chan error, 1)
	go func() { errCh <- server.ListenAndServe() }()
	fmt.Fprintf(os.Stderr, "[mymcp] serving on %s\n", server.Addr)

	sigCh := make(chan os.Signal, 1)
	signal.Notify(sigCh, syscall.SIGTERM, syscall.SIGINT)
	select {
	case err := <-errCh:
		return err
	case <-sigCh:
	}

	ctx, cancel := context.WithTimeout(context.Background(),
		time.Duration(cfg.ShutdownGraceSec)*time.Second)
	defer cancel()
	shutdownErr := server.Shutdown(ctx)
	if err := store.Flush(); err != nil {
		fmt.Fprintf(os.Stderr, "[mymcp] token store flush failed: %v\n", err)
	}
	return shutdownErr
}
