# Go Core M3a (Transfer + Admin + Overview + Token CLI) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Complete the Go core's remaining tool/HTTP surface — file `transfer` (tickets + `/files/raw` PUT/GET), the token-CRUD `admin` API, the `server_overview` tool, and offline `token` CLI subcommands — so the **full** black-box compat suite (all 9 tools) is green against both the Python and Go servers.

**Architecture:** Three MCP tools join the existing six (`prepare_upload`/`prepare_download`/`server_overview`), registered through the same `callTool` choke point and role-filtered `tools/list`. A shared in-memory `transfer.TicketStore` is minted by the `prepare_*` tools and redeemed by ticket-only (no Bearer) `/files/raw/{ticket}` streaming endpoints that write `transfer_upload`/`transfer_download` audit records directly. `/admin/tokens` (POST/DELETE/GET) sits behind an admin-token check. `server_overview` reads `<recorder_data_dir>/overview/overview.md` (the sidecar file contract) and returns the Python core's `RecorderDisabled` shape when absent.

**Tech Stack:** Go 1.25, `github.com/modelcontextprotocol/go-sdk`, `github.com/prometheus/client_golang`, stdlib (`net/http`, `crypto/rand`, `io`, `os`). Tests: `go test`; black-box compat: pytest against a live server, run against Python **and** Go in CI.

