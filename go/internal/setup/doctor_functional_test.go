package setup

import (
	"fmt"
	"net"
	"net/http"
	"net/http/httptest"
	"net/url"
	"path/filepath"
	"strings"
	"testing"

	"github.com/algony-tony/mymcp/go/internal/auth"
)

// splitHostPort pulls the host and port out of an httptest.Server URL so a
// test can hand functionalChecks/recorderChecks exactly the (host, port)
// pair Doctor would have computed from MYMCP_HOST/MYMCP_PORT.
func splitHostPort(t *testing.T, rawURL string) (string, string) {
	t.Helper()
	u, err := url.Parse(rawURL)
	if err != nil {
		t.Fatalf("parse %q: %v", rawURL, err)
	}
	host, port, err := net.SplitHostPort(u.Host)
	if err != nil {
		t.Fatalf("split %q: %v", u.Host, err)
	}
	return host, port
}

// newTestTokenStore writes a real tokens.json under t.TempDir() via the
// production auth package (not a hand-rolled fixture), then returns its
// path plus whichever tokens the caller asked for.
func newTestTokenStore(t *testing.T, roles ...string) (string, []string) {
	t.Helper()
	path := filepath.Join(t.TempDir(), "tokens.json")
	store, err := auth.NewTokenStore(path, "unused-by-doctor")
	if err != nil {
		t.Fatalf("NewTokenStore: %v", err)
	}
	var toks []string
	for i, role := range roles {
		tok, err := store.CreateToken(fmt.Sprintf("t%d", i), role)
		if err != nil {
			t.Fatalf("CreateToken(%s): %v", role, err)
		}
		toks = append(toks, tok)
	}
	return path, toks
}

func toolsListBody(n int) string {
	var schemas strings.Builder
	for i := 0; i < n; i++ {
		if i > 0 {
			schemas.WriteString(",")
		}
		fmt.Fprintf(&schemas, `{"name":"t%d","inputSchema":{}}`, i)
	}
	return fmt.Sprintf(`{"jsonrpc":"2.0","id":1,"result":{"tools":[%s]}}`, schemas.String())
}

func TestFunctionalChecksRWTokenSeeingNineToolsIsOK(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/mcp" {
			t.Errorf("unexpected path %q", r.URL.Path)
		}
		w.WriteHeader(http.StatusOK)
		_, _ = w.Write([]byte(toolsListBody(9)))
	}))
	defer srv.Close()
	host, port := splitHostPort(t, srv.URL)
	tokPath, _ := newTestTokenStore(t, "rw")

	checks := functionalChecks(tokPath, host, port)
	if len(checks) != 1 || checks[0].Severity != SevOK {
		t.Fatalf("checks = %+v, want a single SevOK check", checks)
	}
	if !strings.Contains(checks[0].Detail, "9 tools") {
		t.Errorf("detail = %q, want it to name the tool count", checks[0].Detail)
	}
}

func TestFunctionalChecksRWTokenWrongCountFails(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusOK)
		_, _ = w.Write([]byte(toolsListBody(3)))
	}))
	defer srv.Close()
	host, port := splitHostPort(t, srv.URL)
	tokPath, _ := newTestTokenStore(t, "rw")

	checks := functionalChecks(tokPath, host, port)
	if len(checks) != 1 || checks[0].Severity != SevFail {
		t.Fatalf("checks = %+v, want a single SevFail check", checks)
	}
	if !strings.Contains(checks[0].Detail, "saw 3 tools, want 9") {
		t.Errorf("detail = %q, want it to name the mismatch", checks[0].Detail)
	}
}

func TestFunctionalChecksOnlyROTokenAcceptsNonEmptyList(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusOK)
		// An ro token is never checked against the 9-tool count, only that
		// the list isn't empty.
		_, _ = w.Write([]byte(toolsListBody(5)))
	}))
	defer srv.Close()
	host, port := splitHostPort(t, srv.URL)
	tokPath, _ := newTestTokenStore(t, "ro")

	checks := functionalChecks(tokPath, host, port)
	if len(checks) != 1 || checks[0].Severity != SevOK {
		t.Fatalf("checks = %+v, want a single SevOK check for a non-empty ro list", checks)
	}
	if !strings.Contains(checks[0].Detail, "ro token") {
		t.Errorf("detail = %q, want it to name the ro token", checks[0].Detail)
	}
}

func TestFunctionalChecksNon200Fails(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		http.Error(w, "boom", http.StatusInternalServerError)
	}))
	defer srv.Close()
	host, port := splitHostPort(t, srv.URL)
	tokPath, _ := newTestTokenStore(t, "rw")

	checks := functionalChecks(tokPath, host, port)
	if len(checks) != 1 || checks[0].Severity != SevFail {
		t.Fatalf("checks = %+v, want a single SevFail check", checks)
	}
	if !strings.Contains(checks[0].Detail, "HTTP 500") {
		t.Errorf("detail = %q, want it to name the status code", checks[0].Detail)
	}
}

func TestFunctionalChecksDialFailureFails(t *testing.T) {
	tokPath, _ := newTestTokenStore(t, "rw")
	// Nothing listens on this port: 127.0.0.1:1 is a reserved low port no
	// test process can bind, so the dial reliably fails without a race.
	checks := functionalChecks(tokPath, "127.0.0.1", "1")
	if len(checks) != 1 || checks[0].Severity != SevFail {
		t.Fatalf("checks = %+v, want a single SevFail check for a dial failure", checks)
	}
}

func TestHTTPJSONUsesGETForEmptyBodyAndPOSTOtherwise(t *testing.T) {
	var gotMethods []string
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		gotMethods = append(gotMethods, r.Method)
		w.WriteHeader(http.StatusOK)
		_, _ = w.Write([]byte("ok"))
	}))
	defer srv.Close()

	if code, body, err := httpJSON(srv.URL, "", ""); err != nil || code != 200 || body != "ok" {
		t.Fatalf("httpJSON(empty body) = %d, %q, %v", code, body, err)
	}
	if code, body, err := httpJSON(srv.URL, "tok", `{"a":1}`); err != nil || code != 200 || body != "ok" {
		t.Fatalf("httpJSON(non-empty body) = %d, %q, %v", code, body, err)
	}
	if len(gotMethods) != 2 || gotMethods[0] != http.MethodGet || gotMethods[1] != http.MethodPost {
		t.Fatalf("methods = %v, want [GET POST]", gotMethods)
	}
}
