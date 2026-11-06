# Security Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement native TLS support, trusted reverse-proxy real IP parsing, structured auth failure logging to stderr, Prometheus failure metrics, timing attack mitigations, HTTP security headers, request body limits, and doctor diagnostic checks.

**Architecture:**
All security enhancements are added to the Go core server (`go/`). Configuration is extended in `go/internal/config`, real-IP extraction in `go/internal/httpserver/realip.go`, security headers/body limits in `go/internal/httpserver/security.go`, auth failure logging and constant-time token comparison in `go/internal/httpserver`, metrics in `go/internal/metrics`, and setup health diagnostics in `go/internal/setup/doctor.go`.

**Tech Stack:**
Go 1.22+, `crypto/tls`, `crypto/subtle`, `net/http`, `github.com/prometheus/client_golang`, standard Go testing (`testing`, `httptest`).

---

### Task 1: Configuration Support for TLS and Trusted Proxies

**Files:**
- Modify: `go/internal/config/config.go`
- Test: `go/internal/config/config_test.go`

- [ ] **Step 1: Write failing test in `go/internal/config/config_test.go`**

```go
func TestConfigTLSAndTrustedProxies(t *testing.T) {
	t.Setenv("MYMCP_TLS_CERT", "/etc/ssl/cert.pem")
	t.Setenv("MYMCP_TLS_KEY", "/etc/ssl/key.pem")
	t.Setenv("MYMCP_TRUSTED_PROXIES", "127.0.0.1/32, 10.0.0.0/8, ::1/128")

	cfg, err := Load()
	if err != nil {
		t.Fatalf("Load: %v", err)
	}
	if cfg.TLSCert != "/etc/ssl/cert.pem" || cfg.TLSKey != "/etc/ssl/key.pem" {
		t.Errorf("got cert=%q key=%q", cfg.TLSCert, cfg.TLSKey)
	}
	if !cfg.IsTLS() {
		t.Errorf("expected IsTLS() == true")
	}

	nets, err := cfg.ParsedTrustedProxies()
	if err != nil {
		t.Fatalf("ParsedTrustedProxies: %v", err)
	}
	if len(nets) != 3 {
		t.Fatalf("expected 3 nets, got %d", len(nets))
	}

	// Incomplete TLS configuration must fail
	t.Setenv("MYMCP_TLS_CERT", "/etc/ssl/cert.pem")
	t.Setenv("MYMCP_TLS_KEY", "")
	if _, err := Load(); err == nil {
		t.Errorf("expected error when only MYMCP_TLS_CERT is provided")
	}
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd go && go test -v ./internal/config -run TestConfigTLSAndTrustedProxies`
Expected: Compile error (fields/methods `TLSCert`, `TLSKey`, `TrustedProxies`, `IsTLS`, `ParsedTrustedProxies` not defined).

- [ ] **Step 3: Implement config fields and helpers in `go/internal/config/config.go`**

In `Config` struct:
```go
	TLSCert        string
	TLSKey         string
	TrustedProxies string
```

In `Load()`:
```go
	cfg.TLSCert = getStr(get, "MYMCP_TLS_CERT", "")
	cfg.TLSKey = getStr(get, "MYMCP_TLS_KEY", "")
	cfg.TrustedProxies = getStr(get, "MYMCP_TRUSTED_PROXIES", "")

	if (cfg.TLSCert == "") != (cfg.TLSKey == "") {
		return nil, fmt.Errorf("both MYMCP_TLS_CERT and MYMCP_TLS_KEY must be set to enable TLS, got cert=%q key=%q", cfg.TLSCert, cfg.TLSKey)
	}
```

