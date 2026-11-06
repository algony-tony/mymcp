# Security Hardening Design: Native TLS, Auth Failure Observability, and Defense-in-Depth

- **Date**: 2026-11-07
- **Branch**: `feat/security-hardening`
- **Status**: Approved for Planning
- **Scope**: Go Core Server (`go/internal/config`, `go/internal/httpserver`, `go/internal/metrics`, `go/internal/setup`)

---

## 1. Context & Motivation

`mymcp` is a Go-based Model Context Protocol (MCP) server providing Linux system tools (including arbitrary command execution via `bash_execute` for read-write tokens). In the current architecture:

1. **Cleartext Transport**: The server defaults to binding `0.0.0.0:8765` over plain HTTP without native TLS. When deployed on public networks or untrusted local networks, `Authorization: Bearer <token>` credentials can be intercepted, sniffed, or modified via Man-in-the-Middle (MITM) attacks.
2. **Missing Auth Failure Observability**: Authentication failures across `/mcp`, `/admin/tokens/*`, and `/metrics` silently respond with `401 Unauthorized` or `403 Forbidden` without emitting any log records or structured failure events. External monitoring, intrusion detection systems (IDS), SIEM, and ban systems (e.g., fail2ban) cannot detect credential brute-forcing, spray attacks, or unauthorized access attempts.
3. **Defense-in-Depth Gaps**:
   - `requireAdmin` and `/metrics` use string equality (`!=`), leaving them open to timing side-channel attacks on administrative tokens.
   - Missing HTTP security response headers (`X-Content-Type-Options`, `X-Frame-Options`, `Content-Security-Policy`, and `HSTS`).
   - Missing request body size limiting on the HTTP ingestion layer (vulnerable to unbounded memory consumption DoS).
   - In reverse-proxy setups, `r.RemoteAddr` is always the proxy (e.g. `127.0.0.1`), masking real client IPs and creating security spoofing risks if `X-Forwarded-For` is untrusted or unvalidated.

---

## 2. Requirements & Scope

### 2.1 In Scope
1. **Native TLS (HTTPS) Support**:
   - Add `MYMCP_TLS_CERT` and `MYMCP_TLS_KEY` configuration.
   - Support direct HTTPS listening with TLS 1.2+ and modern cipher suites when both cert and key are provided.
   - Abort startup with a descriptive error if only one of `MYMCP_TLS_CERT` or `MYMCP_TLS_KEY` is provided.
   - Preserve backward-compatible plain HTTP when neither is provided.
2. **Trusted Reverse-Proxy & Real IP Resolution**:
   - Add `MYMCP_TRUSTED_PROXIES` configuration accepting comma-separated CIDR notations or IP addresses (e.g., `127.0.0.1/32,::1/128,10.0.0.0/8`).
   - If direct connection (`r.RemoteAddr`) originates from a trusted proxy CIDR, parse `X-Forwarded-For` / `X-Real-IP` to extract the true client IP.
   - If direct connection is NOT from a trusted proxy, ignore forwarded headers and use `r.RemoteAddr` to prevent IP spoofing.
3. **Structured Auth Failure Logging**:
   - Standardize single-line structured logging to `stderr`:
     `[mymcp] [AUTH_FAIL] ip=<ip> path=<path> reason=<reason> req_id=<req_id>`
   - Strictly enforce **zero token disclosure**: client-supplied tokens or partial fragments MUST NOT be logged.
4. **Prometheus Auth Failure Metrics**:
   - Add `mymcp_auth_failures_total{path, reason}` counter metric in Prometheus.
5. **Timing Attack Defense**:
   - Compare `store.AdminToken()` and `metricsToken` using `crypto/subtle.ConstantTimeCompare`.
6. **HTTP Security Headers & Request Limits**:
   - Global middleware adding `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Content-Security-Policy: default-src 'none'`.
   - When TLS is active, add `Strict-Transport-Security: max-age=31536000; includeSubDomains`.
   - Protect request bodies with `http.MaxBytesReader` (default 10MB, matching `WriteFileMaxBytes`).
