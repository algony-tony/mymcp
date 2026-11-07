# Security Architecture & Hardening Design

## 1. Background & Threat Model

`mymcp` is a Go-based Model Context Protocol (MCP) server providing Linux system inspection and execution tools (including arbitrary command execution via `bash_execute` for read-write tokens). Because MCP clients may communicate across untrusted local networks, corporate LANs, or the public internet, transport-layer and application-layer defenses are critical.

### Threat Vectors Addressed

1. **Eavesdropping and Token Interception**:
   - Plain HTTP transmits `Authorization: Bearer <token>` in cleartext. Any intermediary (malicious router, public Wi-Fi, or ISP) can capture the token and impersonate the client.
2. **Brute Force & Credential Spraying**:
   - Unauthenticated or unauthorized requests must not fail silently. Without structured failure logs and metrics, intrusion detection systems (IDS), SIEM, and ban systems (e.g., fail2ban) cannot detect attacks.
3. **Timing Side-Channel Attacks**:
   - Standard string equality checks (`==` or `!=`) short-circuit on the first mismatched byte, leaking token character information via microsecond timing differences.
4. **Denial of Service (DoS) via Unbounded Payloads**:
   - Ingesting arbitrary request bodies without limits allows malicious clients to exhaust server memory.
5. **Client IP Spoofing via Untrusted Headers**:
   - In reverse-proxy setups, trusting arbitrary `X-Forwarded-For` or `X-Real-IP` headers from untrusted direct connections enables attackers to bypass IP whitelisting or falsify audit trails.

---

## 2. Core Security Invariants

- **SOC Red Line (Audit Failure)**: Any failure writing to the audit log returns `InternalError / 500`. Unauditable mutations are strictly forbidden.
- **Zero Token Disclosure**: Client-supplied token values, partial fragments, or hashes of failed tokens MUST NEVER appear in log files, error responses, HTTP headers, or metric labels.
- **Fail-Closed TLS Configuration**: If either `MYMCP_TLS_CERT` or `MYMCP_TLS_KEY` is provided without the other, the server aborts startup immediately with a descriptive error.
- **Fail-Safe IP Extraction**: Forwarded IP headers (`X-Forwarded-For`, `X-Real-IP`) are only parsed if the direct TCP peer (`r.RemoteAddr`) is within `MYMCP_TRUSTED_PROXIES`. Untrusted direct peers always fall back to `r.RemoteAddr`.

---

## 3. Architecture & Mechanisms

```
                     Incoming TCP Request
                              │
                              ▼
                 ┌──────────────────────────┐
                 │  TLS Termination (HTTPS) │ ◄── TLS 1.2+ / Modern Ciphers
                 └────────────┬─────────────┘     (or plain HTTP fallback)
                              │
                              ▼
                 ┌──────────────────────────┐
                 │  Security Headers        │ ◄── X-Content-Type-Options, CSP,
                 └────────────┬─────────────┘     X-Frame-Options, HSTS (TLS only)
                              │
                              ▼
                 ┌──────────────────────────┐
                 │  MaxBodySize Middleware  │ ◄── 2 * WriteFileMaxBytes + 2MB
                 └────────────┬─────────────┘     (Exempts /files/raw/ streaming)
                              │
                              ▼
                 ┌──────────────────────────┐
                 │  Real IP Extraction      │ ◄── Trusted Proxy CIDRs check
                 └────────────┬─────────────┘     (Right-to-left XFF walk)
                              │
                              ▼
                 ┌──────────────────────────┐
                 │  Auth Verification       │ ◄── crypto/subtle.ConstantTimeCompare
                 └──────┬────────────┬──────┘
                        │            │
             (Success)  │            │ (Failure)
                        ▼            ▼
                 ┌───────────┐  ┌──────────────────────────────────────────────┐
                 │  Route    │  │ recordAuthFailure:                           │
                 │  Handler  │  │  1. stderr: [mymcp] [AUTH_FAIL] ip=...       │
                 └───────────┘  │  2. metric: mymcp_auth_failures_total++      │
                                │  3. response: 401/403 with generic error     │
                                └──────────────────────────────────────────────┘
```

### 3.1 Dual-Track TLS Architecture

`mymcp` supports two secure deployment models:

#### Track A: Native TLS (Direct HTTPS)
- Configured via environment variables:
  - `MYMCP_TLS_CERT`: Path to PEM-encoded certificate chain file.
  - `MYMCP_TLS_KEY`: Path to PEM-encoded private key file.
- The server enforces `tls.VersionTLS12` as the minimum protocol version.
- Private key permissions are verified by `mymcp doctor` to be restrictive (`0600` or `0400`).