Add methods:
```go
// IsTLS reports whether TLS certificate and key paths are both configured.
func (c *Config) IsTLS() bool {
	return c.TLSCert != "" && c.TLSKey != ""
}

// ParsedTrustedProxies returns the parsed IPNet slices from TrustedProxies.
// Supports CIDR notation ("192.168.1.0/24") as well as bare IPs ("127.0.0.1" -> "127.0.0.1/32").
func (c *Config) ParsedTrustedProxies() ([]*net.IPNet, error) {
	if strings.TrimSpace(c.TrustedProxies) == "" {
		return nil, nil
	}
	var out []*net.IPNet
	for _, item := range strings.Split(c.TrustedProxies, ",") {
		item = strings.TrimSpace(item)
		if item == "" {
			continue
		}
		if !strings.Contains(item, "/") {
			ip := net.ParseIP(item)
			if ip == nil {
				return nil, fmt.Errorf("invalid trusted proxy IP: %q", item)
			}
			bits := 32
			if ip.To4() == nil {
				bits = 128
			}
			item = fmt.Sprintf("%s/%d", item, bits)
		}
		_, ipnet, err := net.ParseCIDR(item)
		if err != nil {
			return nil, fmt.Errorf("invalid trusted proxy CIDR %q: %w", item, err)
		}
		out = append(out, ipnet)
	}
	return out, nil
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd go && go test -v ./internal/config`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add go/internal/config/config.go go/internal/config/config_test.go
git commit -m "feat(config): add TLS and trusted proxies configuration"
```

---

### Task 2: Real Client IP Extraction Behind Trusted Proxies

**Files:**
- Create: `go/internal/httpserver/realip.go`
- Create: `go/internal/httpserver/realip_test.go`

- [ ] **Step 1: Write test in `go/internal/httpserver/realip_test.go`**

```go
package httpserver

import (
	"net"
	"net/http"
	"net/http/httptest"
	"testing"
)

