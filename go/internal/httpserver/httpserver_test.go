package httpserver

import (
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/algony-tony/mymcp/go/internal/audit"
	"github.com/algony-tony/mymcp/go/internal/auth"
	"github.com/algony-tony/mymcp/go/internal/config"
	"github.com/algony-tony/mymcp/go/internal/metrics"
	"github.com/algony-tony/mymcp/go/internal/tools"
	"github.com/algony-tony/mymcp/go/internal/transfer"
)

func testMux(t *testing.T, metricsToken string) http.Handler {
	t.Helper()
	t.Setenv("MYMCP_AUDIT_LOG_DIR", filepath.Join(t.TempDir(), "audit"))
	cfg, err := config.Load()
	if err != nil {
		t.Fatal(err)
	}
	store, err := auth.NewTokenStore(filepath.Join(t.TempDir(), "tokens.json"), "admin-tok")
	if err != nil {
		t.Fatal(err)
	}
	a, _ := audit.New(false, t.TempDir(), 1<<20, 5)
	m := metrics.New(func() float64 { return 0 })
	d := tools.Deps{Cfg: cfg, Protected: tools.ProtectedFromConfig(cfg), Tickets: transfer.NewTicketStore()}
	return BuildMux(d, store, a, m, metricsToken, "test")
}

func TestMcpRequiresBearer(t *testing.T) {
	mux := testMux(t, "")
	for _, tc := range []struct {
		header string
		detail string
	}{
		{"", "Missing Bearer token"},
		{"Basic abc", "Missing Bearer token"},
		{"Bearer tok_wrong", "Invalid or disabled token"},
	} {
		req := httptest.NewRequest("POST", "/mcp", nil)
		if tc.header != "" {
			req.Header.Set("Authorization", tc.header)
		}
		rec := httptest.NewRecorder()
		mux.ServeHTTP(rec, req)
		if rec.Code != 401 {
			t.Fatalf("header %q: code = %d", tc.header, rec.Code)
		}
		var body map[string]string
		json.Unmarshal(rec.Body.Bytes(), &body)
		if body["detail"] != tc.detail {
			t.Fatalf("header %q: detail = %q, want %q", tc.header, body["detail"], tc.detail)
		}
	}
}

func TestMetricsDisabledWithoutToken(t *testing.T) {
	mux := testMux(t, "")
	req := httptest.NewRequest("GET", "/metrics", nil)
	rec := httptest.NewRecorder()
	mux.ServeHTTP(rec, req)
	if rec.Code != 503 {
		t.Fatalf("status = %d, want 503", rec.Code)
	}
}

func TestMetricsRequiresToken(t *testing.T) {
	mux := testMux(t, "sekret")
	// no auth → 401
	rec := httptest.NewRecorder()
	mux.ServeHTTP(rec, httptest.NewRequest("GET", "/metrics", nil))
	if rec.Code != 401 {
		t.Fatalf("no-auth status = %d, want 401", rec.Code)
	}
	// correct token → 200 + prometheus body
	req := httptest.NewRequest("GET", "/metrics", nil)
	req.Header.Set("Authorization", "Bearer sekret")
	rec = httptest.NewRecorder()
	mux.ServeHTTP(rec, req)
	if rec.Code != 200 {
		t.Fatalf("auth status = %d, want 200", rec.Code)
	}
	body, _ := io.ReadAll(rec.Result().Body)
	if len(body) == 0 {
		t.Fatal("empty metrics body")
	}
}

func TestHealthAndVersion(t *testing.T) {
	mux := testMux(t, "")
	rec := httptest.NewRecorder()
	mux.ServeHTTP(rec, httptest.NewRequest("GET", "/health", nil))
	var h map[string]string
	json.Unmarshal(rec.Body.Bytes(), &h)
	if rec.Code != 200 || h["status"] != "ok" || h["version"] != "test" {
		t.Fatalf("health: %d %v", rec.Code, h)
	}
	rec = httptest.NewRecorder()
	mux.ServeHTTP(rec, httptest.NewRequest("GET", "/version", nil))
	var v map[string]string
	json.Unmarshal(rec.Body.Bytes(), &v)
	if v["version"] != "test" {
		t.Fatalf("version: %v", v)
	}
}