7. **Diagnostic Verification (`mymcp doctor`)**:
   - Validate existence, readability, and key file permissions (`0600`) when TLS is enabled.
   - Warn (`SevWarn`) when server binds to non-loopback interfaces without TLS and without trusted proxy configuration.

### 2.2 Out of Scope
- External fail2ban filter/jail package files (operators can hook into standard journald output using their own configuration).
- ACME / Let's Encrypt automated challenge solver (operators provide certificate files directly or use external tools like certbot/Caddy).

---

## 3. Architecture & Detailed Design

### 3.1 Configuration Additions (`go/internal/config/config.go`)

Extend `Config` with:
```go
type Config struct {
    // ... existing fields ...
    TLSCert        string
    TLSKey         string
    TrustedProxies string // parsed into []*net.IPNet
}
```
Resolution order matches existing patterns: `process env > .env file > default`:
- `MYMCP_TLS_CERT`: path to PEM certificate file.
- `MYMCP_TLS_KEY`: path to PEM private key file.
- `MYMCP_TRUSTED_PROXIES`: comma-separated CIDRs, e.g. `"127.0.0.1/32,::1/128"`.

Helper method on `Config`:
```go
func (c *Config) ParsedTrustedProxies() ([]*net.IPNet, error)
func (c *Config) IsTLS() bool { return c.TLSCert != "" && c.TLSKey != "" }
```

### 3.2 Client IP Extraction (`go/internal/httpserver/realip.go`)

```go
func ExtractClientIP(r *http.Request, trustedNets []*net.IPNet) string {
    directIPStr, _, err := net.SplitHostPort(r.RemoteAddr)
    if err != nil {
        directIPStr = r.RemoteAddr
    }
    directIP := net.ParseIP(directIPStr)
    if directIP == nil {
        return "unknown"
    }

    if !isIPInCIDRs(directIP, trustedNets) {
        return directIPStr
    }

    // Inspect X-Forwarded-For
    if xff := r.Header.Get("X-Forwarded-For"); xff != "" {
        parts := strings.Split(xff, ",")
        // Walk right-to-left to find first untrusted IP (standard proxy parsing)
        for i := len(parts) - 1; i >= 0; i-- {
            candidate := strings.TrimSpace(parts[i])
            cip := net.ParseIP(candidate)
            if cip != nil && !isIPInCIDRs(cip, trustedNets) {
                return candidate
            }
        }
        // If all are trusted, return leftmost
        if len(parts) > 0 {
            first := strings.TrimSpace(parts[0])
            if net.ParseIP(first) != nil {
                return first
            }
        }
    }

    // Fall back to X-Real-IP
    if xri := strings.TrimSpace(r.Header.Get("X-Real-IP")); xri != "" {
        if net.ParseIP(xri) != nil {
            return xri
        }
    }

    return directIPStr
}
```

### 3.3 Auth Failure Helper & Metrics (`go/internal/httpserver/auth.go` & `metrics.go`)

Add to `Metrics`:
```go
AuthFailures *prometheus.CounterVec // labels: "path", "reason"
```

Standardized helper:
```go
func recordAuthFailure(w http.ResponseWriter, m *metrics.Metrics, ip, path, reason, reqID string, status int, detail string) {
    if m != nil && m.AuthFailures != nil {
        m.AuthFailures.WithLabelValues(path, reason).Inc()
    }
    fmt.Fprintf(os.Stderr, "[mymcp] [AUTH_FAIL] ip=%s path=%s reason=%s req_id=%s\n", ip, path, reason, reqID)
    writeJSON(w, status, map[string]string{"detail": detail})
}
```

Standardized `reason` enum:
- `missing_token`: Authorization header missing or malformed prefix.
- `invalid_token`: Bearer token unknown or disabled.
- `missing_admin_token`: Admin endpoint hit without Bearer token.
- `invalid_admin_token`: Admin endpoint hit with incorrect or non-admin token.
- `metrics_unauthorized`: `/metrics` hit with incorrect token.
- `ticket_invalid`: Transfer ticket lookup failed or expired.

### 3.4 Timing Attack Mitigation