func TestExtractClientIP(t *testing.T) {
	_, loopback4, _ := net.ParseCIDR("127.0.0.0/8")
	_, loopback6, _ := net.ParseCIDR("::1/128")
	_, priv10, _ := net.ParseCIDR("10.0.0.0/8")
	trusted := []*net.IPNet{loopback4, loopback6, priv10}

	t.Run("direct untrusted connection ignores XFF", func(t *testing.T) {
		req := httptest.NewRequest("GET", "/", nil)
		req.RemoteAddr = "203.0.113.195:4321"
		req.Header.Set("X-Forwarded-For", "198.51.100.1")
		req.Header.Set("X-Real-IP", "198.51.100.2")

		ip := ExtractClientIP(req, trusted)
		if ip != "203.0.113.195" {
			t.Errorf("got %q, want 203.0.113.195", ip)
		}
	})

	t.Run("empty trusted nets ignores XFF", func(t *testing.T) {
		req := httptest.NewRequest("GET", "/", nil)
		req.RemoteAddr = "127.0.0.1:4321"
		req.Header.Set("X-Forwarded-For", "198.51.100.1")

		ip := ExtractClientIP(req, nil)
		if ip != "127.0.0.1" {
			t.Errorf("got %q, want 127.0.0.1", ip)
		}
	})

	t.Run("trusted proxy extracts untrusted client from XFF", func(t *testing.T) {
		req := httptest.NewRequest("GET", "/", nil)
		req.RemoteAddr = "127.0.0.1:4321"
		req.Header.Set("X-Forwarded-For", "203.0.113.50, 10.0.0.1")

		ip := ExtractClientIP(req, trusted)
		if ip != "203.0.113.50" {
			t.Errorf("got %q, want 203.0.113.50", ip)
		}
	})

	t.Run("trusted proxy falls back to X-Real-IP when XFF missing", func(t *testing.T) {
		req := httptest.NewRequest("GET", "/", nil)
		req.RemoteAddr = "10.0.0.2:4321"
		req.Header.Set("X-Real-IP", "198.51.100.77")

		ip := ExtractClientIP(req, trusted)
		if ip != "198.51.100.77" {
			t.Errorf("got %q, want 198.51.100.77", ip)
		}
	})
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd go && go test -v ./internal/httpserver -run TestExtractClientIP`
Expected: Compile failure (`ExtractClientIP` undefined).

- [ ] **Step 3: Implement `ExtractClientIP` in `go/internal/httpserver/realip.go`**

```go
package httpserver

import (
	"net"
	"net/http"
	"strings"
)

// ExtractClientIP returns the real client IP. If the direct remote IP is within
// trustedCIDRs, it inspects X-Forwarded-For (walking right-to-left for the first
// untrusted IP) and X-Real-IP. If direct remote IP is not trusted or trustedCIDRs
// is empty, it returns the direct connection IP to prevent spoofing.
func ExtractClientIP(r *http.Request, trustedCIDRs []*net.IPNet) string {
	remoteHost, _, err := net.SplitHostPort(r.RemoteAddr)
	if err != nil {
		remoteHost = r.RemoteAddr
	}
	directIP := net.ParseIP(remoteHost)
	if directIP == nil {
		return "unknown"
	}
	if len(trustedCIDRs) == 0 || !ipInCIDRs(directIP, trustedCIDRs) {
		return directIP.String()
	}

	if xff := r.Header.Get("X-Forwarded-For"); xff != "" {
		parts := strings.Split(xff, ",")
		for i := len(parts) - 1; i >= 0; i-- {
			candidate := strings.TrimSpace(parts[i])
			cip := net.ParseIP(candidate)
			if cip == nil {
				continue
			}
			if !ipInCIDRs(cip, trustedCIDRs) {
				return cip.String()
			}
		}
		if len(parts) > 0 {
			if first := net.ParseIP(strings.TrimSpace(parts[0])); first != nil {
				return first.String()
			}
		}
	}

	if xri := strings.TrimSpace(r.Header.Get("X-Real-IP")); xri != "" {
		if rip := net.ParseIP(xri); rip != nil {
			return rip.String()
		}
	}

	return directIP.String()
}

func ipInCIDRs(ip net.IP, cidrs []*net.IPNet) bool {
	for _, cidr := range cidrs {
		if cidr.Contains(ip) {
			return true
		}
	}
	return false
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd go && go test -v ./internal/httpserver -run TestExtractClientIP`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add go/internal/httpserver/realip.go go/internal/httpserver/realip_test.go
git commit -m "feat(httpserver): implement real client IP extraction with trusted proxies"
```

---

### Task 3: Auth Failure Metrics and Constant-Time String Comparison

**Files:**
- Modify: `go/internal/metrics/metrics.go`
- Modify: `go/internal/metrics/metrics_test.go`
- Create: `go/internal/httpserver/security.go`
- Create: `go/internal/httpserver/security_test.go`

- [ ] **Step 1: Write test for AuthFailures metric in `go/internal/metrics/metrics_test.go`**

```go
func TestAuthFailuresMetric(t *testing.T) {
	m := New(func() float64 { return 0 })
	m.AuthFailures.WithLabelValues("/mcp", "invalid_token").Inc()
	m.AuthFailures.WithLabelValues("/admin/tokens", "missing_admin_token").Inc()

	rec := httptest.NewRecorder()
	req := httptest.NewRequest("GET", "/metrics", nil)
	m.Handler().ServeHTTP(rec, req)

	body := rec.Body.String()
	if !strings.Contains(body, `mymcp_auth_failures_total{path="/mcp",reason="invalid_token"} 1`) {
		t.Errorf("metrics missing auth failure for /mcp: %s", body)
	}
	if !strings.Contains(body, `mymcp_auth_failures_total{path="/admin/tokens",reason="missing_admin_token"} 1`) {
		t.Errorf("metrics missing auth failure for /admin/tokens: %s", body)
	}
}
```

- [ ] **Step 2: Add `AuthFailures` CounterVec in `go/internal/metrics/metrics.go`**

```go
type Metrics struct {
	// ... existing fields ...
	AuthFailures *prometheus.CounterVec
}
```
In `New(...)`:
```go
	m.AuthFailures = prometheus.NewCounterVec(prometheus.CounterOpts{
		Name: "mymcp_auth_failures_total",
		Help: "Total count of failed authentication attempts.",
	}, []string{"path", "reason"})
	m.reg.MustRegister(m.AuthFailures)
```

- [ ] **Step 3: Implement `constantTimeEquals` in `go/internal/httpserver/security.go` and test in `security_test.go`**

In `go/internal/httpserver/security.go`:
```go
package httpserver

import "crypto/subtle"

// constantTimeEquals compares two strings in constant time to mitigate timing attacks.
func constantTimeEquals(a, b string) bool {
	return subtle.ConstantTimeCompare([]byte(a), []byte(b)) == 1
}
```

In `go/internal/httpserver/security_test.go`:
```go
package httpserver

import "testing"

func TestConstantTimeEquals(t *testing.T) {
	if !constantTimeEquals("secret-token-123", "secret-token-123") {
		t.Errorf("expected matching strings to return true")
	}
	if constantTimeEquals("secret-token-123", "secret-token-456") {
		t.Errorf("expected mismatching strings to return false")
	}
	if constantTimeEquals("secret", "secret-longer") {
		t.Errorf("expected different length strings to return false")
	}
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd go && go test -v ./internal/metrics && go test -v ./internal/httpserver -run TestConstantTimeEquals`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add go/internal/metrics/metrics.go go/internal/metrics/metrics_test.go go/internal/httpserver/security.go go/internal/httpserver/security_test.go
git commit -m "feat(metrics,httpserver): add auth failures counter and constant-time string comparison"
```

---

### Task 4: Structured Auth Failure Logging and Endpoint Integration

**Files:**
- Modify: `go/internal/httpserver/httpserver.go`
- Modify: `go/internal/httpserver/httpserver_test.go`

- [ ] **Step 1: Write tests in `go/internal/httpserver/httpserver_test.go` verifying `[AUTH_FAIL]` logging and zero token leakage**

```go
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
}
```

- [ ] **Step 2: Implement `recordAuthFailure` and update `BuildMux`, `authMiddleware`, `requireAdmin`, and `/metrics` in `httpserver.go`**

In `httpserver.go`:
```go
var authLogWriter io.Writer = os.Stderr

func recordAuthFailure(w http.ResponseWriter, m *metrics.Metrics, ip, path, reason, reqID string, status int, detail string) {
	if m != nil && m.AuthFailures != nil {
		m.AuthFailures.WithLabelValues(path, reason).Inc()
	}
	fmt.Fprintf(authLogWriter, "[mymcp] [AUTH_FAIL] ip=%s path=%s reason=%s req_id=%s\n", ip, path, reason, reqID)
	writeJSON(w, status, map[string]string{"detail": detail})
}
```

In `requireAdmin`:
```go
func requireAdmin(store *auth.TokenStore, m *metrics.Metrics, trusted []*net.IPNet, w http.ResponseWriter, r *http.Request) bool {
	const prefix = "Bearer "
	authz := r.Header.Get("Authorization")
	ip := ExtractClientIP(r, trusted)
	reqID := requestID(r)
	if len(authz) < len(prefix) || authz[:len(prefix)] != prefix {
		recordAuthFailure(w, m, ip, "/admin/tokens", "missing_admin_token", reqID, 401, "Missing Bearer token")
		return false
	}
	if !constantTimeEquals(authz[len(prefix):], store.AdminToken()) {
		recordAuthFailure(w, m, ip, "/admin/tokens", "invalid_admin_token", reqID, 403, "Admin token required")
		return false
	}
	return true
}
```

In `authMiddleware`:
```go
func authMiddleware(store *auth.TokenStore, m *metrics.Metrics, trusted []*net.IPNet, next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		const prefix = "Bearer "
		authz := r.Header.Get("Authorization")
		ip := ExtractClientIP(r, trusted)
		reqID := requestID(r)
		if len(authz) < len(prefix) || authz[:len(prefix)] != prefix {
			recordAuthFailure(w, m, ip, "/mcp", "missing_token", reqID, 401, "Missing Bearer token")
			return
		}
		info := store.Validate(authz[len(prefix):])
		if info == nil {
			recordAuthFailure(w, m, ip, "/mcp", "invalid_token", reqID, 401, "Invalid or disabled token")
			return
		}
		ctx := mcpserver.WithAuthInfo(r.Context(), mcpserver.AuthInfo{
			TokenName: info.Name, Role: info.Role, IP: ip, RequestID: reqID,
		})
		next.ServeHTTP(w, r.WithContext(ctx))
	})
}
```

In `/metrics` handler:
```go
		ip := ExtractClientIP(r, trusted)
		reqID := requestID(r)
		if !strings.HasPrefix(r.Header.Get("Authorization"), "Bearer ") ||
			!constantTimeEquals(r.Header.Get("Authorization")[7:], metricsToken) {
			recordAuthFailure(w, m, ip, "/metrics", "metrics_unauthorized", reqID, 401, "Unauthorized")
			return
		}
