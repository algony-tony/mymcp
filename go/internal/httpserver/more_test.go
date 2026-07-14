package httpserver

import (
	"encoding/json"
	"errors"
	"io"
	"net"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestRequestIDHonorsInboundHeader(t *testing.T) {
	// Clean inbound ID is echoed.
	r := httptest.NewRequest("GET", "/mcp", nil)
	r.Header.Set("X-Request-ID", "req-abc-123")
	if got := requestID(r); got != "req-abc-123" {
		t.Fatalf("clean inbound id = %q", got)
	}
	// Control chars are rejected → a fresh 32-hex id is generated.
	r2 := httptest.NewRequest("GET", "/mcp", nil)
	r2.Header.Set("X-Request-ID", "bad\nid")
	if got := requestID(r2); got == "bad\nid" || len(got) != 32 {
		t.Fatalf("control-char id must be replaced, got %q", got)
	}
	// No header → generated.
	if got := requestID(httptest.NewRequest("GET", "/mcp", nil)); len(got) != 32 {
		t.Fatalf("generated id len = %d", len(got))
	}
}

// adminDo issues an admin-authenticated request against the mux.
func adminDo(t *testing.T, mux http.Handler, method, path, body string) *httptest.ResponseRecorder {
	t.Helper()
	var r *http.Request
	if body != "" {
		r = httptest.NewRequest(method, path, strings.NewReader(body))
	} else {
		r = httptest.NewRequest(method, path, nil)
	}
	r.Header.Set("Authorization", "Bearer admin-tok")
	rec := httptest.NewRecorder()
	mux.ServeHTTP(rec, r)
	return rec
}

func TestAdminCreateInvalidJSON(t *testing.T) {
	mux := testMux(t, "")
	rec := adminDo(t, mux, "POST", "/admin/tokens", `{not json`)
	if rec.Code != 400 || !strings.Contains(rec.Body.String(), "invalid JSON body") {
		t.Fatalf("invalid JSON = %d %s", rec.Code, rec.Body)
	}
}

func TestAdminCreateBadRole(t *testing.T) {
	mux := testMux(t, "")
	rec := adminDo(t, mux, "POST", "/admin/tokens", `{"name":"x","role":"root"}`)
	if rec.Code != 400 {
		t.Fatalf("bad role = %d %s", rec.Code, rec.Body)
	}
}

func TestAuthMiddlewareSuccessReachesHandler(t *testing.T) {
	mux := testMux(t, "")
	// Mint a valid rw token via the admin endpoint, then use it on /mcp.
	rec := adminDo(t, mux, "POST", "/admin/tokens", `{"name":"ci","role":"rw"}`)
	if rec.Code != 200 {
		t.Fatalf("mint token: %d %s", rec.Code, rec.Body)
	}
	var created map[string]string
	json.Unmarshal(rec.Body.Bytes(), &created)

	req := httptest.NewRequest("POST", "/mcp", strings.NewReader("{}"))
	req.Header.Set("Authorization", "Bearer "+created["token"])
	req.Header.Set("Content-Type", "application/json")
	req.RemoteAddr = "203.0.113.7:5555"
	out := httptest.NewRecorder()
	mux.ServeHTTP(out, req)
	// A valid token clears authMiddleware; the MCP handler may 200/400 but must
	// not emit the 401 auth failure.
	if out.Code == 401 {
		t.Fatalf("valid token was rejected by auth: %d %s", out.Code, out.Body)
	}
}

func TestHTTPMetricsUnmatchedPathLabel(t *testing.T) {
	mux := testMux(t, "sekret")
	// A route with no ServeMux match → r.Pattern == "" → "<unmatched>" label.
	mux.ServeHTTP(httptest.NewRecorder(), httptest.NewRequest("GET", "/no/such/route", nil))
	req := httptest.NewRequest("GET", "/metrics", nil)
	req.Header.Set("Authorization", "Bearer sekret")
	rec := httptest.NewRecorder()
	mux.ServeHTTP(rec, req)
	body, _ := io.ReadAll(rec.Result().Body)
	if !strings.Contains(string(body), `path="<unmatched>"`) {
		t.Fatalf("want <unmatched> path label, got:\n%s", body)
	}
}

func TestServeRequiresAdminToken(t *testing.T) {
	// An explicit (empty) env file makes NeedTempTokens() false, so Serve does
	// NOT mint temp tokens or bind a port — it reaches the admin-token check and
	// returns before ListenAndServe.
	envFile := filepath.Join(t.TempDir(), ".env")
	if err := os.WriteFile(envFile, []byte(""), 0o600); err != nil {
		t.Fatal(err)
	}
	t.Setenv("MYMCP_ENV_FILE", envFile)
	t.Setenv("MYMCP_ADMIN_TOKEN", "")
	if NeedTempTokens() {
		t.Fatal("precondition: explicit env file must disable temp tokens")
	}
	err := Serve("", 0, "test")
	if err == nil || !strings.Contains(err.Error(), "MYMCP_ADMIN_TOKEN") {
		t.Fatalf("Serve without admin token = %v, want MYMCP_ADMIN_TOKEN error", err)
	}
}

// TestServeStartupErrorPropagates drives Serve through its full wiring (store,
// audit, metrics, deps, http.Server build) and returns via the ListenAndServe
// error path — deterministically, by pointing it at an already-bound port. This
// covers the setup body without the flakiness of signal-based shutdown.
func TestServeStartupErrorPropagates(t *testing.T) {
	ln, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	defer ln.Close()
	port := ln.Addr().(*net.TCPAddr).Port

	t.Setenv("MYMCP_ADMIN_TOKEN", "admin-tok")
	t.Setenv("MYMCP_TOKEN_FILE", filepath.Join(t.TempDir(), "tokens.json"))
	t.Setenv("MYMCP_AUDIT_LOG_DIR", filepath.Join(t.TempDir(), "audit"))
	t.Setenv("MYMCP_HOST", "127.0.0.1")

	// portFlag steers cfg.Port to the occupied port → ListenAndServe fails fast.
	err = Serve("", port, "test")
	if err == nil || errors.Is(err, http.ErrServerClosed) {
		t.Fatalf("Serve on a busy port = %v, want a bind error", err)
	}
}