**Spec:** `docs/superpowers/specs/2026-07-04-go-core-rewrite-design.md` (milestone **M3**, code-completion half).
**Predecessor plan:** `docs/superpowers/plans/2026-07-04-go-core-m2-tools-safety.md` (merged as PR #69).
**Branch:** `feat/go-core-m3a` off master (create it).
**Scope boundary:** This is the **M3a** half — code completion + full compat green, a normal mergeable PR. The release half (**M3b**: binary-wheel pipeline, `mymcp-recorder` sidecar entry + `[recorder]` extra, `install-service`/`uninstall-service`/`doctor`/`token rotate-*`/`disable-metrics`, docs, ucloud cutover, v3.0.0) is a separate runbook driven with the user afterward — those are deployment mechanics exercised by the cutover, not by the compat gate.
**Reference implementation (read when in doubt):** `src/mymcp/transfer/tickets.py`, `src/mymcp/transfer/endpoints.py`, `src/mymcp/tools/transfer.py`, `src/mymcp/auth.py:82-197`, `src/mymcp/mcp_server.py:52-54,297-361`, `src/mymcp/tool_definitions.py:112-208`, `src/mymcp/cli.py:213-278`.

---

## Global Parity Rules (apply to every task)

- Tool descriptions, input schemas, `error` codes, and marker strings are copied byte-for-byte from the Python source (`test_tools_list.py` deep-compares description + inputSchema; `test_*` assert `error`/`success`).
- Permission sets match `src/mymcp/mcp_server.py:52-53` exactly: READ = {read_file, glob, grep, **prepare_download, server_overview**}; WRITE = {bash_execute, write_file, edit_file, **prepare_upload**}.
- Transfer endpoint JSON (`{"ok": true, ...}` / `{"ok": false, "error": code, "hint": ...}`) and HTTP status codes match `endpoints.py` exactly.
- Transfer audit records use tool names `transfer_upload`/`transfer_download` with the issuer's token/role and the param keys from `_audit_redeem` (the recorder-tailer contract: `transfer_upload ∈ MUTATING_TOOLS`).
- Admin responses/status codes match `auth.py`: create → `{token,name,role}` (400 on bad role); revoke → `{revoked}` (404 if absent); list → the tokens dict; non-admin → 401 (missing Bearer) / 403 (wrong token).

## Known, Documented Divergences (intentional; record in commit messages)

1. **`server_overview` reads a file, not an in-process supervisor.** Python couples to `_recorder_supervisor` (`mcp_server.py:333-361`); the Go core is the sidecar model (spec "File contract"): present `overview.md` → `{success:true,overview:<content>}`; absent → the exact Python `RecorderDisabled` dict. The compat gate runs the recorder **disabled** (no `overview.md`), so both servers return `RecorderDisabled` and match; the file-present path is covered by a Go unit test.
2. **Ticket IDs are `base64url(18 bytes)` via `crypto/rand`**, mirroring Python `secrets.token_urlsafe(24)` length/charset closely; the redeemer treats the ticket as opaque, so exact bytes are not a contract.
3. **No OTel spans** around transfer (as elsewhere in the Go core). Audit records are identical; only tracing is absent.

---

## File Map (what M3a creates / modifies)

```
go/
├── internal/
│   ├── config/config.go                  # MODIFY: + transfer + recorder_data_dir knobs
│   ├── config/config_test.go             # MODIFY: + defaults test
│   ├── transfer/tickets.go               # CREATE: Ticket + TicketStore
│   ├── transfer/tickets_test.go          # CREATE
│   ├── transfer/endpoints.go             # CREATE: /files/raw PUT+GET handlers
│   ├── transfer/endpoints_test.go        # CREATE
│   ├── tools/transfer.go                 # CREATE: PrepareUpload / PrepareDownload
│   ├── tools/transfer_test.go            # CREATE
│   ├── tools/overview.go                 # CREATE: ServerOverview
│   ├── tools/overview_test.go            # CREATE
│   ├── tools/readfile.go                 # MODIFY: + Tickets field on Deps
│   ├── auth/store.go                     # MODIFY: + CreateToken/RevokeToken/ListTokens
│   ├── auth/store_test.go                # MODIFY: + CRUD tests
│   ├── mcpserver/tooldefs.go             # MODIFY: + 3 tool schemas
│   ├── mcpserver/mcpserver.go            # MODIFY: permission sets + dispatch cases
│   ├── mcpserver/mcpserver_test.go       # MODIFY: 9 tools; ro sees 5 read tools
│   ├── httpserver/httpserver.go          # MODIFY: wire ticket store, /files/raw, /admin/tokens
│   ├── httpserver/httpserver_test.go     # MODIFY: admin + transfer route tests
│   └── cmd/mymcp/main.go                 # MODIFY: token list/add/revoke subcommands
tests/compat/
│   ├── conftest.py                       # MODIFY: admin_token fixture
│   ├── test_tools_list.py                # MODIFY: all 9 tools; ro read-set
│   ├── test_transfer.py                  # CREATE
│   ├── test_admin.py                     # CREATE
│   └── test_server_overview.py           # CREATE
.github/workflows/ci.yml                  # MODIFY: MYMCP_COMPAT_ADMIN_TOKEN in both compat jobs
CHANGELOG.md                              # MODIFY: Unreleased entry
```

Tool visibility after M3a: the Go registry contains **9** tools — the full set. `tools/list` is role-filtered (M2): rw sees 9, ro sees the 5 read tools.

---

## Task 0: Branch

- [ ] **Step 1: Create the M3a branch off master**

```bash
cd /home/zhu/repos/mymcp
git checkout master && git pull
git checkout -b feat/go-core-m3a
```

---

## Task 1: Config — transfer + recorder-data-dir knobs

**Files:** Modify `go/internal/config/config.go`, `go/internal/config/config_test.go`

Python defaults (`src/mymcp/config.py:75-83`): `transfer_enabled=True`, `transfer_max_bytes=2*1024*1024*1024`, `transfer_default_ttl_sec=300`, `transfer_max_ttl_sec=900`, `public_base_url=""`, `recorder_data_dir="/var/lib/mymcp/recorder"`.

- [ ] **Step 1: Write the failing test** — append to `config_test.go`:

```go
func TestLoadM3aDefaults(t *testing.T) {
	for _, k := range []string{
		"MYMCP_TRANSFER_ENABLED", "MYMCP_TRANSFER_MAX_BYTES", "MYMCP_TRANSFER_DEFAULT_TTL_SEC",
		"MYMCP_TRANSFER_MAX_TTL_SEC", "MYMCP_PUBLIC_BASE_URL", "MYMCP_RECORDER_DATA_DIR",
	} {
		t.Setenv(k, "")
		os.Unsetenv(k)
	}
	cfg, err := Load()
	if err != nil {
		t.Fatal(err)
	}
	if !cfg.TransferEnabled {
		t.Fatal("TransferEnabled default must be true")
	}
	if cfg.TransferMaxBytes != 2*1024*1024*1024 {
		t.Fatalf("TransferMaxBytes = %d", cfg.TransferMaxBytes)
	}
	if cfg.TransferDefaultTTLSec != 300 || cfg.TransferMaxTTLSec != 900 {
		t.Fatalf("ttl defaults wrong: %d %d", cfg.TransferDefaultTTLSec, cfg.TransferMaxTTLSec)
	}
	if cfg.PublicBaseURL != "" {
		t.Fatalf("PublicBaseURL default = %q", cfg.PublicBaseURL)
	}
	if cfg.RecorderDataDir != "/var/lib/mymcp/recorder" {
		t.Fatalf("RecorderDataDir = %q", cfg.RecorderDataDir)
	}
}
```

- [ ] **Step 2: Run to verify failure** — `cd go && go test ./internal/config/ -run TestLoadM3aDefaults` → build failure (`TransferEnabled` undefined).

- [ ] **Step 3: Add fields + loader lines.** Add to the `Config` struct (after the audit fields):

```go
	TransferEnabled       bool
	TransferMaxBytes      int64
	TransferDefaultTTLSec int
	TransferMaxTTLSec     int
	PublicBaseURL         string
	RecorderDataDir       string
```

In `Load()`, before the `cfg.protectedPathsCSV = …` line:

```go
	if cfg.TransferEnabled, err = getBool(get, "MYMCP_TRANSFER_ENABLED", true); err != nil {
		return nil, err
	}
	transferMax, err := getInt(get, "MYMCP_TRANSFER_MAX_BYTES", 2*1024*1024*1024)
	if err != nil {
		return nil, err
	}
	cfg.TransferMaxBytes = int64(transferMax)
	if cfg.TransferDefaultTTLSec, err = getInt(get, "MYMCP_TRANSFER_DEFAULT_TTL_SEC", 300); err != nil {
		return nil, err
	}
	if cfg.TransferMaxTTLSec, err = getInt(get, "MYMCP_TRANSFER_MAX_TTL_SEC", 900); err != nil {
		return nil, err
	}
	cfg.PublicBaseURL = getStr(get, "MYMCP_PUBLIC_BASE_URL", "")
	cfg.RecorderDataDir = getStr(get, "MYMCP_RECORDER_DATA_DIR", "/var/lib/mymcp/recorder")
```

> Note: `getInt` uses `strconv.Atoi` (host `int`, 64-bit on linux/amd64+arm64 — the only targets), so the 2 GiB default is representable; it is stored as `int64`.

- [ ] **Step 4: Run to verify pass** — `cd go && go test ./internal/config/` → PASS.

- [ ] **Step 5: Commit**

```bash
gofmt -w go/internal/config/
git add go/internal/config/
git commit -m "feat(go/config): add transfer + recorder_data_dir knobs (M3a)"
```

---

## Task 2: transfer ticket store

**Files:** Create `go/internal/transfer/tickets.go`, `go/internal/transfer/tickets_test.go`

Port `src/mymcp/transfer/tickets.py`. Single-use, TTL-bounded tickets; `Mint` sweeps expired first.

- [ ] **Step 1: Write the failing test** — create `tickets_test.go`:

```go
package transfer

import (
	"testing"
	"time"
)

func TestMintLookupConsume(t *testing.T) {
	s := NewTicketStore()
	tk := s.Mint("upload", "/tmp/x", 100, 300, "n", "rw")
	if tk.TicketID == "" || tk.Op != "upload" {
		t.Fatalf("bad ticket: %+v", tk)
	}
	if got := s.Lookup(tk.TicketID); got == nil || got.Path != "/tmp/x" {
		t.Fatalf("lookup failed: %+v", got)
	}
	if !s.Consume(tk.TicketID) {
		t.Fatal("first consume must succeed")
	}
	if s.Consume(tk.TicketID) {
		t.Fatal("second consume must fail")
	}
	if s.Lookup(tk.TicketID) != nil {
		t.Fatal("consumed ticket must not look up")
	}
}

func TestClassify(t *testing.T) {
	s := NewTicketStore()
	if s.Classify("nope") != "missing" {
		t.Fatal("missing")
	}
	tk := s.Mint("download", "/f", 1, 300, "n", "ro")
	if s.Classify(tk.TicketID) != "valid" {
		t.Fatal("valid")
	}
	s.Consume(tk.TicketID)
	if s.Classify(tk.TicketID) != "consumed" {
		t.Fatal("consumed")
	}
	exp := s.Mint("download", "/f", 1, 300, "n", "ro")
	exp.ExpiresAt = time.Now().Add(-time.Second).Unix() // reach in; test-only
	if s.Classify(exp.TicketID) != "expired" {
		t.Fatal("expired")
	}
}

func TestExpiryHidesTicket(t *testing.T) {
	s := NewTicketStore()
	tk := s.Mint("upload", "/f", 1, 0, "n", "rw") // ttl 0 → already expired
	if s.Lookup(tk.TicketID) != nil {
		t.Fatal("ttl<=0 must not be lookable")
	}
}
```

> The `exp.ExpiresAt = …` line requires `ExpiresAt` to be an exported `int64` unix seconds and the `Mint` return to be a pointer into the store (so the mutation is visible to `Classify`). Implement accordingly.

- [ ] **Step 2: Run to verify failure** — `cd go && go test ./internal/transfer/` → build failure.

- [ ] **Step 3: Implement `tickets.go`**

```go
// Package transfer implements one-shot, TTL-bounded file-transfer tickets and
// the ticket-only /files/raw streaming endpoints (port of src/mymcp/transfer).
package transfer

import (
	"crypto/rand"
	"encoding/base64"
	"sync"
	"time"
)

// Ticket grants single-use access to PUT or GET one server path.
type Ticket struct {
	TicketID      string
	Op            string // "upload" | "download"
	Path          string
	MaxBytes      int64
	ExpiresAt     int64 // unix seconds
	CreatedBy     string
	CreatedByRole string
	Consumed      bool
}

// TicketStore is a thread-safe in-memory ticket table.
type TicketStore struct {
	mu      sync.Mutex
	tickets map[string]*Ticket
}

func NewTicketStore() *TicketStore {
	return &TicketStore{tickets: map[string]*Ticket{}}
}

func newTicketID() string {
	b := make([]byte, 18)
	_, _ = rand.Read(b)
	return base64.RawURLEncoding.EncodeToString(b)
}

// Mint sweeps expired entries then inserts a fresh ticket. ttlSec<=0 yields an
// already-expired ticket (Lookup returns nil), matching Python's time math.
func (s *TicketStore) Mint(op, path string, maxBytes int64, ttlSec int, createdBy, createdByRole string) *Ticket {
	s.SweepExpired()
	tk := &Ticket{
		TicketID: newTicketID(), Op: op, Path: path, MaxBytes: maxBytes,
		ExpiresAt: time.Now().Unix() + int64(ttlSec), CreatedBy: createdBy, CreatedByRole: createdByRole,
	}
	s.mu.Lock()
	s.tickets[tk.TicketID] = tk
	s.mu.Unlock()
	return tk
}

// Lookup returns the live ticket or nil (missing, consumed, or expired).
func (s *TicketStore) Lookup(id string) *Ticket {
	s.mu.Lock()
	defer s.mu.Unlock()
	t := s.tickets[id]
	if t == nil || t.Consumed || t.ExpiresAt <= time.Now().Unix() {
		return nil
	}
	return t
}

// Classify explains why Lookup returned nil, atomically.
func (s *TicketStore) Classify(id string) string {
	s.mu.Lock()
	defer s.mu.Unlock()
	t := s.tickets[id]
	switch {
	case t == nil:
		return "missing"
	case t.Consumed:
		return "consumed"
	case t.ExpiresAt <= time.Now().Unix():
		return "expired"
	default:
		return "valid"
	}
}

// Consume marks a ticket used; false if missing/already-consumed.
func (s *TicketStore) Consume(id string) bool {
	s.mu.Lock()
	defer s.mu.Unlock()
	t := s.tickets[id]
	if t == nil || t.Consumed {
		return false
	}
	t.Consumed = true
	return true
}

// SweepExpired drops consumed/expired entries; returns the count removed.
func (s *TicketStore) SweepExpired() int {
	now := time.Now().Unix()
	s.mu.Lock()
	defer s.mu.Unlock()
	n := 0
	for id, t := range s.tickets {
		if t.Consumed || t.ExpiresAt <= now {
			delete(s.tickets, id)
			n++
		}
	}
	return n
}
```

- [ ] **Step 4: Run to verify pass** — `cd go && go test ./internal/transfer/` → PASS.

- [ ] **Step 5: Commit**

```bash
gofmt -w go/internal/transfer/
git add go/internal/transfer/
git commit -m "feat(go/transfer): single-use TTL ticket store"
```

---

## Task 3: prepare_upload / prepare_download tools

**Files:** Create `go/internal/tools/transfer.go`, `go/internal/tools/transfer_test.go`; modify `go/internal/tools/readfile.go` (add `Tickets` to `Deps`).

Port `src/mymcp/tools/transfer.py`. Tickets are minted from `Deps.Tickets`; URLs use `Cfg.PublicBaseURL` (empty → relative `/files/raw/{id}`).

- [ ] **Step 1: Add `Tickets` to `Deps`** in `readfile.go`:

```go
type Deps struct {
	Cfg       *config.Config
	Protected []fsutil.ProtectedEntry
	RgOverride string
	Tickets   *transfer.TicketStore
}
```

Add the import `"github.com/algony-tony/mymcp/go/internal/transfer"` to `readfile.go`.

- [ ] **Step 2: Write the failing test** — create `transfer_test.go`:

```go
package tools

import (
	"path/filepath"
	"strings"
	"testing"

	"github.com/algony-tony/mymcp/go/internal/transfer"
)

func transferDeps(t *testing.T) Deps {
	d := testDeps(t)
	d.Tickets = transfer.NewTicketStore()
	return d
}

func TestPrepareUploadMintsTicketAndURL(t *testing.T) {
	d := transferDeps(t)
	p := filepath.Join(t.TempDir(), "up.bin")
	res := PrepareUpload(d, p, nil, nil, true)
	if res["success"] != true || res["method"] != "PUT" {
		t.Fatalf("res = %v", res)
	}
	if !strings.HasPrefix(res["url"].(string), "/files/raw/") {
		t.Fatalf("relative url expected: %v", res["url"])
	}
	if res["dest_path"] != p {
		t.Fatalf("dest_path = %v", res["dest_path"])
	}
	if d.Tickets.Lookup(res["ticket"].(string)) == nil {
		t.Fatal("ticket not minted")
	}
}

func TestPrepareUploadRelativePathRejected(t *testing.T) {
	d := transferDeps(t)
	res := PrepareUpload(d, "rel/path", nil, nil, true)
	if res["success"] != false || res["error"] != "InvalidPath" {
		t.Fatalf("res = %v", res)
	}
}

func TestPrepareUploadPublicBaseURL(t *testing.T) {
	d := transferDeps(t)
	d.Cfg.PublicBaseURL = "https://host.example/"
	res := PrepareUpload(d, filepath.Join(t.TempDir(), "x"), nil, nil, true)
	if !strings.HasPrefix(res["url"].(string), "https://host.example/files/raw/") {
		t.Fatalf("absolute url expected: %v", res["url"])
	}
}

func TestPrepareUploadOverwriteFalse(t *testing.T) {
	d := transferDeps(t)
	p := writeTemp(t, "exists")
	res := PrepareUpload(d, p, nil, nil, false)
	if res["success"] != false || res["error"] != "FileExists" {
		t.Fatalf("res = %v", res)
	}
}

func TestPrepareDownloadMintsTicket(t *testing.T) {
	d := transferDeps(t)
	p := writeTemp(t, "payload")
	res := PrepareDownload(d, p, nil)
	if res["success"] != true || res["method"] != "GET" || res["size"] != int64(7) {
		t.Fatalf("res = %v", res)
	}
}

func TestPrepareDownloadMissing(t *testing.T) {
	d := transferDeps(t)
	res := PrepareDownload(d, filepath.Join(t.TempDir(), "nope"), nil)
	if res["success"] != false || res["error"] != "FileNotFound" {
		t.Fatalf("res = %v", res)
	}
}

func TestPrepareUploadTTLClamped(t *testing.T) {
	d := transferDeps(t)
	d.Cfg.TransferMaxTTLSec = 60
	res := PrepareUpload(d, filepath.Join(t.TempDir(), "x"), nil, intPtr(9999), true)
	if res["expires_in"] != 60 {
		t.Fatalf("ttl not clamped: %v", res["expires_in"])
	}
}

func intPtr(i int) *int { return &i }
```

- [ ] **Step 3: Run to verify failure** — `cd go && go test ./internal/tools/ -run 'Prepare'` → build failure (`PrepareUpload` undefined).

- [ ] **Step 4: Implement `tools/transfer.go`**

```go
package tools

import (
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"time"

	"github.com/algony-tony/mymcp/go/internal/fsutil"
)

func publicBaseURL(d Deps) string {
	return strings.TrimRight(d.Cfg.PublicBaseURL, "/")
}

func buildTransferURL(d Deps, ticketID string) string {
	base := publicBaseURL(d)
	if base == "" {
		return "/files/raw/" + ticketID
	}
	return base + "/files/raw/" + ticketID
}

func isoUTC(unixSec int64) string {
	return time.Unix(unixSec, 0).UTC().Format("2006-01-02T15:04:05Z")
}

// PrepareUpload ports prepare_upload: mint a single-use PUT ticket.
func PrepareUpload(d Deps, destPath string, maxBytes *int64, expiresIn *int, overwrite bool) map[string]any {
	if !d.Cfg.TransferEnabled {
		return map[string]any{"success": false, "error": "TransferDisabled",
			"message": "File transfer feature is disabled on this server."}
	}
	if !filepath.IsAbs(destPath) {
		return map[string]any{"success": false, "error": "InvalidPath",
			"message": "dest_path must be an absolute path."}
	}
	if msg := fsutil.CheckProtectedPath(destPath, fsutil.ModeWrite, d.Protected); msg != "" {
		return map[string]any{"success": false, "error": "ProtectedPath", "message": msg}
	}
	if !overwrite {
		if _, err := os.Stat(destPath); err == nil {
			return map[string]any{"success": false, "error": "FileExists",
				"message": fmt.Sprintf("%s already exists and overwrite=False.", destPath)}
		}
	}
	cap := d.Cfg.TransferMaxBytes
	requested := cap
	if maxBytes != nil {
		requested = *maxBytes
	}
	if requested <= 0 {
		return map[string]any{"success": false, "error": "InvalidMaxBytes", "message": "max_bytes must be positive."}
	}
	effectiveMax := requested
	if effectiveMax > cap {
		effectiveMax = cap
	}
	ttl := d.Cfg.TransferDefaultTTLSec
	if expiresIn != nil {
		ttl = *expiresIn
	}
	if ttl <= 0 {
		return map[string]any{"success": false, "error": "InvalidExpiresIn", "message": "expires_in must be positive."}
	}
	if ttl > d.Cfg.TransferMaxTTLSec {
		ttl = d.Cfg.TransferMaxTTLSec
	}
	tk := d.Tickets.Mint("upload", destPath, effectiveMax, ttl, "", "")
	url := buildTransferURL(d, tk.TicketID)
	return map[string]any{
		"success": true, "url": url, "method": "PUT", "ticket": tk.TicketID,
		"expires_in": ttl, "expires_at": isoUTC(tk.ExpiresAt), "max_bytes": effectiveMax,
		"dest_path": destPath,
		"curl_example": fmt.Sprintf("curl -fsS -T /local/path/to/file '%s'", url),
		"instructions": "Run the curl above from the MCP client's local shell. " +
			"The file's raw bytes go in the request body. On success the " +
			"server returns {\"ok\": true, \"path\": \"...\", \"bytes_written\": N}.",
		"on_error": "If the URL returns 4xx, read the JSON error.hint field. " +
			"Tickets are single-use; do not retry the same URL — " +
			"call prepare_upload again to mint a fresh one.",
	}
}

// PrepareDownload ports prepare_download: mint a single-use GET ticket.
func PrepareDownload(d Deps, srcPath string, expiresIn *int) map[string]any {
	if !d.Cfg.TransferEnabled {
		return map[string]any{"success": false, "error": "TransferDisabled",
			"message": "File transfer feature is disabled on this server."}
	}
	if !filepath.IsAbs(srcPath) {
		return map[string]any{"success": false, "error": "InvalidPath",
			"message": "src_path must be an absolute path."}
	}
	if msg := fsutil.CheckProtectedPath(srcPath, fsutil.ModeRead, d.Protected); msg != "" {
		return map[string]any{"success": false, "error": "ProtectedPath", "message": msg}
	}
	st, err := os.Stat(srcPath)
	if err != nil {
		return map[string]any{"success": false, "error": "FileNotFound",
			"message": fmt.Sprintf("%s does not exist.", srcPath)}
	}
	if st.IsDir() {
		return map[string]any{"success": false, "error": "NotARegularFile",
			"message": fmt.Sprintf("%s is not a regular file.", srcPath)}
	}
	ttl := d.Cfg.TransferDefaultTTLSec
	if expiresIn != nil {
		ttl = *expiresIn
	}
	if ttl <= 0 {
		return map[string]any{"success": false, "error": "InvalidExpiresIn", "message": "expires_in must be positive."}
	}
	if ttl > d.Cfg.TransferMaxTTLSec {
		ttl = d.Cfg.TransferMaxTTLSec
	}
	size := st.Size()
	tk := d.Tickets.Mint("download", srcPath, size, ttl, "", "")
	url := buildTransferURL(d, tk.TicketID)
	return map[string]any{
		"success": true, "url": url, "method": "GET", "ticket": tk.TicketID,
		"expires_in": ttl, "expires_at": isoUTC(tk.ExpiresAt), "size": size, "src_path": srcPath,
		"curl_example": fmt.Sprintf("curl -fsS '%s' -o /local/path/%s", url, filepath.Base(srcPath)),
		"instructions": "Run the curl above from the MCP client's local shell. " +
			"Bytes stream back as the response body.",
		"on_error": "If the URL returns 4xx, read the JSON error.hint field. " +
			"Tickets are single-use; mint a new one with prepare_download if needed.",
	}
}
```

> **Parity note:** `created_by`/`created_by_role` are set to the issuer at dispatch time in Task 7 (the tools receive them via a thin wrapper), not here — keep the tool functions free of context. Task 7's dispatch passes `info.TokenName`/`info.Role`; adjust the signatures there. For M3a we mint with the issuer identity by threading it through `Dispatch`; see Task 7.

- [ ] **Step 5: Run to verify pass** — `cd go && go test ./internal/tools/ -run 'Prepare'` → PASS.

- [ ] **Step 6: Commit**

```bash
gofmt -w go/internal/tools/
git add go/internal/tools/transfer.go go/internal/tools/transfer_test.go go/internal/tools/readfile.go
git commit -m "feat(go/tools): prepare_upload/prepare_download ticket-minting tools"
```

---

## Task 4: transfer HTTP endpoints (/files/raw)

**Files:** Create `go/internal/transfer/endpoints.go`, `go/internal/transfer/endpoints_test.go`

Port `src/mymcp/transfer/endpoints.py`. Ticket-only auth (no Bearer); stream with a hard byte cap; audit `transfer_upload`/`transfer_download`.

- [ ] **Step 1: Write the failing test** — create `endpoints_test.go`:

```go
package transfer

import (
	"bytes"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"testing"

	"github.com/algony-tony/mymcp/go/internal/audit"
	"github.com/algony-tony/mymcp/go/internal/fsutil"
)

func testEndpoints(t *testing.T) (*Endpoints, *TicketStore, string) {
	t.Helper()
	dir := t.TempDir()
	a, _ := audit.New(false, t.TempDir(), 1<<20, 5)
	store := NewTicketStore()
	e := &Endpoints{Tickets: store, Audit: a, Protected: nil, Enabled: true}
	return e, store, dir
}

func TestUploadRoundTrip(t *testing.T) {
	e, store, dir := testEndpoints(t)
	dst := filepath.Join(dir, "out.bin")
	tk := store.Mint("upload", dst, 1024, 300, "n", "rw")

	req := httptest.NewRequest("PUT", "/files/raw/"+tk.TicketID, bytes.NewReader([]byte("hello")))
	req.SetPathValue("ticket_id", tk.TicketID)
	rec := httptest.NewRecorder()
	e.Upload(rec, req)
	if rec.Code != 200 {
		t.Fatalf("code=%d body=%s", rec.Code, rec.Body)
	}
	got, _ := os.ReadFile(dst)
	if string(got) != "hello" {
		t.Fatalf("file=%q", got)
	}
	// Single use: the ticket is consumed by the handler, second PUT 410s.
	req2 := httptest.NewRequest("PUT", "/files/raw/"+tk.TicketID, bytes.NewReader([]byte("x")))
	req2.SetPathValue("ticket_id", tk.TicketID)
	rec2 := httptest.NewRecorder()
	e.Upload(rec2, req2)
	if rec2.Code != 410 && rec2.Code != 404 {
		t.Fatalf("reuse code=%d", rec2.Code)
	}
}

func TestUploadSizeExceeded(t *testing.T) {
	e, store, dir := testEndpoints(t)
	dst := filepath.Join(dir, "big.bin")
	tk := store.Mint("upload", dst, 3, 300, "n", "rw")
	req := httptest.NewRequest("PUT", "/files/raw/"+tk.TicketID, bytes.NewReader([]byte("toolong")))
	req.SetPathValue("ticket_id", tk.TicketID)
	rec := httptest.NewRecorder()
	e.Upload(rec, req)
	if rec.Code != 413 {
		t.Fatalf("code=%d", rec.Code)
	}
	if _, err := os.Stat(dst); !os.IsNotExist(err) {
		t.Fatal("no partial file must remain")
	}
}

func TestUploadWrongMethodTicket(t *testing.T) {
	e, store, dir := testEndpoints(t)
	tk := store.Mint("download", filepath.Join(dir, "f"), 10, 300, "n", "ro")
	req := httptest.NewRequest("PUT", "/files/raw/"+tk.TicketID, http.NoBody)
	req.SetPathValue("ticket_id", tk.TicketID)
	rec := httptest.NewRecorder()
	e.Upload(rec, req)
	if rec.Code != 405 {
		t.Fatalf("code=%d", rec.Code)
	}
}

func TestDownloadRoundTrip(t *testing.T) {
	e, store, dir := testEndpoints(t)
	src := filepath.Join(dir, "src.bin")
	os.WriteFile(src, []byte("payload"), 0o644)
	tk := store.Mint("download", src, 7, 300, "n", "ro")
	req := httptest.NewRequest("GET", "/files/raw/"+tk.TicketID, http.NoBody)
	req.SetPathValue("ticket_id", tk.TicketID)
	rec := httptest.NewRecorder()
	e.Download(rec, req)
	if rec.Code != 200 {
		t.Fatalf("code=%d", rec.Code)
	}
	body, _ := io.ReadAll(rec.Result().Body)
	if string(body) != "payload" {
		t.Fatalf("body=%q", body)
	}
	if cd := rec.Result().Header.Get("Content-Disposition"); cd == "" {
		t.Fatal("missing content-disposition")
	}
}

func TestDownloadMissingFile(t *testing.T) {
	e, store, dir := testEndpoints(t)
	tk := store.Mint("download", filepath.Join(dir, "gone"), 1, 300, "n", "ro")
	req := httptest.NewRequest("GET", "/files/raw/"+tk.TicketID, http.NoBody)
	req.SetPathValue("ticket_id", tk.TicketID)
	rec := httptest.NewRecorder()
	e.Download(rec, req)
	if rec.Code != 404 {
		t.Fatalf("code=%d", rec.Code)
	}
}

func TestDisabledReturns404(t *testing.T) {
	e, store, dir := testEndpoints(t)
	e.Enabled = false
	tk := store.Mint("upload", filepath.Join(dir, "f"), 10, 300, "n", "rw")
	req := httptest.NewRequest("PUT", "/files/raw/"+tk.TicketID, http.NoBody)
	req.SetPathValue("ticket_id", tk.TicketID)
	rec := httptest.NewRecorder()
	e.Upload(rec, req)
	if rec.Code != 404 {
		t.Fatalf("code=%d", rec.Code)
	}
}

var _ = fsutil.ModeWrite // keep the import if unused in trimmed builds
```

- [ ] **Step 2: Run to verify failure** — `cd go && go test ./internal/transfer/ -run 'Upload|Download|Disabled'` → build failure (`Endpoints` undefined).

- [ ] **Step 3: Implement `endpoints.go`**

```go
package transfer

import (
	"encoding/json"
	"io"
	"net"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"time"

	"github.com/algony-tony/mymcp/go/internal/audit"
	"github.com/algony-tony/mymcp/go/internal/fsutil"
)

// Endpoints serves the ticket-only /files/raw routes. It shares the TicketStore
// with the prepare_* tools and writes transfer_upload/transfer_download audit.
type Endpoints struct {
	Tickets   *TicketStore
	Audit     *audit.Writer
	Protected []fsutil.ProtectedEntry
	Enabled   bool
}

// Register wires PUT+GET /files/raw/{ticket_id} onto mux.
func (e *Endpoints) Register(mux *http.ServeMux) {
	mux.HandleFunc("PUT /files/raw/{ticket_id}", e.Upload)
	mux.HandleFunc("GET /files/raw/{ticket_id}", e.Download)
}

func writeErr(w http.ResponseWriter, status int, code, hint string) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(map[string]any{"ok": false, "error": code, "hint": hint})
}

func clientIP(r *http.Request) string {
	if host, _, err := net.SplitHostPort(r.RemoteAddr); err == nil {
		return host
	}
	return "unknown"
}

// resolveTicket runs the shared lookup/classify/method/consume gate. Returns the
// consumed ticket, or nil after having written the error response.
func (e *Endpoints) resolveTicket(w http.ResponseWriter, r *http.Request, wantOp string) *Ticket {
	if !e.Enabled {
		writeErr(w, 404, "transfer_disabled", "File transfer is disabled on this server.")
		return nil
	}
	id := r.PathValue("ticket_id")
	tk := e.Tickets.Lookup(id)
	if tk == nil {
		switch e.Tickets.Classify(id) {
		case "expired":
			writeErr(w, 410, "ticket_expired", "Mint a new ticket.")
		case "consumed":
			writeErr(w, 410, "ticket_not_found", "Ticket already used.")
		default:
			writeErr(w, 404, "ticket_not_found", "Mint a new ticket.")
		}
		return nil
	}
	if tk.Op != wantOp {
		if wantOp == "upload" {
			writeErr(w, 405, "wrong_method", "This ticket requires GET.")
		} else {
			writeErr(w, 405, "wrong_method", "This ticket requires PUT.")
		}
		return nil
	}
	if !e.Tickets.Consume(id) {
		writeErr(w, 410, "ticket_not_found", "Ticket already used.")
		return nil
	}
	return tk
}

func (e *Endpoints) audit(tk *Ticket, ok bool, n int64, code, ip string) {
	tool := "transfer_download"
	if tk.Op == "upload" {
		tool = "transfer_upload"
	}
	result := "ok"
	var errCode, errMsg string
	if !ok {
		result, errCode, errMsg = "error", code, code
	}
	_ = e.Audit.Log(audit.Entry{
		TS: time.Now().UTC().Format(time.RFC3339Nano), TokenName: tk.CreatedBy,
		Role: tk.CreatedByRole, IP: ip, Tool: tool, Result: result,
		ErrorCode: errCode, ErrorMessage: errMsg,
		Params: map[string]any{
			"op": tk.Op, "path": tk.Path, "ticket": firstN(tk.TicketID, 8),
			"bytes": n, "issuer_token_name": tk.CreatedBy,
			"issuer_role": tk.CreatedByRole, "redeemer_ip": ip,
		},
	})
}

func firstN(s string, n int) string {
	if len(s) <= n {
		return s
	}
	return s[:n]
}

// Upload handles PUT /files/raw/{ticket_id}.
func (e *Endpoints) Upload(w http.ResponseWriter, r *http.Request) {
	tk := e.resolveTicket(w, r, "upload")
	if tk == nil {
		return
	}
	ip := clientIP(r)
	if msg := fsutil.CheckProtectedPath(tk.Path, fsutil.ModeWrite, e.Protected); msg != "" {
		e.audit(tk, false, 0, "path_protected", ip)
		writeErr(w, 403, "path_protected", msg)
		return
	}
	if cl := r.Header.Get("Content-Length"); cl != "" {
		if n, err := strconv.ParseInt(cl, 10, 64); err != nil {
			e.audit(tk, false, 0, "bad_content_length", ip)
			writeErr(w, 400, "bad_content_length", "Content-Length is not an integer.")
			return
		} else if n > tk.MaxBytes {
			e.audit(tk, false, n, "size_exceeded", ip)
			writeErr(w, 413, "size_exceeded", "Body exceeds max_bytes="+strconv.FormatInt(tk.MaxBytes, 10)+".")
			return
		}
	}
	parent := filepath.Dir(tk.Path)
	if parent == "" {
		parent = "/"
	}
	if err := os.MkdirAll(parent, 0o755); err != nil {
		e.audit(tk, false, 0, "mkdir_failed", ip)
		writeErr(w, 500, "mkdir_failed", err.Error())
		return
	}
	tmp, err := os.CreateTemp(parent, ".mymcp-upload-*")
	if err != nil {
		e.audit(tk, false, 0, "write_failed", ip)
		writeErr(w, 500, "write_failed", err.Error())
		return
	}
	tmpPath := tmp.Name()
	written, exceeded, copyErr := copyCapped(tmp, r.Body, tk.MaxBytes)
	tmp.Close()
	if exceeded {
		os.Remove(tmpPath)
		e.audit(tk, false, written, "size_exceeded", ip)
		writeErr(w, 413, "size_exceeded", "Body exceeds max_bytes="+strconv.FormatInt(tk.MaxBytes, 10)+".")
		return
	}
	if copyErr != nil {
		os.Remove(tmpPath)
		e.audit(tk, false, written, "write_failed", ip)
		writeErr(w, 500, "write_failed", copyErr.Error())
		return
	}
	if err := os.Rename(tmpPath, tk.Path); err != nil {
		os.Remove(tmpPath)
		e.audit(tk, false, written, "write_failed", ip)
		writeErr(w, 500, "write_failed", err.Error())
		return
	}
	e.audit(tk, true, written, "", ip)
	w.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(w).Encode(map[string]any{"ok": true, "path": tk.Path, "bytes_written": written})
}

// copyCapped streams src→dst, stopping if more than limit bytes arrive.
func copyCapped(dst io.Writer, src io.Reader, limit int64) (written int64, exceeded bool, err error) {
	buf := make([]byte, 64*1024)
	for {
		n, rerr := src.Read(buf)
		if n > 0 {
			if written+int64(n) > limit {
				return written, true, nil
			}
			if _, werr := dst.Write(buf[:n]); werr != nil {
				return written, false, werr
			}
			written += int64(n)
		}
		if rerr == io.EOF {
			return written, false, nil
		}
		if rerr != nil {
			return written, false, rerr
		}
	}
}

// Download handles GET /files/raw/{ticket_id}.
func (e *Endpoints) Download(w http.ResponseWriter, r *http.Request) {
	tk := e.resolveTicket(w, r, "download")
	if tk == nil {
		return
	}
	ip := clientIP(r)
	if msg := fsutil.CheckProtectedPath(tk.Path, fsutil.ModeRead, e.Protected); msg != "" {
		e.audit(tk, false, 0, "path_protected", ip)
		writeErr(w, 403, "path_protected", msg)
		return
	}
	st, err := os.Stat(tk.Path)
	if err != nil || st.IsDir() {
		e.audit(tk, false, 0, "path_not_found", ip)
		writeErr(w, 404, "path_not_found", "Server file no longer exists.")
		return
	}
	f, err := os.Open(tk.Path)
	if err != nil {
		e.audit(tk, false, 0, "path_not_found", ip)
		writeErr(w, 404, "path_not_found", "Server file no longer exists.")
		return
	}
	defer f.Close()
	w.Header().Set("Content-Type", "application/octet-stream")
	w.Header().Set("Content-Length", strconv.FormatInt(st.Size(), 10))
	w.Header().Set("Content-Disposition", contentDisposition(filepath.Base(tk.Path)))
	sent, cerr := io.Copy(w, f)
	if cerr != nil {
		e.audit(tk, false, sent, "stream_aborted", ip)
		return
	}
	e.audit(tk, true, sent, "", ip)
}

// contentDisposition builds a safe attachment header (port of _content_disposition).
func contentDisposition(filename string) string {
	var b strings.Builder
	for _, c := range filename {
		if c >= 0x20 && c != 0x7f {
			b.WriteRune(c)
		}
	}
	safe := b.String()
	quoted := strings.ReplaceAll(safe, `\`, `\\`)
	quoted = strings.ReplaceAll(quoted, `"`, `\"`)
	asciiOnly := strings.Map(func(r rune) rune {
		if r > 127 {
			return -1
		}
		return r
	}, quoted)
	star := url.PathEscape(safe)
	return `attachment; filename="` + asciiOnly + `"; filename*=UTF-8''` + star
}
```

- [ ] **Step 4: Run to verify pass** — `cd go && go test ./internal/transfer/` → PASS.

- [ ] **Step 5: Commit**

```bash
gofmt -w go/internal/transfer/
git add go/internal/transfer/endpoints.go go/internal/transfer/endpoints_test.go
git commit -m "feat(go/transfer): /files/raw upload+download endpoints with audit"
```

---

## Task 5: server_overview tool

**Files:** Create `go/internal/tools/overview.go`, `go/internal/tools/overview_test.go`

Sidecar model: read `<RecorderDataDir>/overview/overview.md`. Absent → the exact Python `RecorderDisabled` dict (divergence #1).

- [ ] **Step 1: Write the failing test** — create `overview_test.go`:

```go
package tools