```

- [ ] **Step 3: Run tests to verify they pass**

Run: `cd go && go test -v ./internal/httpserver`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add go/internal/httpserver/httpserver.go go/internal/httpserver/httpserver_test.go
git commit -m "feat(httpserver): record structured auth failure logs and metrics with constant-time token comparison"
```

---

### Task 5: HTTP Security Headers and Request Body Size Limit Middleware

**Files:**
- Modify: `go/internal/httpserver/security.go`
- Modify: `go/internal/httpserver/security_test.go`
- Modify: `go/internal/httpserver/httpserver.go`

- [ ] **Step 1: Write test for Security Headers and Max Body Size in `go/internal/httpserver/security_test.go`**

```go
func TestSecurityHeaders(t *testing.T) {
	inner := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(200)
	})

	// Plain HTTP
	hHTTP := securityHeaders(false)(inner)
	rec := httptest.NewRecorder()
	hHTTP.ServeHTTP(rec, httptest.NewRequest("GET", "/health", nil))
	if rec.Header().Get("X-Content-Type-Options") != "nosniff" {
		t.Errorf("missing nosniff")
	}
	if rec.Header().Get("X-Frame-Options") != "DENY" {
		t.Errorf("missing DENY")
	}
	if rec.Header().Get("Strict-Transport-Security") != "" {
		t.Errorf("HSTS should not be sent on plain HTTP")
	}

	// HTTPS
	hTLS := securityHeaders(true)(inner)
	recTLS := httptest.NewRecorder()
	hTLS.ServeHTTP(recTLS, httptest.NewRequest("GET", "/health", nil))
	if recTLS.Header().Get("Strict-Transport-Security") != "max-age=31536000; includeSubDomains" {
		t.Errorf("missing HSTS on TLS")
	}
}

func TestMaxBodySize(t *testing.T) {
	handler := maxBodySize(10)(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		_, err := io.ReadAll(r.Body)
		if err != nil {
			var maxErr *http.MaxBytesError
			if errors.As(err, &maxErr) {
				writeJSON(w, http.StatusRequestEntityTooLarge, map[string]string{"detail": "Request body too large"})
				return
			}
			http.Error(w, err.Error(), 500)
			return
		}
		writeJSON(w, 200, map[string]string{"status": "ok"})
	}))

	rec := httptest.NewRecorder()
	req := httptest.NewRequest("POST", "/", strings.NewReader("123456789012345")) // 15 bytes > 10 bytes limit
	handler.ServeHTTP(rec, req)
	if rec.Code != http.StatusRequestEntityTooLarge {
		t.Fatalf("expected 413, got %d", rec.Code)
	}
}
```