func TestTempTokenDecision(t *testing.T) {
	// No env file + no admin token → temp tokens kick in.
	t.Setenv("MYMCP_ENV_FILE", filepath.Join(t.TempDir(), "nonexistent.env"))
	os.Unsetenv("MYMCP_ADMIN_TOKEN")
	if !NeedTempTokens() {
		t.Fatal("want temp tokens when nothing configured")
	}
	t.Setenv("MYMCP_ADMIN_TOKEN", "x")
	if NeedTempTokens() {
		t.Fatal("no temp tokens when admin token set")
	}
}

func TestHTTPMetricsPathLabelStripsMethod(t *testing.T) {
	mux := testMux(t, "sekret")
	// Hit a method-prefixed route so the counter records its path label.
	mux.ServeHTTP(httptest.NewRecorder(), httptest.NewRequest("GET", "/health", nil))
	// Scrape metrics and inspect the emitted path label.
	req := httptest.NewRequest("GET", "/metrics", nil)
	req.Header.Set("Authorization", "Bearer sekret")
	rec := httptest.NewRecorder()
	mux.ServeHTTP(rec, req)
	body, _ := io.ReadAll(rec.Result().Body)
	s := string(body)
	// Parity with src/mymcp/server.py:_path_label: bare "/health", not "GET /health".
	if !strings.Contains(s, `path="/health"`) {
		t.Fatalf(`want path="/health" in metrics, got:\n%s`, s)
	}
	if strings.Contains(s, `path="GET /health"`) {
		t.Fatalf("path label must not include the method prefix:\n%s", s)
	}
}

func TestAdminRequiresAdminToken(t *testing.T) {
	mux := testMux(t, "")
	rec := httptest.NewRecorder()
	mux.ServeHTTP(rec, httptest.NewRequest("GET", "/admin/tokens", nil))
	if rec.Code != 401 {
		t.Fatalf("no-token = %d", rec.Code)
	}
	req := httptest.NewRequest("GET", "/admin/tokens", nil)
	req.Header.Set("Authorization", "Bearer nope")
	rec = httptest.NewRecorder()
	mux.ServeHTTP(rec, req)
	if rec.Code != 403 {
		t.Fatalf("wrong-token = %d", rec.Code)
	}
}

func TestAdminCreateListRevoke(t *testing.T) {
	mux := testMux(t, "") // testMux uses admin token "admin-tok"
	do := func(method, path, body string) *httptest.ResponseRecorder {
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
	rec := do("POST", "/admin/tokens", `{"name":"ci","role":"rw"}`)
	if rec.Code != 200 || !strings.Contains(rec.Body.String(), `"role":"rw"`) {
		t.Fatalf("create: %d %s", rec.Code, rec.Body)
	}
	var created map[string]string
	json.Unmarshal(rec.Body.Bytes(), &created)
	tok := created["token"]
	if rec := do("GET", "/admin/tokens", ""); !strings.Contains(rec.Body.String(), tok) {
		t.Fatalf("list missing token: %s", rec.Body)
	}
	if rec := do("DELETE", "/admin/tokens/"+tok, ""); rec.Code != 200 {
		t.Fatalf("revoke: %d", rec.Code)
	}
	if rec := do("DELETE", "/admin/tokens/"+tok, ""); rec.Code != 404 {
		t.Fatalf("second revoke: %d", rec.Code)
	}
}

func TestGenRequestID(t *testing.T) {
	a := genRequestID()
	b := genRequestID()
	if a == b || len(a) != 32 {
		t.Fatalf("bad request ids: %q %q", a, b)
	}
}