Use constant-time equality:
```go
func constantTimeEquals(provided, expected string) bool {
    if subtle.ConstantTimeCompare([]byte(provided), []byte(expected)) == 1 {
        return true
    }
    return false
}
```
Applied in:
- `requireAdmin(store, w, r, ...)`
- `/metrics` handler token comparison.

### 3.5 HTTP Middleware Pipeline

Wrap the handler with security middlewares:
1. `httpMetrics`: latency and status tracking.
2. `securityHeaders(isTLS)`:
   - `X-Content-Type-Options: nosniff`
   - `X-Frame-Options: DENY`
   - `Content-Security-Policy: default-src 'none'`
   - If `isTLS`: `Strict-Transport-Security: max-age=31536000; includeSubDomains`
3. `maxBodySize(maxBytes)`:
   - Wraps `r.Body = http.MaxBytesReader(w, r.Body, maxBytes)`.
4. Route-specific middlewares (e.g. `authMiddleware` for `/mcp`).

### 3.6 TLS Server Startup (`go/internal/httpserver/httpserver.go`)

In `Serve()`:
```go
if cfg.TLSCert != "" || cfg.TLSKey != "" {
    if cfg.TLSCert == "" || cfg.TLSKey == "" {
        return fmt.Errorf("both MYMCP_TLS_CERT and MYMCP_TLS_KEY must be provided")
    }
    // Verify file accessibility
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
    fmt.Fprintf(os.Stderr, "[mymcp] serving HTTPS on %s\n", server.Addr)
} else {
    go func() { errCh <- server.ListenAndServe() }()
    fmt.Fprintf(os.Stderr, "[mymcp] serving HTTP on %s\n", server.Addr)
}
```

### 3.7 Diagnostics (`go/internal/setup/doctor.go`)

In `Doctor()` checks:
1. **TLS Group**:
   - If TLS configured:
     - Check cert and key existence (`SevFail` if missing).
     - Check key permissions (`0600` expected, `SevWarn` if permission > 0600).
     - Check key pair validity via `tls.LoadX509KeyPair` (`SevFail` if invalid).
   - If TLS not configured:
     - Check if `MYMCP_HOST` is a wildcard or public IP (`0.0.0.0`, `""`, or non-loopback).
     - If public and `MYMCP_TRUSTED_PROXIES` is empty:
       - Emit `SevWarn` indicating plain HTTP is exposed on a non-loopback address without TLS or configured reverse proxy.

---

## 4. Error Handling & Invariants

- **Token Safety Invariant**: Never output received token values into logs, error responses, or metric labels under any condition.
- **Fail-Closed on TLS Misconfiguration**: If one of cert/key is specified without the other, fail immediately before binding.
- **Fail-Safe on IP Spoofing**: If headers contain invalid IPs or untrusted proxy requests, default to `r.RemoteAddr` rather than trusting arbitrary headers.
- **Audit System Isolation**: HTTP-level auth failures do not block or crash the core audit writer.

---

## 5. Verification Plan

1. **Unit Tests**:
   - `realip_test.go`: test direct IP, single trusted proxy, multiple proxy chain, untrusted proxy header discard, invalid IP formats.
   - `httpserver_test.go`:
     - Test `[AUTH_FAIL]` format emitted to `stderr` on invalid `/mcp` token.
     - Test `[AUTH_FAIL]` on `/admin/tokens` and `/metrics`.
     - Test `mymcp_auth_failures_total` counter increment.
     - Test constant-time equality check.
     - Test security headers (HSTS presence on TLS, absence on HTTP).
     - Test `MaxBytesReader` 413 rejection on oversized body.
     - Test HTTPS startup and request handling using a self-signed ephemeral cert.
2. **Doctor Tests (`doctor_test.go`)**:
   - Test TLS valid cert/key pair check (`SevOK`).
   - Test TLS bad permissions warning (`SevWarn`).
   - Test plain HTTP public bind warning (`SevWarn`).
3. **Regression & Compat**:
   - Run `cd go && go test -v ./... && go vet ./... && gofmt -l .`
   - Verify Go coverage meets floor (>=78%).
   - Run Python compat suite against Go server.