- [ ] **Step 2: Implement middlewares in `security.go` and wire into `BuildMux` in `httpserver.go`**

In `go/internal/httpserver/security.go`:
```go
func securityHeaders(isTLS bool) func(http.Handler) http.Handler {
	return func(next http.Handler) http.Handler {
		return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			w.Header().Set("X-Content-Type-Options", "nosniff")
			w.Header().Set("X-Frame-Options", "DENY")
			w.Header().Set("Content-Security-Policy", "default-src 'none'")
			if isTLS {
				w.Header().Set("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
			}
			next.ServeHTTP(w, r)
		})
	}
}

func maxBodySize(limitBytes int64) func(http.Handler) http.Handler {
	return func(next http.Handler) http.Handler {
		return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			r.Body = http.MaxBytesReader(w, r.Body, limitBytes)
			next.ServeHTTP(w, r)
		})
	}
}
```

In `httpserver.go`, wrap `BuildMux` returns with `securityHeaders(d.Cfg != nil && d.Cfg.IsTLS())` and `maxBodySize(maxBytes)`.

- [ ] **Step 3: Run tests to verify they pass**

Run: `cd go && go test -v ./internal/httpserver`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add go/internal/httpserver/security.go go/internal/httpserver/security_test.go go/internal/httpserver/httpserver.go
git commit -m "feat(httpserver): add security response headers and request body limit middleware"
```

---

### Task 6: Native TLS Server Startup (`Serve`) and HTTPS Integration Test

**Files:**
- Modify: `go/internal/httpserver/httpserver.go`
- Modify: `go/internal/httpserver/httpserver_test.go`

- [ ] **Step 1: Write integration test with an ephemeral self-signed certificate in `httpserver_test.go`**

```go
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
		Handler: handler,
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
```

- [ ] **Step 2: Update `Serve()` in `go/internal/httpserver/httpserver.go`**

Configure TLS when `cfg.IsTLS()` is true:
```go
	if cfg.IsTLS() {
		if _, err := os.Stat(cfg.TLSCert); err != nil {
			return fmt.Errorf("TLS cert file: %w", err)
		}
		if _, err := os.Stat(cfg.TLSKey); err != nil {
			return fmt.Errorf("TLS key file: %w", err)
		}
		server.TLSConfig = &tls.Config{
			MinVersion: tls.VersionTLS12,
		}
		go func() { errCh <- server.ListenAndServeTLS(cfg.TLSCert, cfg.TLSKey) }()
		fmt.Fprintf(os.Stderr, "[mymcp] serving HTTPS on https://%s\n", server.Addr)
	} else {
		go func() { errCh <- server.ListenAndServe() }()
		fmt.Fprintf(os.Stderr, "[mymcp] serving HTTP on http://%s\n", server.Addr)
	}