#### Track B: Reverse Proxy Termination
- External proxies (e.g., Caddy, Nginx, Cloudflare) terminate TLS and forward requests to `mymcp`.
- Configured via:
  - `MYMCP_TRUSTED_PROXIES`: Comma-separated list of CIDR blocks or IP addresses (e.g. `127.0.0.1/32,::1/128,10.0.0.0/8`).
- Only connections originating from a trusted CIDR have their `X-Forwarded-For` or `X-Real-IP` headers evaluated.

### 3.2 Real Client IP Extraction Algorithm

When logging audit events or auth failures, the client IP is extracted using a right-to-left walk of the `X-Forwarded-For` header:

1. Extract the direct peer IP from `r.RemoteAddr`.
2. Check if the direct IP matches any CIDR in `MYMCP_TRUSTED_PROXIES`:
   - If **untrusted**, ignore forwarded headers and return direct IP immediately (prevents client header spoofing).
   - If **trusted**, proceed to evaluate headers.
3. If `X-Forwarded-For` is present:
   - Split by comma into IP list.
   - Walk right-to-left: the first IP that is *not* in the trusted proxy CIDR list is returned as the client IP.
   - If all IPs in the chain are trusted, return the leftmost IP.
4. If `X-Forwarded-For` is empty, check `X-Real-IP`.
5. If no headers are present or valid, fall back to the direct peer IP.

### 3.3 Auth Failure Observability

Authentication failures across all endpoints (`/mcp`, `/admin/*`, `/metrics`, `/files/raw/*`) invoke `recordAuthFailure`:

1. **Structured Log to `stderr`**:
   Emits a single-line parseable log entry:
   ```
   [mymcp] [AUTH_FAIL] ip=<client_ip> path=<path> reason=<reason> req_id=<req_id>
   ```
   - Standardized `reason` values:
     - `missing_token`: Missing or malformed `Authorization` header.
     - `invalid_token`: Token unrecognized, revoked, or insufficient privileges.
     - `missing_admin_token`: Admin endpoint requested without credentials.
     - `invalid_admin_token`: Admin credentials incorrect.
     - `metrics_unauthorized`: `/metrics` accessed without valid metrics token.
     - `ticket_invalid`: File transfer ticket not found, expired, or invalid.
2. **Prometheus Metric Counter**:
   Increments `mymcp_auth_failures_total{path="<path>", reason="<reason>"}`.
3. **Response Sanitization**:
   Returns an RFC-compliant JSON response with a generic error detail (`{"detail": "Unauthorized"}`).

### 3.4 Defense-in-Depth Protections

#### Timing Attack Defense
All secret comparisons (admin token, metrics token, bearer tokens) use `crypto/subtle.ConstantTimeCompare`:
```go
func constantTimeEquals(provided, expected string) bool {
    return subtle.ConstantTimeCompare([]byte(provided), []byte(expected)) == 1
}
```

#### HTTP Security Headers
All HTTP responses include standard hardening headers:
- `X-Content-Type-Options: nosniff`: Prevents MIME-type sniffing.
- `X-Frame-Options: DENY`: Prevents clickjacking and framing.
- `Content-Security-Policy: default-src 'none'`: Blocks execution of injected scripts.
- `Strict-Transport-Security: max-age=31536000; includeSubDomains` (Emitted only when native TLS is active).

#### Request Body Limiting
- The global HTTP middleware wraps requests with `http.MaxBytesReader` to guard against memory exhaustion.
- **Threshold**: Minimum 20 MiB or `2 * WriteFileMaxBytes + 2 MiB` (providing headroom for JSON-RPC 2.0 escaping and framing).
- **Exemption**: Direct file streaming endpoints (`/files/raw/*`) handle their own limits up to `TransferMaxBytes` (2 GiB) without buffering in memory.

---

## 4. Diagnostics (`mymcp doctor`)

The preflight diagnostic tool `mymcp doctor` includes dedicated security checks:

1. **TLS Configuration Check**:
   - Verifies that both certificate and key files exist and are readable.
   - Verifies that the private key file permissions do not allow group or other access (requires `0600` or `0400`).
   - Verifies key pair validity using `tls.LoadX509KeyPair`.
2. **Public Bind Exposure Check**:
   - If the server binds to a non-loopback interface (e.g., `0.0.0.0` or a public IP):
     - Emits a warning (`SevWarn`) if native TLS is disabled and `MYMCP_TRUSTED_PROXIES` is not configured.
     - Directs administrators to configure TLS certificates or route traffic through a trusted reverse proxy.
