package httpserver

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"testing"

	"github.com/algony-tony/mymcp/go/internal/auth"
	"github.com/algony-tony/mymcp/go/internal/config"
	"github.com/algony-tony/mymcp/go/internal/tools"
)

func testMux(t *testing.T) (*http.ServeMux, *auth.TokenStore) {
	t.Helper()
	t.Setenv("MYMCP_AUDIT_LOG_DIR", filepath.Join(t.TempDir(), "audit"))
	cfg, err := config.Load()
	if err != nil {
		t.Fatal(err)
	}
	store, err := auth.NewTokenStore(filepath.Join(t.TempDir(), "tokens.json"), "admin")
	if err != nil {
		t.Fatal(err)
	}
	store.AddEphemeral("tok_rw", "t-rw", "rw")
	d := tools.Deps{Cfg: cfg, Protected: tools.ProtectedFromConfig(cfg)}
	return BuildMux(d, store, "vtest"), store
}

func TestMcpRequiresBearer(t *testing.T) {
	mux, _ := testMux(t)
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

func TestHealthAndVersion(t *testing.T) {
	mux, _ := testMux(t)
	rec := httptest.NewRecorder()
	mux.ServeHTTP(rec, httptest.NewRequest("GET", "/health", nil))
	var h map[string]string
	json.Unmarshal(rec.Body.Bytes(), &h)
	if rec.Code != 200 || h["status"] != "ok" || h["version"] != "vtest" {
		t.Fatalf("health: %d %v", rec.Code, h)
	}
	rec = httptest.NewRecorder()
	mux.ServeHTTP(rec, httptest.NewRequest("GET", "/version", nil))
	var v map[string]string
	json.Unmarshal(rec.Body.Bytes(), &v)
	if v["version"] != "vtest" {
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