```

- [ ] **Step 3: Run test to verify it passes**

Run: `cd go && go test -v ./internal/httpserver -run TestHTTPSServerIntegration`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add go/internal/httpserver/httpserver.go go/internal/httpserver/httpserver_test.go
git commit -m "feat(httpserver): support native TLS listening and certificate validation"
```

---

### Task 7: Doctor Diagnostics for TLS and Insecure Binds

**Files:**
- Modify: `go/internal/setup/doctor.go`
- Modify: `go/internal/setup/doctor_checks_test.go`

- [ ] **Step 1: Write tests in `go/internal/setup/doctor_checks_test.go`**

```go
func TestDoctorTLSChecks(t *testing.T) {
	// 1. Valid TLS configuration
	certFile, keyFile := generateTestCertificate(t)
	checks := tlsChecks(certFile, keyFile)
	for _, c := range checks {
		if c.Severity != SevOK {
			t.Errorf("check %s failed: %s", c.Name, c.Detail)
		}
	}

	// 2. Missing cert file
	checks = tlsChecks("/nonexistent.crt", keyFile)
	if len(checks) == 0 || checks[0].Severity != SevFail {
		t.Errorf("expected SevFail on missing cert")
	}

	// 3. Plain HTTP on 0.0.0.0 without trusted proxies
	check := bindSecurityCheck("0.0.0.0", false, "")
	if check.Severity != SevWarn {
		t.Errorf("expected SevWarn on public plain HTTP, got %v", check.Severity)
	}

	// 4. Plain HTTP on 127.0.0.1 is OK
	check = bindSecurityCheck("127.0.0.1", false, "")
	if check.Severity != SevOK {
		t.Errorf("expected SevOK on loopback plain HTTP, got %v", check.Severity)
	}
}
```

