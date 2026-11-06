package httpserver

import (
	"bytes"
	"crypto/rand"
	"crypto/rsa"
	"crypto/tls"
	"crypto/x509"
	"crypto/x509/pkix"
	"encoding/json"
	"encoding/pem"
	"errors"
	"fmt"
	"io"
	"math/big"
	"net"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

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

func TestAuthFailureLoggingAndMetrics(t *testing.T) {
	stderrCapture := &bytes.Buffer{}
	origStderr := authLogWriter
	authLogWriter = stderrCapture
	defer func() { authLogWriter = origStderr }()

	store, err := auth.NewTokenStore(filepath.Join(t.TempDir(), "tokens.json"), "admin-secret")
	if err != nil {
		t.Fatal(err)
	}
	m := metrics.New(func() float64 { return 0 })
	h := BuildMux(tools.Deps{Cfg: &config.Config{}}, store, nil, m, "metrics-secret", "test")

	// 1. Missing bearer token on /mcp
	req := httptest.NewRequest("POST", "/mcp", strings.NewReader(`{}`))
	req.RemoteAddr = "192.0.2.1:1234"
	rec := httptest.NewRecorder()
	h.ServeHTTP(rec, req)
	if rec.Code != 401 {
		t.Fatalf("expected 401, got %d", rec.Code)
	}
	if !strings.Contains(stderrCapture.String(), "[AUTH_FAIL] ip=192.0.2.1 path=/mcp reason=missing_token") {
		t.Errorf("expected missing_token log, got: %s", stderrCapture.String())
	}
	stderrCapture.Reset()

	// 2. Invalid bearer token on /mcp (verify token secret is NOT logged)
	secretAttempt := "tok_super_secret_leak_test"
	req = httptest.NewRequest("POST", "/mcp", strings.NewReader(`{}`))
	req.RemoteAddr = "192.0.2.2:1234"
	req.Header.Set("Authorization", "Bearer "+secretAttempt)
	rec = httptest.NewRecorder()
	h.ServeHTTP(rec, req)
	if rec.Code != 401 {
		t.Fatalf("expected 401, got %d", rec.Code)
	}
	logOutput := stderrCapture.String()
	if !strings.Contains(logOutput, "[AUTH_FAIL] ip=192.0.2.2 path=/mcp reason=invalid_token") {
		t.Errorf("expected invalid_token log, got: %s", logOutput)
	}
	if strings.Contains(logOutput, secretAttempt) {
		t.Fatalf("CRITICAL: auth failure logged the raw token!")
	}
	stderrCapture.Reset()

	// 3. Invalid admin token on /admin/tokens
	req = httptest.NewRequest("GET", "/admin/tokens", nil)
	req.RemoteAddr = "192.0.2.3:1234"
	req.Header.Set("Authorization", "Bearer wrong-admin")
	rec = httptest.NewRecorder()
	h.ServeHTTP(rec, req)
	if rec.Code != 403 {
		t.Fatalf("expected 403, got %d", rec.Code)
	}
	if !strings.Contains(stderrCapture.String(), "[AUTH_FAIL] ip=192.0.2.3 path=/admin/tokens reason=invalid_admin_token") {
		t.Errorf("expected invalid_admin_token log, got: %s", stderrCapture.String())
	}
	stderrCapture.Reset()

	// 4. Missing / invalid metrics token on /metrics
	req = httptest.NewRequest("GET", "/metrics", nil)
	req.RemoteAddr = "192.0.2.4:1234"
	rec = httptest.NewRecorder()
	h.ServeHTTP(rec, req)
	if rec.Code != 401 {
		t.Fatalf("expected 401, got %d", rec.Code)
	}
	if !strings.Contains(stderrCapture.String(), "[AUTH_FAIL] ip=192.0.2.4 path=/metrics reason=metrics_unauthorized") {
		t.Errorf("expected metrics_unauthorized log, got: %s", stderrCapture.String())
	}

	// 5. Verify Prometheus metrics scrape records the auth failures
	req = httptest.NewRequest("GET", "/metrics", nil)
	req.Header.Set("Authorization", "Bearer metrics-secret")
	rec = httptest.NewRecorder()
	h.ServeHTTP(rec, req)
	if rec.Code != 200 {
		t.Fatalf("metrics scrape failed: %d", rec.Code)
	}
	metricsBody := rec.Body.String()
	if !strings.Contains(metricsBody, `mymcp_auth_failures_total{path="/mcp",reason="missing_token"} 1`) {
		t.Errorf("expected missing_token metric, got:\n%s", metricsBody)
	}
	if !strings.Contains(metricsBody, `mymcp_auth_failures_total{path="/mcp",reason="invalid_token"} 1`) {
		t.Errorf("expected invalid_token metric, got:\n%s", metricsBody)
	}
	if !strings.Contains(metricsBody, `mymcp_auth_failures_total{path="/admin/tokens",reason="invalid_admin_token"} 1`) {
		t.Errorf("expected invalid_admin_token metric, got:\n%s", metricsBody)
	}
	if !strings.Contains(metricsBody, `mymcp_auth_failures_total{path="/metrics",reason="metrics_unauthorized"} 1`) {
		t.Errorf("expected metrics_unauthorized metric, got:\n%s", metricsBody)
	}
}

func generateTestCertificate(t *testing.T) (certFile, keyFile string) {
	priv, err := rsa.GenerateKey(rand.Reader, 2048)
	if err != nil {
		t.Fatal(err)
	}
	template := x509.Certificate{
		SerialNumber: big.NewInt(1),
		Subject:      pkix.Name{Organization: []string{"MyMCP Test"}},
		NotBefore:    time.Now().Add(-1 * time.Hour),
		NotAfter:     time.Now().Add(24 * time.Hour),
		IPAddresses:  []net.IP{net.ParseIP("127.0.0.1")},
		KeyUsage:     x509.KeyUsageKeyEncipherment | x509.KeyUsageDigitalSignature,
		ExtKeyUsage:  []x509.ExtKeyUsage{x509.ExtKeyUsageServerAuth},
	}
	der, err := x509.CreateCertificate(rand.Reader, &template, &template, &priv.PublicKey, priv)
	if err != nil {
		t.Fatal(err)
	}
	certPath := filepath.Join(t.TempDir(), "test.crt")
	keyPath := filepath.Join(t.TempDir(), "test.key")
	certOut, _ := os.Create(certPath)
	pem.Encode(certOut, &pem.Block{Type: "CERTIFICATE", Bytes: der})
	certOut.Close()

	keyOut, _ := os.OpenFile(keyPath, os.O_CREATE|os.O_WRONLY, 0600)
	pem.Encode(keyOut, &pem.Block{Type: "RSA PRIVATE KEY", Bytes: x509.MarshalPKCS1PrivateKey(priv)})
	keyOut.Close()
	return certPath, keyPath
}

func TestHTTPSServerIntegration(t *testing.T) {
	certFile, keyFile := generateTestCertificate(t)
	cfg := &config.Config{
		Host: "127.0.0.1", Port: 0,
		TLSCert: certFile, TLSKey: keyFile,
	}
	store, _ := auth.NewTokenStore(filepath.Join(t.TempDir(), "tokens.json"), "admin")
	m := metrics.New(func() float64 { return 0 })
	handler := BuildMux(tools.Deps{Cfg: cfg}, store, nil, m, "metrics", "test")

	server := &http.Server{
		Handler:   handler,
		TLSConfig: &tls.Config{MinVersion: tls.VersionTLS12},
	}
	ln, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	defer ln.Close()

	go server.ServeTLS(ln, certFile, keyFile)
	defer server.Close()

	client := &http.Client{
		Transport: &http.Transport{
			TLSClientConfig: &tls.Config{InsecureSkipVerify: true},
		},
	}
	resp, err := client.Get(fmt.Sprintf("https://%s/health", ln.Addr().String()))
	if err != nil {
		t.Fatalf("HTTPS GET /health failed: %v", err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != 200 {
		t.Errorf("expected 200, got %d", resp.StatusCode)
	}
	if resp.Header.Get("Strict-Transport-Security") == "" {
		t.Errorf("missing HSTS header on HTTPS response")
	}
}

func TestServeTLSValidation(t *testing.T) {
	certFile, keyFile := generateTestCertificate(t)
	t.Setenv("MYMCP_ADMIN_TOKEN", "admin-tok")
	t.Setenv("MYMCP_TOKEN_FILE", filepath.Join(t.TempDir(), "tokens.json"))
	t.Setenv("MYMCP_AUDIT_LOG_DIR", filepath.Join(t.TempDir(), "audit"))

	// Missing cert
	t.Setenv("MYMCP_TLS_CERT", filepath.Join(t.TempDir(), "nonexistent.crt"))
	t.Setenv("MYMCP_TLS_KEY", keyFile)
	err := Serve("127.0.0.1", 0, "test")
	if err == nil || !strings.Contains(err.Error(), "TLS cert file") {
		t.Fatalf("expected TLS cert file error, got %v", err)
	}

	// Missing key
	t.Setenv("MYMCP_TLS_CERT", certFile)
	t.Setenv("MYMCP_TLS_KEY", filepath.Join(t.TempDir(), "nonexistent.key"))
	err = Serve("127.0.0.1", 0, "test")
	if err == nil || !strings.Contains(err.Error(), "TLS key file") {
		t.Fatalf("expected TLS key file error, got %v", err)
	}
}

func TestServeTLSStartupErrorPropagates(t *testing.T) {
	ln, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	defer ln.Close()
	port := ln.Addr().(*net.TCPAddr).Port

	certFile, keyFile := generateTestCertificate(t)
	t.Setenv("MYMCP_ADMIN_TOKEN", "admin-tok")
	t.Setenv("MYMCP_TOKEN_FILE", filepath.Join(t.TempDir(), "tokens.json"))
	t.Setenv("MYMCP_AUDIT_LOG_DIR", filepath.Join(t.TempDir(), "audit"))
	t.Setenv("MYMCP_HOST", "127.0.0.1")
	t.Setenv("MYMCP_TLS_CERT", certFile)
	t.Setenv("MYMCP_TLS_KEY", keyFile)

	err = Serve("", port, "test")
	if err == nil || errors.Is(err, http.ErrServerClosed) {
		t.Fatalf("Serve on busy TLS port = %v, want a bind error", err)
	}
}