import (
	"os"
	"path/filepath"
	"testing"
)

func TestServerOverviewAbsentReturnsRecorderDisabled(t *testing.T) {
	d := testDeps(t)
	d.Cfg.RecorderDataDir = t.TempDir() // no overview/overview.md
	res := ServerOverview(d)
	if res["success"] != false || res["error"] != "RecorderDisabled" {
		t.Fatalf("res = %v", res)
	}
	if res["message"] != "server_overview requires MYMCP_RECORDER_ENABLED=true" {
		t.Fatalf("message = %v", res["message"])
	}
}

func TestServerOverviewPresentReturnsContent(t *testing.T) {
	d := testDeps(t)
	dir := t.TempDir()
	d.Cfg.RecorderDataDir = dir
	ov := filepath.Join(dir, "overview")
	os.MkdirAll(ov, 0o755)
	os.WriteFile(filepath.Join(ov, "overview.md"), []byte("# Server\nstuff\n"), 0o644)
	res := ServerOverview(d)
	if res["success"] != true || res["overview"] != "# Server\nstuff\n" {
		t.Fatalf("res = %v", res)
	}
}
```

- [ ] **Step 2: Run to verify failure** — `cd go && go test ./internal/tools/ -run ServerOverview` → build failure.

- [ ] **Step 3: Implement `overview.go`**

```go
package tools