- [ ] **Step 2: Implement `tlsChecks` and `bindSecurityCheck` in `go/internal/setup/doctor.go`**

```go
func tlsChecks(certPath, keyPath string) []Check {
	var out []Check
	stCert, errCert := os.Stat(certPath)
	if errCert != nil {
		return []Check{{Group: "SECURITY", Name: "TLS certificate", Severity: SevFail,
			Detail: fmt.Sprintf("cannot read %s: %v", certPath, errCert),
			Remedy: "verify MYMCP_TLS_CERT path exists and is readable"}}
	}
	out = append(out, Check{Group: "SECURITY", Name: "TLS certificate", Severity: SevOK, Detail: certPath})

	stKey, errKey := os.Stat(keyPath)
	if errKey != nil {
		return append(out, Check{Group: "SECURITY", Name: "TLS private key", Severity: SevFail,
			Detail: fmt.Sprintf("cannot read %s: %v", keyPath, errKey),
			Remedy: "verify MYMCP_TLS_KEY path exists and is readable"})
	}
	if stKey.Mode().Perm() != 0o600 {
		out = append(out, Check{Group: "SECURITY", Name: "TLS key permissions", Severity: SevWarn,
			Detail: fmt.Sprintf("%s is mode %o; private keys should be 0600", keyPath, stKey.Mode().Perm()),
			Remedy: "chmod 600 " + keyPath})
	} else {
		out = append(out, Check{Group: "SECURITY", Name: "TLS key permissions", Severity: SevOK, Detail: "0600"})
	}

	if _, err := tls.LoadX509KeyPair(certPath, keyPath); err != nil {
		out = append(out, Check{Group: "SECURITY", Name: "TLS keypair", Severity: SevFail,
			Detail: fmt.Sprintf("invalid certificate/key pair: %v", err),
			Remedy: "ensure certificate matches the private key"})
	} else {
		out = append(out, Check{Group: "SECURITY", Name: "TLS keypair", Severity: SevOK})
	}
	return out
}

func bindSecurityCheck(host string, isTLS bool, trustedProxies string) Check {
	if isTLS || (!isWildcardHost(host) && host == "127.0.0.1") || trustedProxies != "" {
		return Check{Group: "SECURITY", Name: "transport security", Severity: SevOK, Detail: "TLS or loopback/reverse-proxy active"}
	}
	return Check{
		Group: "SECURITY", Name: "transport security", Severity: SevWarn,
		Detail: fmt.Sprintf("mymcp is serving plain HTTP on %s; bearer tokens sent over public networks can be intercepted", host),
		Remedy: "configure MYMCP_TLS_CERT/MYMCP_TLS_KEY, bind to 127.0.0.1, or configure MYMCP_TRUSTED_PROXIES behind a reverse proxy",
	}
}
```
Wire them into `Doctor()` under `// --- SECURITY ---`.

- [ ] **Step 3: Run tests to verify they pass**

Run: `cd go && go test -v ./internal/setup -run TestDoctorTLSChecks`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add go/internal/setup/doctor.go go/internal/setup/doctor_checks_test.go
git commit -m "feat(setup): add doctor diagnostic checks for TLS and public bind exposure"
```

---

### Task 8: Full Go Test Suite, Linting, and Coverage Verification

**Files:**
- Entire codebase

- [ ] **Step 1: Run format and vet checks**

Run: `cd go && gofmt -l . && go vet ./...`
Expected: Clean output (no formatting diffs, no vet errors).

- [ ] **Step 2: Run all Go unit and integration tests with coverage ratchet**

Run: `cd go && go test -cover ./...`
Expected: All packages PASS, overall statement coverage >= 78%.

- [ ] **Step 3: Run Python sidecar tests to verify zero regressions**

Run: `pytest tests/test_config.py tests/test_security.py -v --benchmark-disable`
Expected: PASS

- [ ] **Step 4: Commit any remaining polish or docs**

```bash
git add .
git commit -m "chore(security): complete security hardening verification"
```