import (
	"os"
	"path/filepath"

	"github.com/algony-tony/mymcp/go/internal/fsutil"
)

// ServerOverview returns the recorder-maintained overview. In the v3 sidecar
// model the core reads <recorder_data_dir>/overview/overview.md written by the
// mymcp-recorder process; when absent it returns the Python core's
// RecorderDisabled shape so the compat gate (recorder disabled) matches.
func ServerOverview(d Deps) map[string]any {
	path := filepath.Join(d.Cfg.RecorderDataDir, "overview", "overview.md")
	raw, err := os.ReadFile(path)
	if err != nil {
		return map[string]any{
			"success": false, "error": "RecorderDisabled",
			"message": "server_overview requires MYMCP_RECORDER_ENABLED=true",
		}
	}
	return map[string]any{"success": true, "overview": fsutil.DecodeReplace(raw)}
}
```

- [ ] **Step 4: Run to verify pass** — `cd go && go test ./internal/tools/ -run ServerOverview` → PASS.

- [ ] **Step 5: Commit**

```bash
gofmt -w go/internal/tools/
git add go/internal/tools/overview.go go/internal/tools/overview_test.go
git commit -m "feat(go/tools): server_overview reads sidecar overview.md"
```

---

## Task 6: TokenStore CRUD (admin backing)

**Files:** Modify `go/internal/auth/store.go`, `go/internal/auth/store_test.go`

Port `create_token`/`revoke_token`/`list_tokens` (`src/mymcp/auth.py:82-108`).

- [ ] **Step 1: Write the failing test** — append to `store_test.go`:

```go
func TestCreateRevokeList(t *testing.T) {
	path := filepath.Join(t.TempDir(), "tokens.json")
	s, err := NewTokenStore(path, "admin")
	if err != nil {
		t.Fatal(err)
	}
	tok, err := s.CreateToken("ci", "rw")
	if err != nil || !strings.HasPrefix(tok, "tok_") {
		t.Fatalf("create: %q %v", tok, err)
	}
	if s.Validate(tok) == nil {
		t.Fatal("created token must validate")
	}
	list := s.ListTokens()
	if info, ok := list[tok]; !ok || info.Role != "rw" || info.Name != "ci" {
		t.Fatalf("list wrong: %+v", list)
	}
	if !s.RevokeToken(tok) {
		t.Fatal("revoke must succeed")
	}
	if s.RevokeToken(tok) {
		t.Fatal("second revoke must fail")
	}
	if s.Validate(tok) != nil {
		t.Fatal("revoked token must not validate")
	}
}

func TestCreateTokenBadRole(t *testing.T) {
	s, _ := NewTokenStore(filepath.Join(t.TempDir(), "t.json"), "admin")
	if _, err := s.CreateToken("x", "superuser"); err == nil {
		t.Fatal("bad role must error")
	}
}
```

Add `"strings"` to `store_test.go` imports if not present.

- [ ] **Step 2: Run to verify failure** — `cd go && go test ./internal/auth/ -run 'CreateRevokeList|CreateTokenBadRole'` → build failure.

- [ ] **Step 3: Implement** — add to `store.go`:

```go
// CreateToken mints a persisted ro/rw token: "tok_" + 32 hex chars.
func (s *TokenStore) CreateToken(name, role string) (string, error) {
	if role != "ro" && role != "rw" {
		return "", fmt.Errorf("Invalid role: %q. Must be 'ro' or 'rw'.", role)
	}
	token, err := GenerateToken()
	if err != nil {
		return "", err
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	s.data.Tokens[token] = &TokenInfo{
		Name: name, CreatedAt: time.Now().UTC().Format(time.RFC3339Nano),
		LastUsed: nil, Enabled: true, Role: role,
	}
	if err := s.saveLocked(); err != nil {
		return "", err
	}
	return token, nil
}

// RevokeToken removes a token; false if it did not exist.
func (s *TokenStore) RevokeToken(token string) bool {
	s.mu.Lock()
	defer s.mu.Unlock()
	if _, ok := s.data.Tokens[token]; !ok {
		return false
	}
	delete(s.data.Tokens, token)
	_ = s.saveLocked()
	return true
}

// ListTokens returns a copy of the token map (values copied, not shared).
func (s *TokenStore) ListTokens() map[string]TokenInfo {
	s.mu.Lock()
	defer s.mu.Unlock()
	out := make(map[string]TokenInfo, len(s.data.Tokens))
	for tok, info := range s.data.Tokens {
		out[tok] = *info
	}
	return out
}
```

- [ ] **Step 4: Run to verify pass** — `cd go && go test ./internal/auth/` → PASS.

- [ ] **Step 5: Commit**

```bash
gofmt -w go/internal/auth/
git add go/internal/auth/
git commit -m "feat(go/auth): token CRUD (create/revoke/list) for the admin API"
```

---

## Task 7: Register 3 tools + wire transfer & admin into the server

**Files:** Modify `go/internal/mcpserver/tooldefs.go`, `mcpserver.go`, `mcpserver_test.go`, `go/internal/httpserver/httpserver.go`, `httpserver_test.go`

- [ ] **Step 1: Add the 3 tool schemas to `tooldefs.go`** (verbatim from `tool_definitions.py:112-208`), appended to the `toolDefs` slice:

```go
	{
		Name: "prepare_upload",
		Description: "Mint a one-shot ticket URL for uploading bytes to a server path.\n\n" +
			"Workflow: this tool RETURNS a ticket URL; it does NOT pull from the " +
			"client. The client must then upload via:\n" +
			"    curl -X PUT --data-binary @/local/path <ticket_url>\n" +
			"Tickets are single-use and expire (default MYMCP_TRANSFER_DEFAULT_TTL_SEC=300s).",
		SchemaJSON: `{
  "type": "object",
  "properties": {
    "dest_path": {"type": "string", "description": "Absolute server path to write to"},
    "max_bytes": {"type": "integer", "description": "Reject upload above this many bytes"},
    "expires_in": {"type": "integer", "description": "Ticket TTL seconds (default 300)"},
    "overwrite": {"type": "boolean", "description": "If false, refuse when dest_path exists (default true)"}
  },
  "required": ["dest_path"],
  "additionalProperties": false
}`,
	},
	{
		Name: "prepare_download",
		Description: "Mint a one-shot ticket URL for downloading bytes from a server path.\n\n" +
			"Workflow: this tool RETURNS a ticket URL; it does NOT push to the " +
			"client. The client must then fetch via:\n" +
			"    curl -o /local/path <ticket_url>\n" +
			"Tickets are single-use and expire (default MYMCP_TRANSFER_DEFAULT_TTL_SEC=300s).",
		SchemaJSON: `{
  "type": "object",
  "properties": {
    "src_path": {"type": "string", "description": "Absolute server path to read from"},
    "expires_in": {"type": "integer", "description": "Ticket TTL seconds (default 300)"}
  },
  "required": ["src_path"],
  "additionalProperties": false
}`,
	},
	{
		Name:        "server_overview",
		Description: "Return a maintained map of this server's services, apps, data, and recent changes.",
		SchemaJSON:  `{"type": "object", "properties": {}, "additionalProperties": false}`,
	},
```

> **Verify byte-for-byte** against `tool_definitions.py` before committing (`test_tools_list.py` deep-compares).

- [ ] **Step 2: Update permission sets + dispatch in `mcpserver.go`.** Change the two sets:

```go
var readTools = map[string]bool{"read_file": true, "glob": true, "grep": true,
	"prepare_download": true, "server_overview": true}
var writeTools = map[string]bool{"bash_execute": true, "write_file": true, "edit_file": true,
	"prepare_upload": true}
```

Thread the issuer identity into `Dispatch` so `prepare_*` can stamp the ticket. Change the `dispatchRecover`/`Dispatch` signature to accept `info AuthInfo`:

```go
func (s *Server) dispatchRecover(name string, args map[string]any, info AuthInfo) (result string, panicked bool) {
	defer func() {
		if r := recover(); r != nil {
			log.Printf("panic in tool %s: %v", name, r)
			panicked = true
		}
	}()
	return Dispatch(s.deps, name, args, info), false
}
```

Update the `callTool` call site: `resultJSON, panicked := s.dispatchRecover(name, args, info)`.

Change `Dispatch` to take `info AuthInfo` and add the three cases (place before `default:`):

```go
func Dispatch(d tools.Deps, name string, args map[string]any, info AuthInfo) string {
	...
	case "prepare_upload":
		result = tools.PrepareUpload(d, argStr(args, "dest_path", ""),
			argInt64Ptr(args, "max_bytes"), argIntPtr(args, "expires_in"),
			argBoolDefault(args, "overwrite", true))
		stampIssuer(d, info)
	case "prepare_download":
		result = tools.PrepareDownload(d, argStr(args, "src_path", ""), argIntPtr(args, "expires_in"))
		stampIssuer(d, info)
	case "server_overview":
		result = tools.ServerOverview(d)
	...
}
```

Because the ticket is minted inside `PrepareUpload`/`PrepareDownload` with empty issuer, stamp the just-minted ticket by ID. Simplest correct approach: pass issuer into the tools. **Revise Task 3's signatures** to accept `createdBy, createdByRole string` and pass them to `Mint`, and change these dispatch cases to:

```go
	case "prepare_upload":
		result = tools.PrepareUpload(d, argStr(args, "dest_path", ""),
			argInt64Ptr(args, "max_bytes"), argIntPtr(args, "expires_in"),
			argBoolDefault(args, "overwrite", true), info.TokenName, info.Role)
	case "prepare_download":
		result = tools.PrepareDownload(d, argStr(args, "src_path", ""),
			argIntPtr(args, "expires_in"), info.TokenName, info.Role)
	case "server_overview":
		result = tools.ServerOverview(d)
```

Add the arg helpers to `mcpserver.go`:

```go
func argIntPtr(args map[string]any, key string) *int {
	if v, ok := argInt(args, key); ok {
		return &v
	}
	return nil
}

func argInt64Ptr(args map[string]any, key string) *int64 {
	if v, ok := argInt(args, key); ok {
		n := int64(v)
		return &n
	}
	return nil
}

func argBoolDefault(args map[string]any, key string, def bool) bool {
	if v, ok := args[key].(bool); ok {
		return v
	}
	return def
}
```

> **Apply the Task 3 signature revision now:** `PrepareUpload(d Deps, destPath string, maxBytes *int64, expiresIn *int, overwrite bool, createdBy, createdByRole string)` and `PrepareDownload(d Deps, srcPath string, expiresIn *int, createdBy, createdByRole string)`; pass `createdBy, createdByRole` into both `d.Tickets.Mint(...)` calls (replacing the two `""` args). Update the Task 3 tests to pass `"n", "rw"` / `"n", "ro"` as the trailing args.

- [ ] **Step 3: Update `mcpserver_test.go`** — expect 9 tools and the 5-tool ro read-set:

```go
func TestToolNamesAreNine(t *testing.T) {
	names := ToolNames()
	if len(names) != 9 {
		t.Fatalf("expected 9 tools, got %d: %v", len(names), names)
	}
	want := map[string]bool{"read_file": true, "glob": true, "grep": true,
		"bash_execute": true, "write_file": true, "edit_file": true,
		"prepare_upload": true, "prepare_download": true, "server_overview": true}
	for _, n := range names {
		delete(want, n)
	}
	if len(want) != 0 {
		t.Fatalf("missing tools: %v", want)
	}
}
```

Replace the old `TestToolNamesAreSix`. In `TestBuildInProcessDeniedAndUnknown`, update the ro read-set assertion to expect the read tools present and the four write tools absent:

```go
	for _, n := range []string{"read_file", "glob", "grep", "prepare_download", "server_overview"} {
		if !seen[n] {
			t.Fatalf("ro must see read tool %q; got %v", n, seen)
		}
	}
	for _, n := range []string{"bash_execute", "write_file", "edit_file", "prepare_upload"} {
		if seen[n] {
			t.Fatalf("ro must NOT see write tool %q", n)
		}
	}
```

Any `Dispatch(...)` calls in existing tests now need the trailing `AuthInfo{}` arg, e.g. `Dispatch(d, "write_file", args, AuthInfo{})`.

- [ ] **Step 4: Wire the ticket store, /files/raw, and /admin/tokens into `httpserver.go`.**

In `BuildMux`, after building the mux and before `return httpMetrics(...)`, register transfer + admin. Change the `BuildMux` signature to accept the ticket store:

```go
func BuildMux(d tools.Deps, store *auth.TokenStore, auditW *audit.Writer, m *metrics.Metrics, metricsToken, version string) http.Handler {
	mux := http.NewServeMux()
	// ... existing /mcp, /health, /version, /metrics ...

	// Transfer endpoints (ticket-only auth, share d.Tickets with the tools).
	(&transfer.Endpoints{
		Tickets: d.Tickets, Audit: auditW, Protected: d.Protected, Enabled: d.Cfg.TransferEnabled,
	}).Register(mux)

	// Admin token CRUD behind the admin token.
	mux.HandleFunc("POST /admin/tokens", adminCreate(store))
	mux.HandleFunc("DELETE /admin/tokens/{token}", adminRevoke(store))
	mux.HandleFunc("GET /admin/tokens", adminList(store))

	return httpMetrics(m, mux)
}
```

Add the admin handlers + guard to `httpserver.go`:

```go
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
		if !store.RevokeToken(token) {
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
```

Add imports `"github.com/algony-tony/mymcp/go/internal/transfer"` to `httpserver.go`.

Create the shared ticket store in `Serve` and put it on `Deps`. After `auditW, err := audit.New(...)`:

```go
	d := tools.Deps{Cfg: cfg, Protected: tools.ProtectedFromConfig(cfg), Tickets: transfer.NewTicketStore()}
```

(Replace the existing `d := tools.Deps{...}` line.) Update the two existing `BuildMux` call sites (in `Serve` and any test helper) — the signature is unchanged except that `d` now carries `Tickets`.

- [ ] **Step 5: Update `httpserver_test.go`** — the `testMux` helper must give `d` a ticket store, and add admin + transfer route coverage:

```go
	d := tools.Deps{Cfg: cfg, Protected: tools.ProtectedFromConfig(cfg), Tickets: transfer.NewTicketStore()}
	return BuildMux(d, store, a, m, metricsToken, "test")
```

Add tests (import `"strings"`, `"bytes"`, `"github.com/algony-tony/mymcp/go/internal/transfer"` as needed):

```go
func TestAdminRequiresAdminToken(t *testing.T) {
	mux := testMux(t, "")
	// no token → 401
	rec := httptest.NewRecorder()
	mux.ServeHTTP(rec, httptest.NewRequest("GET", "/admin/tokens", nil))
	if rec.Code != 401 {
		t.Fatalf("no-token = %d", rec.Code)
	}
	// wrong token → 403
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
```

- [ ] **Step 6: Run to verify pass** — `cd go && go build ./... && go test ./... && go vet ./... && test -z "$(gofmt -l .)"` → all green.

- [ ] **Step 7: Commit**

```bash
gofmt -w go/
git add go/internal/mcpserver/ go/internal/httpserver/ go/internal/tools/transfer.go go/internal/tools/transfer_test.go
git commit -m "feat(go): register transfer + overview tools; wire /files/raw and /admin/tokens"
```

---

## Task 8: CLI token subcommands

**Files:** Modify `go/cmd/mymcp/main.go`, create `go/cmd/mymcp/main_test.go`

Port `token list/add/revoke` (`src/mymcp/cli.py:213-278`). These operate offline on `tokens.json` via the store. `rotate-*`/`disable-metrics`/`install-service`/`doctor` are **M3b** (deploy mechanics).

- [ ] **Step 1: Write the failing test** — create `main_test.go`:

```go
package main

import (
	"path/filepath"
	"testing"
)

func TestTokenAddThenList(t *testing.T) {
	tok := filepath.Join(t.TempDir(), "tokens.json")
	t.Setenv("MYMCP_TOKEN_FILE", tok)
	t.Setenv("MYMCP_ADMIN_TOKEN", "admin")
	if code := run([]string{"token", "add", "--role", "rw", "ci"}); code != 0 {
		t.Fatalf("add exit=%d", code)
	}
	if code := run([]string{"token", "list"}); code != 0 {
		t.Fatalf("list exit=%d", code)
	}
}

func TestTokenRevokeMissing(t *testing.T) {
	tok := filepath.Join(t.TempDir(), "tokens.json")
	t.Setenv("MYMCP_TOKEN_FILE", tok)
	t.Setenv("MYMCP_ADMIN_TOKEN", "admin")
	if code := run([]string{"token", "revoke", "tok_absent"}); code != 1 {
		t.Fatalf("revoke-missing exit=%d (want 1)", code)
	}
}

func TestUnknownCommand(t *testing.T) {
	if code := run([]string{"frobnicate"}); code != 2 {
		t.Fatalf("unknown exit=%d", code)
	}
}
```

- [ ] **Step 2: Run to verify failure** — `cd go && go test ./cmd/mymcp/` → the `token` command is unknown, `TestTokenAddThenList` fails (exit 2).

- [ ] **Step 3: Implement.** Add a `token` case to `run()`'s switch (before `default:`):

```go
	case "token":
		return runToken(args[1:])
```

Add to `main.go`:

```go
func loadTokenStore() (*auth.TokenStore, error) {
	cfg, err := config.Load()
	if err != nil {
		return nil, err
	}
	admin := cfg.AdminToken
	if admin == "" {
		admin = "unset" // list/add/revoke operate on the file; admin value is not consulted here
	}
	return auth.NewTokenStore(cfg.TokenFile, admin)
}

func runToken(args []string) int {
	if len(args) == 0 {
		fmt.Fprintln(os.Stderr, "usage: mymcp token {list|add|revoke}")
		return 2
	}
	switch args[0] {
	case "list":
		store, err := loadTokenStore()
		if err != nil {
			fmt.Fprintln(os.Stderr, "token:", err)
			return 1
		}
		toks := store.ListTokens()
		if len(toks) == 0 {
			fmt.Println("(no ro/rw tokens)")
			return 0
		}
		for tok, info := range toks {
			fmt.Printf("%-2s  %-20s  %s\n", info.Role, info.Name, tok)
		}
		return 0
	case "add":
		fs := flag.NewFlagSet("add", flag.ContinueOnError)
		role := fs.String("role", "ro", "token role: ro or rw")
		if err := fs.Parse(args[1:]); err != nil {
			return 2
		}
		if fs.NArg() < 1 {
			fmt.Fprintln(os.Stderr, "usage: mymcp token add [--role ro|rw] <name>")
			return 2
		}
		store, err := loadTokenStore()
		if err != nil {
			fmt.Fprintln(os.Stderr, "token:", err)
			return 1
		}
		tok, err := store.CreateToken(fs.Arg(0), *role)
		if err != nil {
			fmt.Fprintln(os.Stderr, "token:", err)
			return 1
		}
		fmt.Println(tok)
		return 0
	case "revoke":
		if len(args) < 2 {
			fmt.Fprintln(os.Stderr, "usage: mymcp token revoke <token>")
			return 2
		}
		store, err := loadTokenStore()
		if err != nil {
			fmt.Fprintln(os.Stderr, "token:", err)
			return 1
		}
		if store.RevokeToken(args[1]) {
			fmt.Printf("revoked %s\n", args[1])
			return 0
		}
		fmt.Fprintf(os.Stderr, "not found: %s\n", args[1])
		return 1
	default:
		fmt.Fprintf(os.Stderr, "unknown token subcommand: %s\n", args[0])
		return 2
	}
}
```

Add imports to `main.go`: `"github.com/algony-tony/mymcp/go/internal/auth"`, `"github.com/algony-tony/mymcp/go/internal/config"`. Update the top-level usage string to `"usage: mymcp {serve|version|token}"`.

- [ ] **Step 4: Run to verify pass** — `cd go && go test ./cmd/mymcp/` → PASS.

- [ ] **Step 5: Commit**

```bash
gofmt -w go/cmd/
git add go/cmd/mymcp/
git commit -m "feat(go/cli): token list/add/revoke subcommands"
```

---

## Task 9: Compatibility suite — transfer, admin, overview, all 9 tools

**Files:** Modify `tests/compat/conftest.py`, `test_tools_list.py`; create `test_transfer.py`, `test_admin.py`, `test_server_overview.py`

- [ ] **Step 1: Add an `admin_token` fixture to `conftest.py`.** After `AUDIT_DIR = …`:

```python
ADMIN_TOKEN = os.environ.get("MYMCP_COMPAT_ADMIN_TOKEN", "")
```

After the `audit_dir` fixture:

```python
@pytest.fixture
def admin_token() -> str:
    if not ADMIN_TOKEN:
        pytest.skip("MYMCP_COMPAT_ADMIN_TOKEN not set")
    return ADMIN_TOKEN
```

- [ ] **Step 2: Extend `test_tools_list.py` to the full set.** Replace the tuples + ro assertion:

```python
M1_TOOLS = ("read_file", "glob", "grep")
M2_WRITE_TOOLS = ("bash_execute", "write_file", "edit_file")
M3_READ_TOOLS = ("prepare_download", "server_overview")
M3_WRITE_TOOLS = ("prepare_upload",)

ALL_TOOLS = M1_TOOLS + M2_WRITE_TOOLS + M3_READ_TOOLS + M3_WRITE_TOOLS
READ_TOOLS = M1_TOOLS + M3_READ_TOOLS
WRITE_TOOLS = M2_WRITE_TOOLS + M3_WRITE_TOOLS


@pytest.mark.anyio
@pytest.mark.parametrize("name", ALL_TOOLS)
async def test_tool_present_with_exact_schema(rw, name):
    tools = {t.name: t for t in await rw.list_tools()}
    assert name in tools, f"{name} missing from tools/list"
    golden = TOOL_DEFS[name]
    got = tools[name]
    assert got.description == golden.description
    assert got.inputSchema == golden.inputSchema


@pytest.mark.anyio
async def test_ro_token_read_set(ro):
    names = {t.name for t in await ro.list_tools()}
    assert set(READ_TOOLS) <= names
    assert not (set(WRITE_TOOLS) & names), "ro must not see write tools"
```

(Remove the old `M2_WRITE_TOOLS`-only assertions replaced above.)

- [ ] **Step 3: Create `test_transfer.py`**

```python
import os

import httpx
import pytest

BASE_URL = os.environ.get("MYMCP_COMPAT_URL", "http://127.0.0.1:8765")


def _abs(url: str) -> str:
    return url if url.startswith("http") else BASE_URL + url


@pytest.mark.anyio
async def test_upload_then_download_round_trip(rw, scratch):
    dst = os.path.join(scratch, "up.bin")
    up = await rw.call("prepare_upload", {"dest_path": dst})
    assert up["success"] is True and up["method"] == "PUT"
    r = httpx.put(_abs(up["url"]), content=b"hello-bytes")
    assert r.status_code == 200 and r.json()["ok"] is True
    assert r.json()["bytes_written"] == 11
    with open(dst, "rb") as f:
        assert f.read() == b"hello-bytes"

    dn = await rw.call("prepare_download", {"src_path": dst})
    assert dn["success"] is True and dn["method"] == "GET"
    r = httpx.get(_abs(dn["url"]))
    assert r.status_code == 200 and r.content == b"hello-bytes"


@pytest.mark.anyio
async def test_ticket_single_use(rw, scratch):
    dst = os.path.join(scratch, "once.bin")
    up = await rw.call("prepare_upload", {"dest_path": dst})
    url = _abs(up["url"])
    assert httpx.put(url, content=b"a").status_code == 200
    # Reuse rejected.
    assert httpx.put(url, content=b"b").status_code in (404, 410)


@pytest.mark.anyio
async def test_upload_size_exceeded(rw, scratch):
    dst = os.path.join(scratch, "big.bin")
    up = await rw.call("prepare_upload", {"dest_path": dst, "max_bytes": 3})
    r = httpx.put(_abs(up["url"]), content=b"toolong")
    assert r.status_code == 413
    assert r.json()["error"] == "size_exceeded"


@pytest.mark.anyio
async def test_ro_cannot_prepare_upload(ro, scratch):
    res = await ro.call("prepare_upload", {"dest_path": os.path.join(scratch, "x")})
    assert res["error"] == "PermissionDenied"


@pytest.mark.anyio
async def test_ro_can_prepare_download(ro, scratch):
    # ro is allowed to mint a download ticket (prepare_download ∈ READ_TOOLS).
    p = os.path.join(scratch, "readable.txt")
    with open(p, "w") as f:
        f.write("hi")
    res = await ro.call("prepare_download", {"src_path": p})
    assert res["success"] is True
```

- [ ] **Step 4: Create `test_admin.py`**

```python
import os

import httpx
import pytest

BASE_URL = os.environ.get("MYMCP_COMPAT_URL", "http://127.0.0.1:8765")


def test_admin_requires_admin_token(admin_token):
    assert httpx.get(f"{BASE_URL}/admin/tokens").status_code == 401
    r = httpx.get(f"{BASE_URL}/admin/tokens", headers={"Authorization": "Bearer wrong"})
    assert r.status_code == 403


def test_admin_create_list_revoke(admin_token):
    h = {"Authorization": f"Bearer {admin_token}"}
    r = httpx.post(f"{BASE_URL}/admin/tokens", headers=h, json={"name": "compat-tmp", "role": "rw"})
    assert r.status_code == 200
    tok = r.json()["token"]
    assert r.json()["role"] == "rw"

    listing = httpx.get(f"{BASE_URL}/admin/tokens", headers=h).json()
    assert tok in listing

    assert httpx.delete(f"{BASE_URL}/admin/tokens/{tok}", headers=h).status_code == 200
    assert httpx.delete(f"{BASE_URL}/admin/tokens/{tok}", headers=h).status_code == 404


def test_admin_bad_role_400(admin_token):
    h = {"Authorization": f"Bearer {admin_token}"}
    r = httpx.post(f"{BASE_URL}/admin/tokens", headers=h, json={"name": "x", "role": "root"})
    assert r.status_code == 400
```

- [ ] **Step 5: Create `test_server_overview.py`**

```python
import pytest


@pytest.mark.anyio
async def test_server_overview_disabled_shape(rw):
    # Compat CI runs the recorder disabled / no overview.md, so both the Python
    # and Go servers return the RecorderDisabled shape.
    res = await rw.call("server_overview", {})
    assert res["success"] is False
    assert res["error"] == "RecorderDisabled"


@pytest.mark.anyio
async def test_server_overview_visible_to_ro(ro):
    res = await ro.call("server_overview", {})
    # ro may call it (READ tool); disabled → same shape.
    assert res["error"] == "RecorderDisabled"
```

- [ ] **Step 6: Sanity-check against the Python server**

```bash
export PATH="$PWD/.venv/bin:$PATH"
rm -rf /tmp/mymcp-compat /tmp/mymcp-compat-protected /tmp/mymcp-compat-audit
mkdir -p /tmp/mymcp-compat /tmp/mymcp-compat-protected /tmp/mymcp-compat-audit
cp tests/compat/ci-tokens.json /tmp/compat-tokens.json
MYMCP_ADMIN_TOKEN=compat-admin MYMCP_TOKEN_FILE=/tmp/compat-tokens.json \
MYMCP_PROTECTED_PATHS=/tmp/mymcp-compat-protected MYMCP_PORT=18770 \
MYMCP_METRICS_TOKEN=compat-metrics \
MYMCP_AUDIT_ENABLED=true MYMCP_AUDIT_LOG_DIR=/tmp/mymcp-compat-audit \
  mymcp serve >/tmp/py.log 2>&1 &
for i in $(seq 30); do curl -sf http://127.0.0.1:18770/health >/dev/null && break; sleep 0.5; done
MYMCP_COMPAT_URL=http://127.0.0.1:18770 \
MYMCP_COMPAT_RW_TOKEN=tok_compat_rw_0000000000000000 \
MYMCP_COMPAT_RO_TOKEN=tok_compat_ro_0000000000000000 \
MYMCP_COMPAT_METRICS_TOKEN=compat-metrics MYMCP_COMPAT_AUDIT_DIR=/tmp/mymcp-compat-audit \
MYMCP_COMPAT_ADMIN_TOKEN=compat-admin \
  pytest tests/compat/ -q --benchmark-disable
kill %1
```

Expected: all compat tests PASS against Python (proves the suite is correct). Note `server_overview` returns `RecorderDisabled` because the recorder is not enabled.

- [ ] **Step 7: Sanity-check against the Go server** — same as Step 6 but `cd go && go build -o /tmp/mymcp-go ./cmd/mymcp && cd ..` then boot `/tmp/mymcp-go serve` with a fresh `/tmp/mymcp-compat-audit`. Expected: all PASS.

- [ ] **Step 8: Commit**

```bash
git add tests/compat/
git commit -m "test(compat): transfer + admin + server_overview + all-9 tools/list"
```

---

## Task 10: CI + CHANGELOG + PR

**Files:** Modify `.github/workflows/ci.yml`, `CHANGELOG.md`

- [ ] **Step 1: Add `MYMCP_COMPAT_ADMIN_TOKEN` to both compat jobs.** In each `run compat suite` step, add the env line before `pytest`:

```yaml
          MYMCP_COMPAT_ADMIN_TOKEN=compat-admin \
```

(Both `compat-python` and `compat-go`. The server already boots with `MYMCP_ADMIN_TOKEN=compat-admin`; transfer is enabled by default so no extra boot env is needed.)

- [ ] **Step 2: CHANGELOG entry.** Under `## [Unreleased]` → `### Added`:

```markdown
- Go core M3a: file `transfer` (`prepare_upload`/`prepare_download` tools +
  ticket-only `/files/raw` streaming endpoints), the `/admin/tokens` CRUD API,
  the `server_overview` tool (reads the recorder sidecar's `overview.md`), and
  offline `mymcp token list/add/revoke` — completing the 9-tool surface. The
  full compat suite now runs green against both the Python and Go servers.
```

- [ ] **Step 3: Push and open the PR**

```bash
git add .github/workflows/ci.yml CHANGELOG.md
git commit -m "ci: admin token in compat jobs; changelog for M3a"
git push -u origin feat/go-core-m3a
gh pr create --title "Go core M3a — transfer + admin + server_overview + token CLI" \
  --body "$(cat <<'EOF'
Implements the **code-completion half of M3** (spec: docs/superpowers/specs/2026-07-04-go-core-rewrite-design.md).

- transfer: prepare_upload/prepare_download tools + ticket-only /files/raw PUT/GET streaming, single-use + TTL + size caps, transfer_upload/transfer_download audit
- admin API: POST/DELETE/GET /admin/tokens behind the admin token
- server_overview: reads the sidecar's overview.md (RecorderDisabled shape when absent)
- CLI: offline `token list/add/revoke`
- **full compat suite green against Python AND Go** — all 9 tools

Deferred to the **M3b release runbook** (needs PyPI + VPS): binary-wheel pipeline, mymcp-recorder sidecar entry + [recorder] extra, install-service/uninstall-service/doctor/rotate-*, docs, ucloud cutover, v3.0.0.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

- [ ] **Step 4: Confirm all CI checks green** — `go`, `compat-python`, `compat-go`, `test`, `lint`, `security-audit`, `build`, `mutation-smoke`. Both compat jobs green = the full drop-in surface is proven.

---

## Self-Review

**1. Spec coverage (M3 code half):**

| M3 requirement | Task |
|---|---|
| transfer tickets (single-use, TTL, size cap) | Tasks 2, 4 |
| prepare_upload / prepare_download tools | Task 3 |
| /files/raw PUT+GET, URL shape, PUBLIC_BASE_URL | Tasks 3, 4 |
| transfer_upload/transfer_download audit (tailer contract) | Task 4 |
| admin API token CRUD | Tasks 6, 7 |
| server_overview (sidecar file contract) | Task 5 |
| CLI parity (token subcommands) | Task 8 |
| permission sets (prepare_download/server_overview read; prepare_upload write) | Task 7 |
| full compat suite green (all 9) | Task 9 |
| install-service/doctor/rotate-*, wheels, sidecar, cutover | **M3b runbook (out of scope)** |

**2. Placeholder scan:** every code step contains full source. The two cross-task revisions (Task 3 `prepare_*` signatures gain `createdBy, createdByRole`; existing `Dispatch(...)` callers gain a trailing `AuthInfo`) are called out explicitly with the exact new signatures in Task 7 Step 2.

**3. Type consistency:** `transfer.NewTicketStore() *TicketStore`; `TicketStore.Mint(op,path string,maxBytes int64,ttlSec int,createdBy,createdByRole string) *Ticket`; `Ticket.ExpiresAt int64`; `tools.Deps.Tickets *transfer.TicketStore`; `tools.PrepareUpload(Deps,string,*int64,*int,bool,string,string)`; `tools.PrepareDownload(Deps,string,*int,string,string)`; `tools.ServerOverview(Deps)`; `auth.TokenStore.CreateToken(string,string)(string,error)`/`RevokeToken(string)bool`/`ListTokens()map[string]TokenInfo`; `transfer.Endpoints{Tickets,Audit,Protected,Enabled}`; `mcpserver.Dispatch(tools.Deps,string,map[string]any,AuthInfo)`; `BuildMux(tools.Deps,*auth.TokenStore,*audit.Writer,*metrics.Metrics,string,string)` (unchanged; `d` now carries `Tickets`) — all consistent across tasks.

**4. Parity edge cases** enumerated in "Known, Documented Divergences"; none affect a compat assertion (the compat gate runs the recorder disabled, so `server_overview` matches on both servers).

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-07-11-go-core-m3a-transfer-admin-overview-cli.md`. Two execution options:

**1. Subagent-Driven (recommended)** — fresh subagent per task, two-stage review between tasks. Matches M1.

**2. Inline Execution** — execute tasks in this session with checkpoints (how M2 shipped).

Which approach?
