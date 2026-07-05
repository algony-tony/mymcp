# Go Core M1 (Read-Only Core) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A deployable Go binary serving MCP over Streamable HTTP (stateless) with token auth and the three read-only tools (`read_file`, `glob`, `grep`), behavior-identical to the Python core.

**Architecture:** Single `mcp.Server` with raw-schema tools; our own permission check and role-filtered `tools/list` via SDK middleware (mirrors Python's `call_tool` central handler). Auth is an `http.Handler` middleware guarding `/mcp` that stashes token info in `context.Context`. Config is env + `.env` with pydantic-settings-compatible parsing.

**Tech Stack:** Go ≥1.24, `github.com/modelcontextprotocol/go-sdk` (v1.2+), `github.com/bmatcuk/doublestar/v4` (glob `**`), stdlib everything else. Tests: `go test`; black-box compat: pytest against a live server.

**Spec:** `docs/superpowers/specs/2026-07-04-go-core-rewrite-design.md`
**Branch:** `feat/go-core-m1` off master (create it; the spec branch merges separately).
**Reference implementation (read it when in doubt):** `src/mymcp/tools/files.py`, `src/mymcp/auth.py`, `src/mymcp/server.py`, `src/mymcp/mcp_server.py`, `src/mymcp/config.py`, `src/mymcp/cli.py:27-99`, `src/mymcp/tool_definitions.py`.

**Global parity rules (apply to every task):**
- Response JSON key names, error `error` codes, and marker strings (`" [LINE TRUNCATED]"`, `"[TRUNCATED: N more matches not shown]"`) are copied byte-for-byte from the Python source.
- Error *messages* that embed OS strerror text need not match Python word-for-word; the compat suite asserts `success:false` + `error` code, not message prose.
- All limits/defaults come from config, never hardcoded at call sites.

---

## File Map (what M1 creates)

```
go/
├── go.mod, go.sum
├── cmd/mymcp/main.go              # CLI: serve, version
└── internal/
    ├── version/version.go         # Version var (ldflags)
    ├── config/config.go           # Load(), Config struct, ProtectedPaths()
    ├── config/config_test.go
    ├── auth/store.go              # TokenStore: load/validate/flush/atomic save
    ├── auth/store_test.go
    ├── fsutil/fsutil.go           # DecodeReplace, CheckProtectedPath
    ├── fsutil/fsutil_test.go
    ├── tools/readfile.go
    ├── tools/readfile_test.go
    ├── tools/glob.go
    ├── tools/glob_test.go
    ├── tools/grep.go
    ├── tools/grep_test.go
    ├── mcpserver/tooldefs.go      # raw JSON schemas (verbatim port)
    ├── mcpserver/mcpserver.go     # Server build, dispatch, permission, middleware
    ├── mcpserver/mcpserver_test.go
    ├── httpserver/httpserver.go   # mux, auth middleware, /health /version, shutdown
    └── httpserver/httpserver_test.go
tests/compat/
    ├── conftest.py                # fixtures: base_url, tokens, mcp client session
    ├── test_tools_list.py
    ├── test_read_file.py
    ├── test_glob.py
    ├── test_grep.py
    └── test_auth_http.py
.github/workflows/ci.yml           # + go job, + compat job (python & go)
```

Note on M1 tool visibility: the Go M1 registry contains only the three read tools. `tools/list` for an rw token therefore returns 3 tools (Python returns 9). The compat suite asserts *subset* presence with exact schemas in M1; the full-set assertion is added at M3. Calling any unregistered name returns Python's shape: `{"success": false, "error": "PermissionDenied", "message": "Unknown tool: <name>"}`.

---

### Task 1: Go module skeleton, version command, CI job

**Files:**
- Create: `go/go.mod`
- Create: `go/internal/version/version.go`
- Create: `go/cmd/mymcp/main.go`
- Modify: `.github/workflows/ci.yml` (add `go` job)

- [ ] **Step 1: Create the module and version package**

```bash
mkdir -p go/cmd/mymcp go/internal/version
cd go && go mod init github.com/algony-tony/mymcp/go
```

Create `go/internal/version/version.go`:

```go
// Package version exposes the build version, injected via ldflags:
//
//	go build -ldflags "-X github.com/algony-tony/mymcp/go/internal/version.Version=v3.0.0"
package version

var Version = "dev"
```

- [ ] **Step 2: Write the failing test**

Create `go/internal/version/version_test.go`:

```go
package version

import "testing"

func TestVersionDefaultsToDev(t *testing.T) {
	if Version != "dev" {
		t.Fatalf("Version = %q, want %q", Version, "dev")
	}
}
```

Create `go/cmd/mymcp/main.go`:

```go
// Command mymcp is the Go core of the mymcp MCP server.
package main

import (
	"fmt"
	"os"

	"github.com/algony-tony/mymcp/go/internal/version"
)

func main() {
	os.Exit(run(os.Args[1:]))
}

func run(args []string) int {
	if len(args) == 0 {
		fmt.Fprintln(os.Stderr, "usage: mymcp {serve|version}")
		return 2
	}
	switch args[0] {
	case "version":
		fmt.Println("mymcp " + version.Version)
		return 0
	case "serve":
		fmt.Fprintln(os.Stderr, "serve: not implemented yet")
		return 1
	default:
		fmt.Fprintf(os.Stderr, "unknown command: %s\n", args[0])
		return 2
	}
}
```

- [ ] **Step 3: Verify build and test**

Run: `cd go && go build ./... && go test ./... && go vet ./...`
Expected: builds, 1 test passes, vet clean.
Run: `cd go && ./gofmt-check.sh 2>/dev/null || test -z "$(gofmt -l .)"`
Expected: no unformatted files (empty output → success).

- [ ] **Step 4: Add the Go job to CI**

In `.github/workflows/ci.yml`, add alongside the existing jobs (match the file's existing indentation and `runs-on`):

```yaml
  go:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v7
      - uses: actions/setup-go@v5
        with:
          go-version: "1.24"
          cache-dependency-path: go/go.sum
      - name: gofmt
        run: test -z "$(gofmt -l go/)"
      - name: vet
        run: cd go && go vet ./...
      - name: test
        run: cd go && go test ./...
```

- [ ] **Step 5: Commit**

```bash
git add go/ .github/workflows/ci.yml
git commit -m "feat(go): module skeleton, version command, CI job

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 2: Config package (env + .env, pydantic-settings-compatible)

**Files:**
- Create: `go/internal/config/config.go`
- Test: `go/internal/config/config_test.go`

Parity contract (from `src/mymcp/config.py`):
- Env file discovery order: `MYMCP_ENV_FILE` (if the file exists) → `/etc/mymcp/.env` → `./.env`. First hit wins; none is fine.
- Precedence: **process env beats .env file**.
- Bool parsing (case-insensitive): true ∈ {`true`,`1`,`yes`,`on`}, false ∈ {`false`,`0`,`no`,`off`}; anything else is a startup error naming the variable.
- Bad int: startup error naming the variable.
- `ProtectedPaths()` = `[audit_log_dir]` + comma-split of `MYMCP_PROTECTED_PATHS` (items trimmed, empties dropped).
- M1 field subset with defaults: `host`=0.0.0.0, `port`=8765, `admin_token`="", `token_file`=/etc/mymcp/tokens.json, `read_file_default_limit`=2000, `read_file_max_limit`=50000, `read_file_max_line_bytes`=32768, `glob_max_results`=1000, `grep_default_max_results`=500, `grep_max_results`=5000, `audit_log_dir`=/var/log/mymcp, `shutdown_grace_sec`=5, `protected_paths`="".

- [ ] **Step 1: Write the failing tests**

Create `go/internal/config/config_test.go`:

```go
package config

import (
	"os"
	"path/filepath"
	"testing"
)

// setEnv sets an env var for the test duration.
func setEnv(t *testing.T, k, v string) {
	t.Helper()
	t.Setenv(k, v)
}

func TestDefaults(t *testing.T) {
	cfg, err := Load()
	if err != nil {
		t.Fatal(err)
	}
	if cfg.Host != "0.0.0.0" || cfg.Port != 8765 {
		t.Fatalf("host/port = %q/%d", cfg.Host, cfg.Port)
	}
	if cfg.ReadFileDefaultLimit != 2000 || cfg.ReadFileMaxLimit != 50000 ||
		cfg.ReadFileMaxLineBytes != 32768 {
		t.Fatalf("read_file limits wrong: %+v", cfg)
	}
	if cfg.GlobMaxResults != 1000 || cfg.GrepDefaultMaxResults != 500 || cfg.GrepMaxResults != 5000 {
		t.Fatalf("glob/grep limits wrong: %+v", cfg)
	}
	if cfg.TokenFile != "/etc/mymcp/tokens.json" || cfg.AuditLogDir != "/var/log/mymcp" {
		t.Fatalf("paths wrong: %+v", cfg)
	}
	if cfg.ShutdownGraceSec != 5 {
		t.Fatalf("shutdown grace = %d", cfg.ShutdownGraceSec)
	}
}

func TestEnvOverridesDefault(t *testing.T) {
	setEnv(t, "MYMCP_PORT", "9999")
	cfg, err := Load()
	if err != nil {
		t.Fatal(err)
	}
	if cfg.Port != 9999 {
		t.Fatalf("port = %d, want 9999", cfg.Port)
	}
}

func TestEnvFileDiscoveryAndPrecedence(t *testing.T) {
	dir := t.TempDir()
	envPath := filepath.Join(dir, "custom.env")
	content := "MYMCP_PORT=7000\nMYMCP_HOST=127.0.0.1\n# comment\n\nMYMCP_ADMIN_TOKEN=fromfile\n"
	if err := os.WriteFile(envPath, []byte(content), 0o600); err != nil {
		t.Fatal(err)
	}
	setEnv(t, "MYMCP_ENV_FILE", envPath)
	// Process env must beat the file.
	setEnv(t, "MYMCP_PORT", "7001")
	cfg, err := Load()
	if err != nil {
		t.Fatal(err)
	}
	if cfg.Port != 7001 {
		t.Fatalf("process env should win: port = %d, want 7001", cfg.Port)
	}
	if cfg.Host != "127.0.0.1" || cfg.AdminToken != "fromfile" {
		t.Fatalf("file values not applied: %+v", cfg)
	}
}

func TestEnvFileQuotedValues(t *testing.T) {
	dir := t.TempDir()
	envPath := filepath.Join(dir, ".env")
	content := "MYMCP_ADMIN_TOKEN=\"quoted\"\nMYMCP_HOST='single'\n"
	if err := os.WriteFile(envPath, []byte(content), 0o600); err != nil {
		t.Fatal(err)
	}
	setEnv(t, "MYMCP_ENV_FILE", envPath)
	cfg, err := Load()
	if err != nil {
		t.Fatal(err)
	}
	if cfg.AdminToken != "quoted" || cfg.Host != "single" {
		t.Fatalf("quote stripping failed: %+v", cfg)
	}
}

func TestBadIntNamesVariable(t *testing.T) {
	setEnv(t, "MYMCP_PORT", "not-a-number")
	_, err := Load()
	if err == nil {
		t.Fatal("expected error")
	}
	if got := err.Error(); !contains(got, "MYMCP_PORT") {
		t.Fatalf("error should name the variable, got %q", got)
	}
}

func TestProtectedPathsComposition(t *testing.T) {
	setEnv(t, "MYMCP_AUDIT_LOG_DIR", "/var/log/x")
	setEnv(t, "MYMCP_PROTECTED_PATHS", " /etc/secret , ,/opt/keys ")
	cfg, err := Load()
	if err != nil {
		t.Fatal(err)
	}
	got := cfg.ProtectedPaths()
	want := []string{"/var/log/x", "/etc/secret", "/opt/keys"}
	if len(got) != len(want) {
		t.Fatalf("got %v, want %v", got, want)
	}
	for i := range want {
		if got[i] != want[i] {
			t.Fatalf("got %v, want %v", got, want)
		}
	}
}

func contains(s, sub string) bool {
	return len(s) >= len(sub) && (s == sub || len(sub) == 0 ||
		func() bool {
			for i := 0; i+len(sub) <= len(s); i++ {
				if s[i:i+len(sub)] == sub {
					return true
				}
			}
			return false
		}())
}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd go && go test ./internal/config/`
Expected: compile FAIL (`Load` undefined).

- [ ] **Step 3: Write the implementation**

Create `go/internal/config/config.go`:

```go
// Package config loads MYMCP_* settings from the process environment and an
// optional .env file, with semantics matching the Python core's
// pydantic-settings usage (src/mymcp/config.py): process env beats .env;
// discovery order MYMCP_ENV_FILE, /etc/mymcp/.env, ./.env.
package config

import (
	"bufio"
	"fmt"
	"os"
	"strconv"
	"strings"
)

type Config struct {
	Host       string
	Port       int
	AdminToken string
	TokenFile  string

	ReadFileDefaultLimit int
	ReadFileMaxLimit     int
	ReadFileMaxLineBytes int

	GlobMaxResults        int
	GrepDefaultMaxResults int
	GrepMaxResults        int

	AuditLogDir      string
	ShutdownGraceSec int

	protectedPathsCSV string
}

// Load reads configuration. Values resolve as: process env > .env file > default.
func Load() (*Config, error) {
	fileVals := map[string]string{}
	if envFile := discoverEnvFile(); envFile != "" {
		var err error
		fileVals, err = parseEnvFile(envFile)
		if err != nil {
			return nil, err
		}
	}
	get := func(key string) (string, bool) {
		if v, ok := os.LookupEnv(key); ok {
			return v, true
		}
		if v, ok := fileVals[key]; ok {
			return v, true
		}
		return "", false
	}

	cfg := &Config{}
	var err error
	cfg.Host = getStr(get, "MYMCP_HOST", "0.0.0.0")
	if cfg.Port, err = getInt(get, "MYMCP_PORT", 8765); err != nil {
		return nil, err
	}
	cfg.AdminToken = getStr(get, "MYMCP_ADMIN_TOKEN", "")
	cfg.TokenFile = getStr(get, "MYMCP_TOKEN_FILE", "/etc/mymcp/tokens.json")

	if cfg.ReadFileDefaultLimit, err = getInt(get, "MYMCP_READ_FILE_DEFAULT_LIMIT", 2000); err != nil {
		return nil, err
	}
	if cfg.ReadFileMaxLimit, err = getInt(get, "MYMCP_READ_FILE_MAX_LIMIT", 50000); err != nil {
		return nil, err
	}
	if cfg.ReadFileMaxLineBytes, err = getInt(get, "MYMCP_READ_FILE_MAX_LINE_BYTES", 32768); err != nil {
		return nil, err
	}
	if cfg.GlobMaxResults, err = getInt(get, "MYMCP_GLOB_MAX_RESULTS", 1000); err != nil {
		return nil, err
	}
	if cfg.GrepDefaultMaxResults, err = getInt(get, "MYMCP_GREP_DEFAULT_MAX_RESULTS", 500); err != nil {
		return nil, err
	}
	if cfg.GrepMaxResults, err = getInt(get, "MYMCP_GREP_MAX_RESULTS", 5000); err != nil {
		return nil, err
	}
	cfg.AuditLogDir = getStr(get, "MYMCP_AUDIT_LOG_DIR", "/var/log/mymcp")
	if cfg.ShutdownGraceSec, err = getInt(get, "MYMCP_SHUTDOWN_GRACE_SEC", 5); err != nil {
		return nil, err
	}
	cfg.protectedPathsCSV = getStr(get, "MYMCP_PROTECTED_PATHS", "")
	return cfg, nil
}

// ProtectedPaths returns the always-protected paths: the audit log dir plus
// comma-separated extras from MYMCP_PROTECTED_PATHS (trimmed, empties dropped).
func (c *Config) ProtectedPaths() []string {
	paths := []string{c.AuditLogDir}
	for _, p := range strings.Split(c.protectedPathsCSV, ",") {
		if t := strings.TrimSpace(p); t != "" {
			paths = append(paths, t)
		}
	}
	return paths
}

func discoverEnvFile() string {
	if explicit := os.Getenv("MYMCP_ENV_FILE"); explicit != "" {
		if st, err := os.Stat(explicit); err == nil && !st.IsDir() {
			return explicit
		}
	}
	for _, candidate := range []string{"/etc/mymcp/.env", ".env"} {
		if st, err := os.Stat(candidate); err == nil && !st.IsDir() {
			return candidate
		}
	}
	return ""
}

// parseEnvFile reads KEY=VALUE lines; ignores blanks and # comments; strips
// one level of matching single or double quotes around the value.
func parseEnvFile(path string) (map[string]string, error) {
	f, err := os.Open(path)
	if err != nil {
		return nil, fmt.Errorf("open env file %s: %w", path, err)
	}
	defer f.Close()
	vals := map[string]string{}
	sc := bufio.NewScanner(f)
	sc.Buffer(make([]byte, 0, 64*1024), 1024*1024)
	for sc.Scan() {
		line := strings.TrimSpace(sc.Text())
		if line == "" || strings.HasPrefix(line, "#") {
			continue
		}
		k, v, ok := strings.Cut(line, "=")
		if !ok {
			continue
		}
		k = strings.TrimSpace(k)
		v = strings.TrimSpace(v)
		if len(v) >= 2 {
			if (v[0] == '"' && v[len(v)-1] == '"') || (v[0] == '\'' && v[len(v)-1] == '\'') {
				v = v[1 : len(v)-1]
			}
		}
		vals[k] = v
	}
	return vals, sc.Err()
}

type getter func(key string) (string, bool)

func getStr(get getter, key, def string) string {
	if v, ok := get(key); ok {
		return v
	}
	return def
}

func getInt(get getter, key string, def int) (int, error) {
	v, ok := get(key)
	if !ok {
		return def, nil
	}
	n, err := strconv.Atoi(strings.TrimSpace(v))
	if err != nil {
		return 0, fmt.Errorf("%s: invalid integer %q", key, v)
	}
	return n, nil
}

// ParseBool parses the pydantic-accepted boolean spellings, case-insensitively.
// Reserved for M2 fields (audit_enabled etc.); exported now so semantics are
// pinned by tests from the start.
func ParseBool(key, v string) (bool, error) {
	switch strings.ToLower(strings.TrimSpace(v)) {
	case "true", "1", "yes", "on":
		return true, nil
	case "false", "0", "no", "off":
		return false, nil
	}
	return false, fmt.Errorf("%s: invalid boolean %q", key, v)
}
```

Also add to `config_test.go` (append at the end):

```go
func TestParseBoolSpellings(t *testing.T) {
	for _, s := range []string{"true", "TRUE", "1", "yes", "On"} {
		v, err := ParseBool("K", s)
		if err != nil || !v {
			t.Fatalf("ParseBool(%q) = %v, %v", s, v, err)
		}
	}
	for _, s := range []string{"false", "0", "No", "OFF"} {
		v, err := ParseBool("K", s)
		if err != nil || v {
			t.Fatalf("ParseBool(%q) = %v, %v", s, v, err)
		}
	}
	if _, err := ParseBool("K", "maybe"); err == nil {
		t.Fatal("expected error for 'maybe'")
	}
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd go && go test ./internal/config/ -v`
Expected: 7 PASS.

- [ ] **Step 5: Commit**

```bash
git add go/internal/config/
git commit -m "feat(go): config loading with pydantic-settings-compatible semantics

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 3: Token store (tokens.json, format-compatible)

**Files:**
- Create: `go/internal/auth/store.go`
- Test: `go/internal/auth/store_test.go`

Parity contract (from `src/mymcp/auth.py`):
- File shape: `{"tokens": {"tok_...": {"name","created_at","last_used","enabled","role"}}, "admin_token": "..."}`.
- On load: `admin_token` in the file is **overwritten** by the configured one; tokens missing `role` get `"rw"`.
- Missing file → created immediately with empty tokens.
- `Validate(token)`: nil unless present AND `enabled` true; updates `last_used` (UTC ISO-8601, microseconds) **in memory only**; returns a copy.
- `Flush()`: atomic save — write `<file>.tmp`, chmod 0600 (ignore chmod errors), rename over.
- Token generation: `"tok_" + hex(16 random bytes)` (32 hex chars).

- [ ] **Step 1: Write the failing tests**

Create `go/internal/auth/store_test.go`:

```go
package auth

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestLoadCreatesMissingFile(t *testing.T) {
	path := filepath.Join(t.TempDir(), "sub", "tokens.json")
	st, err := NewTokenStore(path, "admin-secret")
	if err != nil {
		t.Fatal(err)
	}
	if _, err := os.Stat(path); err != nil {
		t.Fatalf("file not created: %v", err)
	}
	if st.AdminToken() != "admin-secret" {
		t.Fatal("admin token not set")
	}
}

func TestLoadOverridesAdminAndDefaultsRole(t *testing.T) {
	path := filepath.Join(t.TempDir(), "tokens.json")
	seed := `{"tokens": {"tok_abc": {"name": "old", "created_at": "x", "last_used": null, "enabled": true}}, "admin_token": "stale"}`
	if err := os.WriteFile(path, []byte(seed), 0o600); err != nil {
		t.Fatal(err)
	}
	st, err := NewTokenStore(path, "fresh-admin")
	if err != nil {
		t.Fatal(err)
	}
	if st.AdminToken() != "fresh-admin" {
		t.Fatal("admin_token must come from config, not file")
	}
	info := st.Validate("tok_abc")
	if info == nil || info.Role != "rw" {
		t.Fatalf("missing role must default to rw, got %+v", info)
	}
}

func TestValidateRejectsDisabledAndUnknown(t *testing.T) {
	path := filepath.Join(t.TempDir(), "tokens.json")
	seed := `{"tokens": {"tok_off": {"name": "n", "created_at": "x", "last_used": null, "enabled": false, "role": "rw"}}, "admin_token": ""}`
	if err := os.WriteFile(path, []byte(seed), 0o600); err != nil {
		t.Fatal(err)
	}
	st, err := NewTokenStore(path, "a")
	if err != nil {
		t.Fatal(err)
	}
	if st.Validate("tok_off") != nil {
		t.Fatal("disabled token must be rejected")
	}
	if st.Validate("tok_nope") != nil {
		t.Fatal("unknown token must be rejected")
	}
}

func TestValidateUpdatesLastUsedInMemoryFlushPersists(t *testing.T) {
	path := filepath.Join(t.TempDir(), "tokens.json")
	seed := `{"tokens": {"tok_a": {"name": "n", "created_at": "x", "last_used": null, "enabled": true, "role": "ro"}}, "admin_token": ""}`
	if err := os.WriteFile(path, []byte(seed), 0o600); err != nil {
		t.Fatal(err)
	}
	st, err := NewTokenStore(path, "a")
	if err != nil {
		t.Fatal(err)
	}
	if info := st.Validate("tok_a"); info == nil || info.LastUsed == nil {
		t.Fatal("last_used must be set after Validate")
	}
	// Disk copy untouched until Flush.
	raw, _ := os.ReadFile(path)
	if strings.Contains(string(raw), "T") && !strings.Contains(string(raw), "null") {
		t.Fatal("disk must still have last_used null before Flush")
	}
	if err := st.Flush(); err != nil {
		t.Fatal(err)
	}
	raw, _ = os.ReadFile(path)
	var disk struct {
		Tokens map[string]TokenInfo `json:"tokens"`
	}
	if err := json.Unmarshal(raw, &disk); err != nil {
		t.Fatal(err)
	}
	if disk.Tokens["tok_a"].LastUsed == nil {
		t.Fatal("Flush must persist last_used")
	}
	st2, err := NewTokenStore(path, "a")
	if err != nil {
		t.Fatal(err)
	}
	if st2.Validate("tok_a") == nil {
		t.Fatal("round-trip load must keep token valid")
	}
}

func TestFlushSetsRestrictivePerms(t *testing.T) {
	path := filepath.Join(t.TempDir(), "tokens.json")
	st, err := NewTokenStore(path, "a")
	if err != nil {
		t.Fatal(err)
	}
	if err := st.Flush(); err != nil {
		t.Fatal(err)
	}
	fi, err := os.Stat(path)
	if err != nil {
		t.Fatal(err)
	}
	if fi.Mode().Perm() != 0o600 {
		t.Fatalf("perm = %o, want 600", fi.Mode().Perm())
	}
}

func TestAddEphemeralToken(t *testing.T) {
	path := filepath.Join(t.TempDir(), "tokens.json")
	st, err := NewTokenStore(path, "a")
	if err != nil {
		t.Fatal(err)
	}
	st.AddEphemeral("tok_temp123", "temp-rw", "rw")
	info := st.Validate("tok_temp123")
	if info == nil || info.Role != "rw" || info.Name != "temp-rw" {
		t.Fatalf("ephemeral token broken: %+v", info)
	}
	if info.CreatedAt != "ephemeral" {
		t.Fatalf("created_at = %q, want ephemeral", info.CreatedAt)
	}
}

func TestGenerateTokenShape(t *testing.T) {
	tok, err := GenerateToken()
	if err != nil {
		t.Fatal(err)
	}
	if !strings.HasPrefix(tok, "tok_") || len(tok) != 4+32 {
		t.Fatalf("token shape wrong: %q", tok)
	}
}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd go && go test ./internal/auth/`
Expected: compile FAIL (`NewTokenStore` undefined).

- [ ] **Step 3: Write the implementation**

Create `go/internal/auth/store.go`:

```go
// Package auth implements the tokens.json-backed token store, format-compatible
// with the Python core (src/mymcp/auth.py): admin_token always comes from
// config, missing roles default to rw, last_used updates in memory and is
// persisted only by Flush (called at shutdown).
package auth

import (
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"sync"
	"time"
)

type TokenInfo struct {
	Name      string  `json:"name"`
	CreatedAt string  `json:"created_at"`
	LastUsed  *string `json:"last_used"`
	Enabled   bool    `json:"enabled"`
	Role      string  `json:"role"`
}

type storeData struct {
	Tokens     map[string]*TokenInfo `json:"tokens"`
	AdminToken string                `json:"admin_token"`
}

type TokenStore struct {
	path string
	mu   sync.Mutex
	data storeData
}

func NewTokenStore(path, adminToken string) (*TokenStore, error) {
	st := &TokenStore{path: path}
	st.data = storeData{Tokens: map[string]*TokenInfo{}, AdminToken: adminToken}
	raw, err := os.ReadFile(path)
	switch {
	case err == nil:
		if err := json.Unmarshal(raw, &st.data); err != nil {
			return nil, fmt.Errorf("parse %s: %w", path, err)
		}
		st.data.AdminToken = adminToken // config wins over file
		if st.data.Tokens == nil {
			st.data.Tokens = map[string]*TokenInfo{}
		}
		for _, info := range st.data.Tokens {
			if info.Role == "" {
				info.Role = "rw" // backward compat, same as Python _load
			}
		}
	case os.IsNotExist(err):
		if err := st.saveLocked(); err != nil {
			return nil, err
		}
	default:
		return nil, err
	}
	return st, nil
}

func (s *TokenStore) AdminToken() string { return s.data.AdminToken }

// Validate returns a copy of the token info if the token exists and is
// enabled, else nil. last_used is bumped in memory only.
func (s *TokenStore) Validate(token string) *TokenInfo {
	s.mu.Lock()
	defer s.mu.Unlock()
	info, ok := s.data.Tokens[token]
	if !ok || !info.Enabled {
		return nil
	}
	now := time.Now().UTC().Format("2006-01-02T15:04:05.000000-07:00")
	info.LastUsed = &now
	cp := *info
	return &cp
}

// AddEphemeral registers an in-memory token (the temp-rw token printed by
// `serve` when nothing is configured). created_at is the literal "ephemeral",
// matching the Python CLI.
func (s *TokenStore) AddEphemeral(token, name, role string) {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.data.Tokens[token] = &TokenInfo{
		Name: name, CreatedAt: "ephemeral", LastUsed: nil, Enabled: true, Role: role,
	}
}

// Flush persists in-memory state atomically: tmp file + chmod 0600 + rename.
func (s *TokenStore) Flush() error {
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.saveLocked()
}

func (s *TokenStore) saveLocked() error {
	if err := os.MkdirAll(filepath.Dir(s.path), 0o755); err != nil {
		return err
	}
	raw, err := json.MarshalIndent(s.data, "", "  ")
	if err != nil {
		return err
	}
	tmp := s.path + ".tmp"
	if err := os.WriteFile(tmp, raw, 0o600); err != nil {
		return err
	}
	_ = os.Chmod(tmp, 0o600) // best-effort, as in Python
	if err := os.Rename(tmp, s.path); err != nil {
		os.Remove(tmp)
		return err
	}
	return nil
}

// GenerateToken returns "tok_" + 32 hex chars (16 random bytes), the same
// shape the Python core mints.
func GenerateToken() (string, error) {
	b := make([]byte, 16)
	if _, err := rand.Read(b); err != nil {
		return "", err
	}
	return "tok_" + hex.EncodeToString(b), nil
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd go && go test ./internal/auth/ -v`
Expected: 7 PASS.

- [ ] **Step 5: Commit**

```bash
git add go/internal/auth/
git commit -m "feat(go): tokens.json-compatible token store

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 4: fsutil — protected paths and UTF-8 replacement decoding

**Files:**
- Create: `go/internal/fsutil/fsutil.go`
- Test: `go/internal/fsutil/fsutil_test.go`

Parity contract (from `src/mymcp/tools/files.py:56-77`):
- `CheckProtectedPath(path, mode)`: resolve symlinks on **both** the candidate and each protected pattern (`os.path.realpath` ≈ `filepath.EvalSymlinks` on the deepest existing ancestor + `filepath.Abs`); protected if equal or candidate is under `protected + "/"`. Legacy (config) paths block both `read` and `write`. Returns the exact message `"Access denied: path is within protected directory <pattern>"` (pattern as configured, not resolved) or `""`.
- `DecodeReplace(b)`: UTF-8 decode where **each invalid byte** becomes U+FFFD (Python `errors="replace"` semantics — NOT Go's `strings.ToValidUTF8`, which collapses runs).

Python's `realpath` never fails on nonexistent paths (resolves the existing prefix, appends the rest). Go's `EvalSymlinks` errors on nonexistent paths — implement `realPath()` that walks up to the deepest existing ancestor, resolves it, and re-appends the remainder.

- [ ] **Step 1: Write the failing tests**

Create `go/internal/fsutil/fsutil_test.go`:

```go
package fsutil

import (
	"os"
	"path/filepath"
	"testing"
)

func TestCheckProtectedPathBlocksInsideDir(t *testing.T) {
	dir := t.TempDir()
	pp := []ProtectedEntry{{Pattern: dir, Modes: ModeRead | ModeWrite}}
	msg := CheckProtectedPath(filepath.Join(dir, "audit.log"), ModeRead, pp)
	want := "Access denied: path is within protected directory " + dir
	if msg != want {
		t.Fatalf("got %q, want %q", msg, want)
	}
	if CheckProtectedPath(dir, ModeWrite, pp) == "" {
		t.Fatal("the protected dir itself must be blocked")
	}
}

func TestCheckProtectedPathAllowsOutsideAndOtherMode(t *testing.T) {
	dir := t.TempDir()
	other := t.TempDir()
	pp := []ProtectedEntry{{Pattern: dir, Modes: ModeWrite}} // write-only protection
	if msg := CheckProtectedPath(filepath.Join(other, "f"), ModeWrite, pp); msg != "" {
		t.Fatalf("outside path blocked: %q", msg)
	}
	if msg := CheckProtectedPath(filepath.Join(dir, "f"), ModeRead, pp); msg != "" {
		t.Fatalf("read must be allowed on write-only protection: %q", msg)
	}
	if CheckProtectedPath(filepath.Join(dir, "f"), ModeWrite, pp) == "" {
		t.Fatal("write must be blocked")
	}
}

func TestCheckProtectedPathResolvesSymlinks(t *testing.T) {
	real := t.TempDir()
	linkParent := t.TempDir()
	link := filepath.Join(linkParent, "link")
	if err := os.Symlink(real, link); err != nil {
		t.Skip("symlinks unavailable")
	}
	pp := []ProtectedEntry{{Pattern: real, Modes: ModeRead | ModeWrite}}
	if CheckProtectedPath(filepath.Join(link, "esc.txt"), ModeRead, pp) == "" {
		t.Fatal("symlinked path into protected dir must be blocked")
	}
}

func TestCheckProtectedPathNonexistentCandidate(t *testing.T) {
	dir := t.TempDir()
	pp := []ProtectedEntry{{Pattern: dir, Modes: ModeRead | ModeWrite}}
	// File doesn't exist yet — must still be recognized as inside.
	if CheckProtectedPath(filepath.Join(dir, "not-yet", "deep.log"), ModeWrite, pp) == "" {
		t.Fatal("nonexistent path under protected dir must be blocked")
	}
}

func TestDecodeReplacePerByte(t *testing.T) {
	// Two invalid bytes → two replacement chars (Python errors="replace").
	in := []byte{'a', 0xff, 0xfe, 'b'}
	got := DecodeReplace(in)
	want := "a��b"
	if got != want {
		t.Fatalf("got %q, want %q", got, want)
	}
	if DecodeReplace([]byte("héllo")) != "héllo" {
		t.Fatal("valid UTF-8 must pass through")
	}
}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd go && go test ./internal/fsutil/`
Expected: compile FAIL.

- [ ] **Step 3: Write the implementation**

Create `go/internal/fsutil/fsutil.go`:

```go
// Package fsutil holds filesystem helpers shared by the file tools:
// protected-path checks (parity with src/mymcp/tools/files.py) and Python
// errors="replace"-style UTF-8 decoding.
package fsutil

import (
	"os"
	"path/filepath"
	"strings"
	"unicode/utf8"
)

type Mode uint8

const (
	ModeRead Mode = 1 << iota
	ModeWrite
)

type ProtectedEntry struct {
	Pattern string
	Modes   Mode
}

// CheckProtectedPath returns the denial message if path is protected against
// mode, or "" if allowed. Message text matches the Python core.
func CheckProtectedPath(path string, mode Mode, protected []ProtectedEntry) string {
	real := realPath(path)
	for _, entry := range protected {
		if entry.Modes&mode == 0 {
			continue
		}
		protReal := realPath(entry.Pattern)
		if real == protReal || strings.HasPrefix(real, protReal+string(os.PathSeparator)) {
			return "Access denied: path is within protected directory " + entry.Pattern
		}
	}
	return ""
}

// realPath mimics Python os.path.realpath: absolute + symlinks resolved, and
// it never fails — for nonexistent paths the deepest existing ancestor is
// resolved and the remaining components are appended.
func realPath(p string) string {
	abs, err := filepath.Abs(p)
	if err != nil {
		return p
	}
	if resolved, err := filepath.EvalSymlinks(abs); err == nil {
		return resolved
	}
	dir, rest := abs, ""
	for {
		parent := filepath.Dir(dir)
		rest = filepath.Join(filepath.Base(dir), rest)
		dir = parent
		if resolved, err := filepath.EvalSymlinks(dir); err == nil {
			return filepath.Join(resolved, rest)
		}
		if parent == filepath.Dir(parent) { // reached root
			return abs
		}
	}
}

// DecodeReplace decodes bytes as UTF-8, replacing EACH invalid byte with
// U+FFFD — Python bytes.decode("utf-8", errors="replace") semantics.
func DecodeReplace(b []byte) string {
	if utf8.Valid(b) {
		return string(b)
	}
	var sb strings.Builder
	sb.Grow(len(b))
	for len(b) > 0 {
		r, size := utf8.DecodeRune(b)
		if r == utf8.RuneError && size == 1 {
			sb.WriteRune('�')
		} else {
			sb.Write(b[:size])
		}
		b = b[size:]
	}
	return sb.String()
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd go && go test ./internal/fsutil/ -v`
Expected: 5 PASS.

- [ ] **Step 5: Commit**

```bash
git add go/internal/fsutil/
git commit -m "feat(go): protected-path checks and Python-style UTF-8 replace decoding

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 5: read_file tool

**Files:**
- Create: `go/internal/tools/readfile.go`
- Test: `go/internal/tools/readfile_test.go`

Parity contract (from `src/mymcp/tools/files.py:85-146`; read it before coding):
- Clamps: `limit = min(max(1, limit), MaxLimit)` (nil/absent → DefaultLimit); `offset = max(1, offset)`.
- Protected check with mode read → `{"success":false,"error":"ProtectedPath","message":<msg>}`.
- Errors: missing → `FileNotFoundError` message `"File not found: <path>"` suggestion `"Check the file path"`; directory → `IsADirectoryError` message `"<path> is a directory"` suggestion `"Use glob to list directory contents"`; permission → `PermissionError` suggestion `"Check file read permissions"`.
- Lines: split keeping semantics of Python `readlines()` (split after `\n`; a trailing chunk without `\n` is still a line). Per line: strip ALL trailing `\n`, then ALL trailing `\r`; if the stripped line exceeds MaxLineBytes, cut at MaxLineBytes bytes, decode-replace, append `" [LINE TRUNCATED]"`; else decode-replace. Format `fmt.Sprintf("%4d\t%s", lineNo, text)`, lineNo starting at offset. Join with `"\n"`.
- Success shape: `{"content": str, "total_lines": N, "truncated": (offset-1+limit) < N}`.

All tools in this package return `map[string]any` (serialized to JSON by the MCP layer) and take a `Deps` struct so tests inject config without env vars:

- [ ] **Step 1: Write the shared Deps type and the failing tests**

Create `go/internal/tools/readfile.go` with ONLY the Deps type first (the test needs it to compile):

```go
// Package tools implements the MCP tool behaviors, ported line-for-line from
// src/mymcp/tools/files.py. Every function returns a map that the MCP layer
// serializes to JSON — key names and error codes are part of the compat
// contract with the Python core.
package tools

import (
	"github.com/algony-tony/mymcp/go/internal/config"
	"github.com/algony-tony/mymcp/go/internal/fsutil"
)

// Deps carries config and the protected-path table into the tools.
type Deps struct {
	Cfg       *config.Config
	Protected []fsutil.ProtectedEntry
}

// ProtectedFromConfig builds the legacy protected table (audit dir + extras),
// which blocks both read and write, matching config.PROTECTED_PATHS.
func ProtectedFromConfig(cfg *config.Config) []fsutil.ProtectedEntry {
	var out []fsutil.ProtectedEntry
	for _, p := range cfg.ProtectedPaths() {
		out = append(out, fsutil.ProtectedEntry{Pattern: p, Modes: fsutil.ModeRead | fsutil.ModeWrite})
	}
	return out
}
```

Create `go/internal/tools/readfile_test.go`:

```go
package tools

import (
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/algony-tony/mymcp/go/internal/config"
	"github.com/algony-tony/mymcp/go/internal/fsutil"
)

func testDeps(t *testing.T) Deps {
	t.Helper()
	t.Setenv("MYMCP_AUDIT_LOG_DIR", filepath.Join(t.TempDir(), "audit"))
	cfg, err := config.Load()
	if err != nil {
		t.Fatal(err)
	}
	return Deps{Cfg: cfg, Protected: ProtectedFromConfig(cfg)}
}

func writeTemp(t *testing.T, content string) string {
	t.Helper()
	p := filepath.Join(t.TempDir(), "f.txt")
	if err := os.WriteFile(p, []byte(content), 0o644); err != nil {
		t.Fatal(err)
	}
	return p
}

func TestReadFileBasicFormat(t *testing.T) {
	d := testDeps(t)
	p := writeTemp(t, "alpha\nbeta\ngamma\n")
	res := ReadFile(d, p, 1, nil)
	if res["total_lines"] != 3 {
		t.Fatalf("total_lines = %v", res["total_lines"])
	}
	want := "   1\talpha\n   2\tbeta\n   3\tgamma"
	if res["content"] != want {
		t.Fatalf("content = %q, want %q", res["content"], want)
	}
	if res["truncated"] != false {
		t.Fatal("must not be truncated")
	}
}

func TestReadFileOffsetLimitAndTruncatedFlag(t *testing.T) {
	d := testDeps(t)
	p := writeTemp(t, "l1\nl2\nl3\nl4\nl5\n")
	lim := 2
	res := ReadFile(d, p, 2, &lim)
	want := "   2\tl2\n   3\tl3"
	if res["content"] != want {
		t.Fatalf("content = %q", res["content"])
	}
	if res["truncated"] != true {
		t.Fatal("truncated must be true: offset-1+limit < total")
	}
	// Clamps: offset<1 → 1; limit<1 → 1.
	lim = 0
	res = ReadFile(d, p, -5, &lim)
	if !strings.HasPrefix(res["content"].(string), "   1\tl1") {
		t.Fatalf("clamp failed: %q", res["content"])
	}
}

func TestReadFileCRLFAndLongLine(t *testing.T) {
	d := testDeps(t)
	t.Setenv("MYMCP_READ_FILE_MAX_LINE_BYTES", "8")
	cfg, _ := config.Load()
	d.Cfg = cfg
	p := writeTemp(t, "short\r\n"+strings.Repeat("x", 20)+"\n")
	res := ReadFile(d, p, 1, nil)
	lines := strings.Split(res["content"].(string), "\n")
	if lines[0] != "   1\tshort" {
		t.Fatalf("CRLF strip failed: %q", lines[0])
	}
	if lines[1] != "   2\t"+strings.Repeat("x", 8)+" [LINE TRUNCATED]" {
		t.Fatalf("long-line truncation failed: %q", lines[1])
	}
}

func TestReadFileNoTrailingNewline(t *testing.T) {
	d := testDeps(t)
	p := writeTemp(t, "a\nb") // no trailing newline: still 2 lines
	res := ReadFile(d, p, 1, nil)
	if res["total_lines"] != 2 {
		t.Fatalf("total_lines = %v, want 2", res["total_lines"])
	}
}

func TestReadFileErrors(t *testing.T) {
	d := testDeps(t)
	res := ReadFile(d, filepath.Join(t.TempDir(), "missing.txt"), 1, nil)
	if res["success"] != false || res["error"] != "FileNotFoundError" {
		t.Fatalf("missing file: %+v", res)
	}
	if !strings.HasPrefix(res["message"].(string), "File not found: ") {
		t.Fatalf("message = %q", res["message"])
	}
	dir := t.TempDir()
	res = ReadFile(d, dir, 1, nil)
	if res["error"] != "IsADirectoryError" || res["message"] != dir+" is a directory" {
		t.Fatalf("directory: %+v", res)
	}
}

func TestReadFileProtected(t *testing.T) {
	d := testDeps(t)
	blocked := t.TempDir()
	d.Protected = append(d.Protected,
		fsutil.ProtectedEntry{Pattern: blocked, Modes: fsutil.ModeRead | fsutil.ModeWrite})
	target := filepath.Join(blocked, "secret.txt")
	os.WriteFile(target, []byte("x"), 0o644)
	res := ReadFile(d, target, 1, nil)
	if res["success"] != false || res["error"] != "ProtectedPath" {
		t.Fatalf("protected: %+v", res)
	}
}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd go && go test ./internal/tools/`
Expected: compile FAIL (`ReadFile` undefined).

- [ ] **Step 3: Write the implementation**

Append to `go/internal/tools/readfile.go`:

```go
import (
	"bytes"
	"errors"
	"fmt"
	"io/fs"
	"os"
	"strings"
)

// ReadFile ports read_file. limit == nil → config default. Returned map keys
// and error codes are the compat contract.
func ReadFile(d Deps, filePath string, offset int, limit *int) map[string]any {
	lim := d.Cfg.ReadFileDefaultLimit
	if limit != nil {
		lim = *limit
	}
	lim = min(max(1, lim), d.Cfg.ReadFileMaxLimit)
	offset = max(1, offset)

	if msg := fsutil.CheckProtectedPath(filePath, fsutil.ModeRead, d.Protected); msg != "" {
		return map[string]any{"success": false, "error": "ProtectedPath", "message": msg}
	}

	raw, err := os.ReadFile(filePath)
	if err != nil {
		switch {
		case errors.Is(err, fs.ErrNotExist):
			return map[string]any{
				"success": false, "error": "FileNotFoundError",
				"message": "File not found: " + filePath, "suggestion": "Check the file path",
			}
		case errors.Is(err, fs.ErrPermission):
			return map[string]any{
				"success": false, "error": "PermissionError",
				"message": err.Error(), "suggestion": "Check file read permissions",
			}
		default:
			// Reading a directory errors with EISDIR on Linux and lands here.
			if st, serr := os.Stat(filePath); serr == nil && st.IsDir() {
				return map[string]any{
					"success": false, "error": "IsADirectoryError",
					"message": filePath + " is a directory",
					"suggestion": "Use glob to list directory contents",
				}
			}
			return map[string]any{"success": false, "error": "OSError", "message": err.Error()}
		}
	}

	rawLines := splitKeepLines(raw)
	total := len(rawLines)
	start := offset - 1
	end := min(start+lim, total)
	var out []string
	if start < total {
		for i, line := range rawLines[start:end] {
			line = bytes.TrimRight(line, "\n")
			line = bytes.TrimRight(line, "\r")
			var text string
			if len(line) > d.Cfg.ReadFileMaxLineBytes {
				text = fsutil.DecodeReplace(line[:d.Cfg.ReadFileMaxLineBytes]) + " [LINE TRUNCATED]"
			} else {
				text = fsutil.DecodeReplace(line)
			}
			out = append(out, fmt.Sprintf("%4d\t%s", start+1+i, text))
		}
	}
	return map[string]any{
		"content":     strings.Join(out, "\n"),
		"total_lines": total,
		"truncated":   (offset - 1 + lim) < total,
	}
}

// splitKeepLines mimics Python readlines(): split after each \n, keep the
// terminator; a final chunk without \n is still a line; empty file → 0 lines.
func splitKeepLines(b []byte) [][]byte {
	var lines [][]byte
	for len(b) > 0 {
		i := bytes.IndexByte(b, '\n')
		if i < 0 {
			lines = append(lines, b)
			break
		}
		lines = append(lines, b[:i+1])
		b = b[i+1:]
	}
	return lines
}
```

(Merge the imports into a single block; `gofmt` will settle the order.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd go && go test ./internal/tools/ -v -run TestReadFile`
Expected: 6 PASS.

- [ ] **Step 5: Commit**

```bash
git add go/internal/tools/
git commit -m "feat(go): read_file tool with line-format parity

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 6: glob tool

**Files:**
- Create: `go/internal/tools/glob.go`
- Test: `go/internal/tools/glob_test.go`

Parity contract (from `src/mymcp/tools/files.py:257-274`):
- Full pattern = `filepath.Join(abs(path), pattern)`; `**` is recursive (use `github.com/bmatcuk/doublestar/v4`, `doublestar.FilepathGlob(fullPattern)`).
- Sort by mtime **descending** (missing file → mtime 0).
- Filter out read-protected paths AFTER sorting.
- `count` = filtered total (pre-truncation); `files` = first `GlobMaxResults`; `truncated` = count > max.
- Any error → `{"success": false, "error": <GoErrorTypeName>, "message": ...}` (the compat suite asserts only `success:false` here, error naming differs from Python).

- [ ] **Step 1: Write the failing tests**

Create `go/internal/tools/glob_test.go`:

```go
package tools

import (
	"os"
	"path/filepath"
	"testing"
	"time"

	"github.com/algony-tony/mymcp/go/internal/fsutil"
)

func TestGlobRecursiveAndMtimeOrder(t *testing.T) {
	d := testDeps(t)
	root := t.TempDir()
	os.MkdirAll(filepath.Join(root, "sub"), 0o755)
	older := filepath.Join(root, "a.log")
	newer := filepath.Join(root, "sub", "b.log")
	os.WriteFile(older, []byte("x"), 0o644)
	os.WriteFile(newer, []byte("x"), 0o644)
	old := time.Now().Add(-time.Hour)
	os.Chtimes(older, old, old)

	res := Glob(d, "**/*.log", root)
	files := res["files"].([]string)
	if len(files) != 2 || res["count"] != 2 || res["truncated"] != false {
		t.Fatalf("res = %+v", res)
	}
	if files[0] != newer || files[1] != older {
		t.Fatalf("mtime desc order wrong: %v", files)
	}
}

func TestGlobTruncation(t *testing.T) {
	d := testDeps(t)
	t.Setenv("MYMCP_GLOB_MAX_RESULTS", "2")
	cfg, _ := loadCfg(t)
	d.Cfg = cfg
	root := t.TempDir()
	for _, n := range []string{"1.txt", "2.txt", "3.txt"} {
		os.WriteFile(filepath.Join(root, n), []byte("x"), 0o644)
	}
	res := Glob(d, "*.txt", root)
	if res["count"] != 3 || res["truncated"] != true {
		t.Fatalf("res = %+v", res)
	}
	if len(res["files"].([]string)) != 2 {
		t.Fatalf("files len = %d", len(res["files"].([]string)))
	}
}

func TestGlobFiltersProtected(t *testing.T) {
	d := testDeps(t)
	root := t.TempDir()
	secret := filepath.Join(root, "secret")
	os.MkdirAll(secret, 0o755)
	os.WriteFile(filepath.Join(secret, "s.txt"), []byte("x"), 0o644)
	os.WriteFile(filepath.Join(root, "ok.txt"), []byte("x"), 0o644)
	d.Protected = append(d.Protected,
		fsutil.ProtectedEntry{Pattern: secret, Modes: fsutil.ModeRead | fsutil.ModeWrite})
	res := Glob(d, "**/*.txt", root)
	files := res["files"].([]string)
	if len(files) != 1 || files[0] != filepath.Join(root, "ok.txt") {
		t.Fatalf("protected filter failed: %v", files)
	}
	if res["count"] != 1 {
		t.Fatalf("count must be post-filter: %v", res["count"])
	}
}

func TestGlobNoMatches(t *testing.T) {
	d := testDeps(t)
	res := Glob(d, "*.nope", t.TempDir())
	if res["count"] != 0 || res["truncated"] != false {
		t.Fatalf("res = %+v", res)
	}
	if len(res["files"].([]string)) != 0 {
		t.Fatal("files must be empty")
	}
}
```

Add the helper to `readfile_test.go` (it is shared):

```go
func loadCfg(t *testing.T) (*config.Config, error) {
	t.Helper()
	return config.Load()
}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd go && go test ./internal/tools/ -run TestGlob`
Expected: compile FAIL (`Glob` undefined).

- [ ] **Step 3: Add the dependency and write the implementation**

```bash
cd go && go get github.com/bmatcuk/doublestar/v4@latest
```

Create `go/internal/tools/glob.go`:

```go
package tools

import (
	"fmt"
	"os"
	"path/filepath"
	"sort"

	"github.com/bmatcuk/doublestar/v4"

	"github.com/algony-tony/mymcp/go/internal/fsutil"
)

// Glob ports glob_files: recursive glob under path, mtime-desc, protected
// paths filtered, truncated at GlobMaxResults. count is the post-filter,
// pre-truncation total.
func Glob(d Deps, pattern, path string) map[string]any {
	base, err := filepath.Abs(path)
	if err != nil {
		return map[string]any{"success": false, "error": fmt.Sprintf("%T", err), "message": err.Error()}
	}
	fullPattern := filepath.Join(base, pattern)
	matches, err := doublestar.FilepathGlob(fullPattern)
	if err != nil {
		return map[string]any{"success": false, "error": fmt.Sprintf("%T", err), "message": err.Error()}
	}
	sort.SliceStable(matches, func(i, j int) bool {
		return mtimeOrZero(matches[i]) > mtimeOrZero(matches[j])
	})
	filtered := matches[:0]
	for _, m := range matches {
		if fsutil.CheckProtectedPath(m, fsutil.ModeRead, d.Protected) == "" {
			filtered = append(filtered, m)
		}
	}
	count := len(filtered)
	truncated := count > d.Cfg.GlobMaxResults
	if truncated {
		filtered = filtered[:d.Cfg.GlobMaxResults]
	}
	if filtered == nil {
		filtered = []string{}
	}
	return map[string]any{"files": []string(filtered), "count": count, "truncated": truncated}
}

func mtimeOrZero(p string) int64 {
	st, err := os.Stat(p)
	if err != nil {
		return 0
	}
	return st.ModTime().UnixNano()
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd go && go test ./internal/tools/ -v -run TestGlob`
Expected: 4 PASS.

- [ ] **Step 5: Commit**

```bash
git add go/internal/tools/ go/go.mod go/go.sum
git commit -m "feat(go): glob tool with mtime ordering and protected filtering

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 7: grep tool (ripgrep + native fallback)

**Files:**
- Create: `go/internal/tools/grep.go`
- Test: `go/internal/tools/grep_test.go`

Parity contract (from `src/mymcp/tools/files.py:282-394`):
- Clamp: `maxResults = min(max(1, maxResults), GrepMaxResults)`; default from `GrepDefaultMaxResults` applied by the dispatch layer (Task 8), the tool takes the resolved value.
- If `rg` is on PATH → rg path; else native fallback. For tests, the lookup goes through `d.RgPath()` so it can be forced off.
- rg args in order: `rg --no-heading -n` [`-i`] [`-C <n>`] [`--glob <g>`] [`-l` | `--count`] `<pattern> <path>`; 60s timeout → `{"success":false,"error":"TimeoutError","message":"grep timed out after 60s"}`; kill the process on timeout.
- rg output: split lines; keep a line only if the part before the first `:` passes the read-protected check; total = kept count; join first maxResults; if truncated append `"\n[TRUNCATED: <n> more matches not shown]"`. Return `{"results": str, "match_count": total}`.
- Native fallback: compile Go regexp (RE2), case-insensitive via `(?i)` prefix; invalid → `{"success":false,"error":"InvalidRegex","message":...}`. If path is a file, search just it; else walk; `glob` filter matches the **basename** (`path.Match`). Per file (skip read-protected, skip unreadable): mode `files` → path once if any line matches; `count` → `"<path>: <n>"` if n>0 (note the space — Python fallback differs from rg here; compat asserts loosely); `content` → `"<path>:<lineno>:<line-rstripped>"`, stop collecting at maxResults (content mode only). Same truncation marker.
- `context_lines` is honored on the rg path (`-C`) and **ignored** by the fallback — same as Python.

- [ ] **Step 1: Write the failing tests**

Create `go/internal/tools/grep_test.go`:

```go
package tools

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

// forceFallback disables rg for the test.
func forceFallback(d *Deps) { d.RgOverride = "disabled" }

func grepDir(t *testing.T) string {
	t.Helper()
	dir := t.TempDir()
	os.WriteFile(filepath.Join(dir, "a.log"), []byte("error one\nok line\nerror two\n"), 0o644)
	os.WriteFile(filepath.Join(dir, "b.txt"), []byte("nothing here\n"), 0o644)
	return dir
}

func TestGrepFallbackContentMode(t *testing.T) {
	d := testDeps(t)
	forceFallback(&d)
	dir := grepDir(t)
	res := Grep(d, "error \\w+", dir, "", "content", 0, 100, false)
	if res["match_count"] != 2 {
		t.Fatalf("match_count = %v; res=%v", res["match_count"], res)
	}
	out := res["results"].(string)
	if !strings.Contains(out, "a.log:1:error one") || !strings.Contains(out, "a.log:3:error two") {
		t.Fatalf("content lines wrong: %q", out)
	}
}

func TestGrepFallbackFilesAndCountModes(t *testing.T) {
	d := testDeps(t)
	forceFallback(&d)
	dir := grepDir(t)
	res := Grep(d, "error", dir, "", "files", 0, 100, false)
	out := res["results"].(string)
	if !strings.HasSuffix(strings.TrimSpace(out), "a.log") || res["match_count"] != 1 {
		t.Fatalf("files mode: %v", res)
	}
	res = Grep(d, "error", dir, "", "count", 0, 100, false)
	if !strings.Contains(res["results"].(string), "a.log: 2") {
		t.Fatalf("count mode: %v", res)
	}
}

func TestGrepFallbackGlobFilterAndCaseInsensitive(t *testing.T) {
	d := testDeps(t)
	forceFallback(&d)
	dir := grepDir(t)
	res := Grep(d, "ERROR", dir, "*.log", "content", 0, 100, true)
	if res["match_count"] != 2 {
		t.Fatalf("case-insensitive+glob: %v", res)
	}
	res = Grep(d, "error", dir, "*.txt", "content", 0, 100, false)
	if res["match_count"] != 0 {
		t.Fatalf("glob filter must exclude a.log: %v", res)
	}
}

func TestGrepFallbackTruncationMarker(t *testing.T) {
	d := testDeps(t)
	forceFallback(&d)
	dir := t.TempDir()
	var sb strings.Builder
	for range 10 {
		sb.WriteString("match\n")
	}
	os.WriteFile(filepath.Join(dir, "m.txt"), []byte(sb.String()), 0o644)
	res := Grep(d, "match", dir, "", "content", 0, 3, false)
	out := res["results"].(string)
	if !strings.Contains(out, "[TRUNCATED: ") || !strings.Contains(out, " more matches not shown]") {
		t.Fatalf("truncation marker missing: %q", out)
	}
}

func TestGrepInvalidRegex(t *testing.T) {
	d := testDeps(t)
	forceFallback(&d)
	res := Grep(d, "([unclosed", t.TempDir(), "", "content", 0, 100, false)
	if res["success"] != false || res["error"] != "InvalidRegex" {
		t.Fatalf("invalid regex: %v", res)
	}
}

func TestGrepFallbackSkipsProtected(t *testing.T) {
	d := testDeps(t)
	forceFallback(&d)
	dir := grepDir(t)
	d.Protected = append(d.Protected, protectedAll(dir))
	res := Grep(d, "error", dir, "", "content", 0, 100, false)
	if res["match_count"] != 0 {
		t.Fatalf("protected dir must yield 0: %v", res)
	}
}

func TestGrepRgPathIfInstalled(t *testing.T) {
	d := testDeps(t)
	if d.RgPath() == "" {
		t.Skip("ripgrep not installed")
	}
	dir := grepDir(t)
	res := Grep(d, "error", dir, "", "content", 0, 100, false)
	if res["match_count"] != 2 {
		t.Fatalf("rg path: %v", res)
	}
}
```

Add to `readfile_test.go` (shared helper):

```go
func protectedAll(dir string) fsutil.ProtectedEntry {
	return fsutil.ProtectedEntry{Pattern: dir, Modes: fsutil.ModeRead | fsutil.ModeWrite}
}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd go && go test ./internal/tools/ -run TestGrep`
Expected: compile FAIL (`Grep`, `RgOverride` undefined).

- [ ] **Step 3: Write the implementation**

Add to the `Deps` struct in `go/internal/tools/readfile.go`:

```go
	// RgOverride: "" = auto-detect rg on PATH; "disabled" = force fallback;
	// any other value = explicit rg binary path. Tests use "disabled".
	RgOverride string
```

And the lookup method:

```go
// RgPath returns the ripgrep binary to use, or "" for the native fallback.
func (d Deps) RgPath() string {
	switch d.RgOverride {
	case "":
		p, err := exec.LookPath("rg")
		if err != nil {
			return ""
		}
		return p
	case "disabled":
		return ""
	default:
		return d.RgOverride
	}
}
```

(Add `"os/exec"` to that file's imports.)

Create `go/internal/tools/grep.go`:

```go
package tools

import (
	"context"
	"fmt"
	"io/fs"
	"os"
	"os/exec"
	"path"
	"path/filepath"
	"regexp"
	"strconv"
	"strings"
	"time"
	"unicode"

	"github.com/algony-tony/mymcp/go/internal/fsutil"
)

// Grep ports grep_files: ripgrep when available, native RE2 fallback
// otherwise. maxResults arrives pre-defaulted by the dispatch layer.
func Grep(d Deps, pattern, searchPath, globPat, outputMode string,
	contextLines, maxResults int, caseInsensitive bool,
) map[string]any {
	maxResults = min(max(1, maxResults), d.Cfg.GrepMaxResults)
	if rg := d.RgPath(); rg != "" {
		return grepRg(d, rg, pattern, searchPath, globPat, outputMode, contextLines, maxResults, caseInsensitive)
	}
	return grepNative(d, pattern, searchPath, globPat, outputMode, maxResults, caseInsensitive)
}

func grepRg(d Deps, rg, pattern, searchPath, globPat, outputMode string,
	contextLines, maxResults int, caseInsensitive bool,
) map[string]any {
	args := []string{"--no-heading", "-n"}
	if caseInsensitive {
		args = append(args, "-i")
	}
	if contextLines > 0 {
		args = append(args, "-C", strconv.Itoa(contextLines))
	}
	if globPat != "" {
		args = append(args, "--glob", globPat)
	}
	switch outputMode {
	case "files":
		args = append(args, "-l")
	case "count":
		args = append(args, "--count")
	}
	args = append(args, pattern, searchPath)

	ctx, cancel := context.WithTimeout(context.Background(), 60*time.Second)
	defer cancel()
	out, err := exec.CommandContext(ctx, rg, args...).Output()
	if ctx.Err() == context.DeadlineExceeded {
		return map[string]any{
			"success": false, "error": "TimeoutError",
			"message": "grep timed out after 60s",
		}
	}
	// rg exits 1 on "no matches" with empty output — not an error for us.
	var exitErr *exec.ExitError
	if err != nil && !errors.As(err, &exitErr) {
		return map[string]any{"success": false, "error": fmt.Sprintf("%T", err), "message": err.Error()}
	}

	var kept []string
	for _, line := range strings.Split(strings.TrimRight(fsutil.DecodeReplace(out), "\n"), "\n") {
		if line == "" {
			continue
		}
		p, _, _ := strings.Cut(line, ":")
		if fsutil.CheckProtectedPath(p, fsutil.ModeRead, d.Protected) == "" {
			kept = append(kept, line)
		}
	}
	return grepResult(kept, maxResults)
}

func grepNative(d Deps, pattern, searchPath, globPat, outputMode string,
	maxResults int, caseInsensitive bool,
) map[string]any {
	if caseInsensitive {
		pattern = "(?i)" + pattern
	}
	re, err := regexp.Compile(pattern)
	if err != nil {
		return map[string]any{"success": false, "error": "InvalidRegex", "message": err.Error()}
	}

	var files []string
	if st, err := os.Stat(searchPath); err == nil && !st.IsDir() {
		files = []string{searchPath}
	} else {
		filepath.WalkDir(searchPath, func(p string, entry fs.DirEntry, err error) error {
			if err != nil || entry.IsDir() {
				return nil
			}
			if globPat != "" {
				if ok, _ := path.Match(globPat, entry.Name()); !ok {
					return nil
				}
			}
			files = append(files, p)
			return nil
		})
	}

	var matches []string
	for _, fpath := range files {
		if fsutil.CheckProtectedPath(fpath, fsutil.ModeRead, d.Protected) != "" {
			continue
		}
		if len(matches) >= maxResults && outputMode == "content" {
			break
		}
		raw, err := os.ReadFile(fpath)
		if err != nil {
			continue
		}
		// Python readlines() yields no phantom empty final line for files
		// ending in \n — trim one trailing newline before splitting.
		lines := strings.Split(strings.TrimSuffix(fsutil.DecodeReplace(raw), "\n"), "\n")
		switch outputMode {
		case "files":
			for _, line := range lines {
				if re.MatchString(line) {
					matches = append(matches, fpath)
					break
				}
			}
		case "count":
			n := 0
			for _, line := range lines {
				if re.MatchString(line) {
					n++
				}
			}
			if n > 0 {
				matches = append(matches, fmt.Sprintf("%s: %d", fpath, n))
			}
		default: // content
			for i, line := range lines {
				if re.MatchString(line) {
					matches = append(matches,
						fmt.Sprintf("%s:%d:%s", fpath, i+1, strings.TrimRightFunc(line, unicode.IsSpace)))
				}
			}
		}
	}
	return grepResult(matches, maxResults)
}

func grepResult(matches []string, maxResults int) map[string]any {
	total := len(matches)
	truncated := total > maxResults
	shown := matches
	if truncated {
		shown = matches[:maxResults]
	}
	result := strings.Join(shown, "\n")
	if truncated {
		result += fmt.Sprintf("\n[TRUNCATED: %d more matches not shown]", total-maxResults)
	}
	return map[string]any{"results": result, "match_count": total}
}
```

(Add `"errors"` to the import block; `gofmt` settles ordering.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd go && go test ./internal/tools/ -v -run TestGrep`
Expected: 7 PASS (or 6 PASS + 1 SKIP without ripgrep).

- [ ] **Step 5: gofmt + vet + full package test, then commit**

Run: `cd go && gofmt -w . && go vet ./... && go test ./...`
Expected: all clean/PASS.

```bash
git add go/internal/tools/
git commit -m "feat(go): grep tool with ripgrep path and native RE2 fallback

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 8: MCP server assembly (tool defs, dispatch, permissions)

**Files:**
- Create: `go/internal/mcpserver/tooldefs.go`
- Create: `go/internal/mcpserver/mcpserver.go`
- Test: `go/internal/mcpserver/mcpserver_test.go`

Parity contract (from `src/mymcp/mcp_server.py`):
- Tool schemas/descriptions byte-identical to `tool_definitions.py` — achieved **by construction**: embed the exact JSON and unmarshal into `jsonschema.Schema`.
- Permission logic: unknown name → `"Unknown tool: <name>"`; ro role + write tool → `"Permission denied: tool '<name>' requires rw role"`; both surface as `{"success":false,"error":"PermissionDenied","message":<msg>}` returned as **TextContent** (never an MCP protocol error).
- Tool results: JSON-serialized map as a single TextContent. Panics → `{"success":false,"error":"InternalError","message":"Tool '<name>' failed with an unexpected error"}`.
- M1 sets: `READ_TOOLS = {read_file, glob, grep}`, `WRITE_TOOLS = {}` (grows in M2/M3). With write tools absent, role-filtered `tools/list` is trivially satisfied; the filtering middleware lands in M2.
- Arg handling mirrors `dispatch_tool`: `read_file` limit = `min(given or default, max)` before the tool's own clamp; `grep` max_results likewise.

- [ ] **Step 0: Pin the SDK and verify API shapes**

```bash
cd go && go get github.com/modelcontextprotocol/go-sdk@latest
go doc github.com/modelcontextprotocol/go-sdk/mcp.Server.AddTool
go doc github.com/modelcontextprotocol/go-sdk/mcp.CallToolRequest
go doc github.com/modelcontextprotocol/go-sdk/mcp.CallToolResult
go doc github.com/modelcontextprotocol/go-sdk/mcp.Server.AddReceivingMiddleware
go doc github.com/modelcontextprotocol/go-sdk/jsonschema.Schema.UnmarshalJSON
```

The code below targets the documented v1.x shapes: handler `func(ctx, *mcp.CallToolRequest) (*mcp.CallToolResult, error)`, arguments as raw JSON at `req.Params.Arguments`, results as `&mcp.CallToolResult{Content: []mcp.Content{&mcp.TextContent{Text: ...}}}`, middleware `func(mcp.MethodHandler) mcp.MethodHandler`. **If any signature differs, adapt mechanically — the behavior contract above is what's fixed. Do not change response shapes.**

- [ ] **Step 1: Write tooldefs.go (schemas embedded as verbatim JSON)**

Create `go/internal/mcpserver/tooldefs.go`. The three `inputSchema` JSON literals below are transcribed from `src/mymcp/tool_definitions.py` — if in doubt, regenerate with `python3 -c "import json; from mymcp.tool_definitions import TOOL_DEFS; print(json.dumps({n: t.inputSchema for n,t in TOOL_DEFS.items() if n in ('read_file','glob','grep')}, indent=2))"` and compare:

```go
package mcpserver

import (
	"encoding/json"
	"fmt"

	"github.com/modelcontextprotocol/go-sdk/jsonschema"
)

type toolDef struct {
	Name        string
	Description string
	SchemaJSON  string
}

var toolDefs = []toolDef{
	{
		Name: "read_file",
		Description: "Read a file with line numbers. Supports pagination via offset/limit.",
		SchemaJSON: `{
  "type": "object",
  "properties": {
    "file_path": {"type": "string", "description": "Absolute path to file"},
    "offset": {"type": "integer", "description": "Start line 1-based (default 1)"},
    "limit": {"type": "integer", "description": "Lines to read (default MYMCP_READ_FILE_DEFAULT_LIMIT=2000, max MYMCP_READ_FILE_MAX_LIMIT=50000)"}
  },
  "required": ["file_path"],
  "additionalProperties": false
}`,
	},
	{
		Name: "glob",
		Description: "Find files by glob pattern, e.g. '**/*.py'. Results sorted by mtime desc.",
		SchemaJSON: `{
  "type": "object",
  "properties": {
    "pattern": {"type": "string", "description": "Glob pattern, e.g. '**/*.log'"},
    "path": {"type": "string", "description": "Root directory (default /)"}
  },
  "required": ["pattern"],
  "additionalProperties": false
}`,
	},
	{
		Name: "grep",
		Description: "Search file contents with regex. Uses ripgrep if installed, else Python fallback.",
		SchemaJSON: `{
  "type": "object",
  "properties": {
    "pattern": {"type": "string", "description": "Regex pattern"},
    "path": {"type": "string", "description": "File or directory to search (default /)"},
    "glob": {"type": "string", "description": "File filter e.g. '*.log'"},
    "output_mode": {"type": "string", "enum": ["content", "files", "count"], "description": "Output mode (default content)"},
    "context_lines": {"type": "integer", "description": "Lines of context (default 0)"},
    "max_results": {"type": "integer", "description": "Max matches (default 500, max 5000)"},
    "case_insensitive": {"type": "boolean", "description": "Case-insensitive (default false)"}
  },
  "required": ["pattern"],
  "additionalProperties": false
}`,
	},
}

func mustSchema(raw string) *jsonschema.Schema {
	var s jsonschema.Schema
	if err := json.Unmarshal([]byte(raw), &s); err != nil {
		panic(fmt.Sprintf("bad embedded schema: %v", err))
	}
	return &s
}
```

(Note: the grep description says "Python fallback" — keep it verbatim; changing tool descriptions breaks byte-parity of `tools/list`.)

- [ ] **Step 2: Write the failing tests**

Create `go/internal/mcpserver/mcpserver_test.go`:

```go
package mcpserver

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/algony-tony/mymcp/go/internal/config"
	"github.com/algony-tony/mymcp/go/internal/tools"
)

func deps(t *testing.T) tools.Deps {
	t.Helper()
	t.Setenv("MYMCP_AUDIT_LOG_DIR", filepath.Join(t.TempDir(), "audit"))
	cfg, err := config.Load()
	if err != nil {
		t.Fatal(err)
	}
	return tools.Deps{Cfg: cfg, Protected: tools.ProtectedFromConfig(cfg)}
}

func TestCheckToolPermission(t *testing.T) {
	if got := CheckToolPermission("read_file", "ro"); got != "" {
		t.Fatalf("ro+read must pass: %q", got)
	}
	if got := CheckToolPermission("read_file", "rw"); got != "" {
		t.Fatalf("rw+read must pass: %q", got)
	}
	if got := CheckToolPermission("no_such_tool", "rw"); got != "Unknown tool: no_such_tool" {
		t.Fatalf("unknown: %q", got)
	}
}

func TestDispatchReadFile(t *testing.T) {
	d := deps(t)
	p := filepath.Join(t.TempDir(), "x.txt")
	os.WriteFile(p, []byte("hello\n"), 0o644)
	out := Dispatch(d, "read_file", map[string]any{"file_path": p})
	var res map[string]any
	if err := json.Unmarshal([]byte(out), &res); err != nil {
		t.Fatal(err)
	}
	if res["content"] != "   1\thello" || res["total_lines"] != float64(1) {
		t.Fatalf("res = %v", res)
	}
}

func TestDispatchGrepDefaultsMaxResults(t *testing.T) {
	d := deps(t)
	dir := t.TempDir()
	os.WriteFile(filepath.Join(dir, "f.txt"), []byte("m\nm\nm\n"), 0o644)
	out := Dispatch(d, "grep", map[string]any{"pattern": "m", "path": dir})
	if !strings.Contains(out, `"match_count":3`) && !strings.Contains(out, `"match_count": 3`) {
		t.Fatalf("out = %s", out)
	}
}

func TestDispatchUnknownTool(t *testing.T) {
	d := deps(t)
	out := Dispatch(d, "bash_execute", map[string]any{"command": "id"})
	if !strings.Contains(out, `"UnknownTool"`) {
		t.Fatalf("out = %s", out)
	}
}

func TestSchemasParseAndListNames(t *testing.T) {
	names := ToolNames()
	want := []string{"read_file", "glob", "grep"}
	if len(names) != 3 {
		t.Fatalf("names = %v", names)
	}
	for _, w := range want {
		found := false
		for _, n := range names {
			if n == w {
				found = true
			}
		}
		if !found {
			t.Fatalf("missing %s in %v", w, names)
		}
	}
	for _, td := range toolDefs {
		_ = mustSchema(td.SchemaJSON) // panics on bad JSON
	}
}
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `cd go && go test ./internal/mcpserver/`
Expected: compile FAIL.

- [ ] **Step 4: Write mcpserver.go**

Create `go/internal/mcpserver/mcpserver.go`:

```go
// Package mcpserver assembles the MCP server: tool registration, the central
// dispatch with permission checks (parity with src/mymcp/mcp_server.py), and
// role plumbing via context.
package mcpserver

import (
	"context"
	"encoding/json"
	"fmt"
	"log"

	"github.com/modelcontextprotocol/go-sdk/mcp"

	"github.com/algony-tony/mymcp/go/internal/tools"
	"github.com/algony-tony/mymcp/go/internal/version"
)

// Role sets — M1 registers only the read tools; write sets grow in M2/M3.
var readTools = map[string]bool{"read_file": true, "glob": true, "grep": true}
var writeTools = map[string]bool{}

type ctxKey int

const authInfoKey ctxKey = 0

type AuthInfo struct {
	TokenName string
	Role      string
	IP        string
}

// WithAuthInfo is called by the HTTP auth middleware.
func WithAuthInfo(ctx context.Context, info AuthInfo) context.Context {
	return context.WithValue(ctx, authInfoKey, info)
}

func authInfoFrom(ctx context.Context) AuthInfo {
	if v, ok := ctx.Value(authInfoKey).(AuthInfo); ok {
		return v
	}
	return AuthInfo{TokenName: "unknown", Role: "rw", IP: "unknown"}
}

// ToolNames returns the registered tool names (M1: the three read tools).
func ToolNames() []string {
	names := make([]string, 0, len(toolDefs))
	for _, td := range toolDefs {
		names = append(names, td.Name)
	}
	return names
}

// CheckToolPermission ports check_tool_permission: "" = allowed.
func CheckToolPermission(name, role string) string {
	if !readTools[name] && !writeTools[name] {
		return "Unknown tool: " + name
	}
	if role == "rw" || readTools[name] {
		return ""
	}
	return fmt.Sprintf("Permission denied: tool '%s' requires rw role", name)
}

// Dispatch ports dispatch_tool: run the tool, return the JSON string.
// Argument defaulting mirrors the Python dispatch layer.
func Dispatch(d tools.Deps, name string, args map[string]any) string {
	var result map[string]any
	switch name {
	case "read_file":
		var limit *int
		if v, ok := argInt(args, "limit"); ok {
			l := min(v, d.Cfg.ReadFileMaxLimit)
			limit = &l
		}
		offset := 1
		if v, ok := argInt(args, "offset"); ok {
			offset = v
		}
		result = tools.ReadFile(d, argStr(args, "file_path", ""), offset, limit)
	case "glob":
		result = tools.Glob(d, argStr(args, "pattern", ""), argStr(args, "path", "/"))
	case "grep":
		maxResults := d.Cfg.GrepDefaultMaxResults
		if v, ok := argInt(args, "max_results"); ok {
			maxResults = min(v, d.Cfg.GrepMaxResults)
		}
		contextLines := 0
		if v, ok := argInt(args, "context_lines"); ok {
			contextLines = v
		}
		result = tools.Grep(d,
			argStr(args, "pattern", ""), argStr(args, "path", "/"),
			argStr(args, "glob", ""), argStr(args, "output_mode", "content"),
			contextLines, maxResults, argBool(args, "case_insensitive"))
	default:
		result = map[string]any{
			"success": false, "error": "UnknownTool",
			"message": fmt.Sprintf("No tool named '%s'", name),
		}
	}
	raw, err := json.Marshal(result)
	if err != nil {
		return `{"success": false, "error": "InternalError", "message": "result serialization failed"}`
	}
	return string(raw)
}

// BuildServer wires the SDK server: every tool shares one handler that runs
// the permission check then Dispatch, recovering panics into InternalError.
func BuildServer(d tools.Deps) *mcp.Server {
	srv := mcp.NewServer(&mcp.Implementation{Name: "linux-server", Version: version.Version}, nil)
	for _, td := range toolDefs {
		td := td
		srv.AddTool(
			&mcp.Tool{Name: td.Name, Description: td.Description, InputSchema: mustSchema(td.SchemaJSON)},
			func(ctx context.Context, req *mcp.CallToolRequest) (res *mcp.CallToolResult, _ error) {
				info := authInfoFrom(ctx)
				defer func() {
					if r := recover(); r != nil {
						log.Printf("panic in tool %s: %v", td.Name, r)
						res = textResult(fmt.Sprintf(
							`{"success": false, "error": "InternalError", "message": "Tool '%s' failed with an unexpected error"}`,
							td.Name))
					}
				}()
				if msg := CheckToolPermission(td.Name, info.Role); msg != "" {
					return textResult(permDeniedJSON(msg)), nil
				}
				args := map[string]any{}
				if len(req.Params.Arguments) > 0 {
					if err := json.Unmarshal(req.Params.Arguments, &args); err != nil {
						return textResult(permDeniedJSON("invalid arguments: " + err.Error())), nil
					}
				}
				return textResult(Dispatch(d, td.Name, args)), nil
			},
		)
	}
	return srv
}

func textResult(s string) *mcp.CallToolResult {
	return &mcp.CallToolResult{Content: []mcp.Content{&mcp.TextContent{Text: s}}}
}

func permDeniedJSON(msg string) string {
	raw, _ := json.Marshal(map[string]any{
		"success": false, "error": "PermissionDenied", "message": msg,
	})
	return string(raw)
}

func argStr(args map[string]any, key, def string) string {
	if v, ok := args[key].(string); ok {
		return v
	}
	return def
}

func argInt(args map[string]any, key string) (int, bool) {
	switch v := args[key].(type) {
	case float64:
		return int(v), true
	case int:
		return v, true
	}
	return 0, false
}

func argBool(args map[string]any, key string) bool {
	v, _ := args[key].(bool)
	return v
}
```

**Unknown-tool parity note:** the SDK rejects calls to unregistered names before our handlers run. To return Python's `{"success":false,"error":"PermissionDenied","message":"Unknown tool: <name>"}` instead, add a receiving middleware (verify exact signature with `go doc` from Step 0):

```go
// In BuildServer, after tool registration:
srv.AddReceivingMiddleware(func(next mcp.MethodHandler) mcp.MethodHandler {
	return func(ctx context.Context, method string, req mcp.Request) (mcp.Result, error) {
		if method == "tools/call" {
			if p, ok := req.GetParams().(*mcp.CallToolParams); ok {
				name := p.Name
				if !readTools[name] && !writeTools[name] {
					return textResult(permDeniedJSON("Unknown tool: " + name)), nil
				}
				info := authInfoFrom(ctx)
				if msg := CheckToolPermission(name, info.Role); msg != "" {
					return textResult(permDeniedJSON(msg)), nil
				}
			}
		}
		return next(ctx, method, req)
	}
})
```

If the middleware API differs materially, an acceptable M1 fallback is to skip the middleware and register a hidden catch-all check inside the HTTP layer later — but FIRST try the middleware; the compat test `test_unknown_tool_permission_denied` is the acceptance check either way. Also fix `role0`: replace `role0(name)` with just `name` and delete the helper (it was a lint dodge; write it clean).

- [ ] **Step 5: Run tests, fmt, vet**

Run: `cd go && gofmt -w . && go vet ./... && go test ./internal/mcpserver/ -v`
Expected: 5 PASS.

- [ ] **Step 6: Commit**

```bash
git add go/internal/mcpserver/ go/go.mod go/go.sum
git commit -m "feat(go): MCP server assembly with verbatim schemas and permission parity

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 9: HTTP server, auth middleware, serve command, graceful shutdown

**Files:**
- Create: `go/internal/httpserver/httpserver.go`
- Test: `go/internal/httpserver/httpserver_test.go`
- Modify: `go/cmd/mymcp/main.go`

Parity contract (from `src/mymcp/server.py` and `src/mymcp/cli.py:27-99`):
- `/mcp` guarded by Bearer auth; 401 bodies exactly `{"detail": "Missing Bearer token"}` / `{"detail": "Invalid or disabled token"}` (Content-Type application/json).
- On success: token info (`name`, `role`, client IP) flows via `mcpserver.WithAuthInfo` into the request context before delegating to the MCP handler.
- `/health` → `{"status": "ok", "version": <v>}`; `/version` → `{"version": <v>}` — both unauthenticated.
- Serve flow: `--env-file` sets `MYMCP_ENV_FILE` before `config.Load()`; `--host/--port` override config. Temp tokens: when no env file is discovered AND `MYMCP_ADMIN_TOKEN` unset — set `MYMCP_TOKEN_FILE` to `$TMPDIR/mymcp-temp-<pid>.json` (unless set), generate admin + rw tokens, print to stderr exactly:
  `[mymcp] temp admin token: <tok>` / `[mymcp] temp rw token:    <tok>` / `[mymcp] tokens are in-memory; they vanish on exit.` — then register the rw token as ephemeral in the store.
- Missing admin token (env file exists but no MYMCP_ADMIN_TOKEN) → startup error `"MYMCP_ADMIN_TOKEN environment variable is required"`.
- SIGTERM/SIGINT → `http.Server.Shutdown` with `ShutdownGraceSec` timeout, then `store.Flush()`.

- [ ] **Step 1: Write the failing tests**

Create `go/internal/httpserver/httpserver_test.go`:

```go
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd go && go test ./internal/httpserver/`
Expected: compile FAIL.

- [ ] **Step 3: Write the implementation**

Create `go/internal/httpserver/httpserver.go`:

```go
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
```

Add to `go/internal/config/config.go`:

```go
// DiscoveredEnvFile exposes env-file discovery for the temp-token decision.
func DiscoveredEnvFile() string { return discoverEnvFile() }
```

Rewrite `go/cmd/mymcp/main.go`'s `run` to wire serve:

```go
func run(args []string) int {
	if len(args) == 0 {
		fmt.Fprintln(os.Stderr, "usage: mymcp {serve|version}")
		return 2
	}
	switch args[0] {
	case "version":
		fmt.Println("mymcp " + version.Version)
		return 0
	case "serve":
		fs := flag.NewFlagSet("serve", flag.ContinueOnError)
		envFile := fs.String("env-file", "", "path to .env file")
		host := fs.String("host", "", "bind host (overrides config)")
		port := fs.Int("port", 0, "bind port (overrides config)")
		if err := fs.Parse(args[1:]); err != nil {
			return 2
		}
		if *envFile != "" {
			os.Setenv("MYMCP_ENV_FILE", *envFile)
		}
		if err := httpserver.Serve(*host, *port, version.Version); err != nil && err != http.ErrServerClosed {
			fmt.Fprintln(os.Stderr, "serve:", err)
			return 1
		}
		return 0
	default:
		fmt.Fprintf(os.Stderr, "unknown command: %s\n", args[0])
		return 2
	}
}
```

(Add imports: `flag`, `net/http`, `github.com/algony-tony/mymcp/go/internal/httpserver`.)

- [ ] **Step 4: Run tests, then a manual smoke test**

Run: `cd go && gofmt -w . && go vet ./... && go test ./...`
Expected: all PASS.

Smoke:

```bash
cd go && go build -o /tmp/mymcp-go ./cmd/mymcp
/tmp/mymcp-go serve --port 18765 2>/tmp/mymcp-go.err &
sleep 1
grep "temp rw token" /tmp/mymcp-go.err   # capture the token
curl -s http://127.0.0.1:18765/health     # {"status":"ok","version":"dev"}
curl -s http://127.0.0.1:18765/mcp -X POST -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"smoke","version":"0"}}}'
# ^ without Bearer → {"detail": "Missing Bearer token"}; with the temp rw token → an initialize result
kill %1
```

- [ ] **Step 5: Commit**

```bash
git add go/
git commit -m "feat(go): HTTP server with auth middleware, serve command, graceful shutdown

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 10: Compat suite (read-only subset) + CI wiring

**Files:**
- Create: `tests/compat/conftest.py`
- Create: `tests/compat/test_tools_list.py`
- Create: `tests/compat/test_read_file.py`
- Create: `tests/compat/test_glob.py`
- Create: `tests/compat/test_grep.py`
- Create: `tests/compat/test_auth_http.py`
- Modify: `.github/workflows/ci.yml` (compat jobs)
- Modify: `CHANGELOG.md` (Unreleased entry)

Design: the suite is pure black-box, aimed at `MYMCP_COMPAT_URL`. Both CI jobs seed the SAME `tokens.json` (format compatibility is itself under test) and set the same env. Assertions on `tools/list` are **subset** (Go M1 serves 3 tools, Python serves 9); the full-set gate arrives in M3.

- [ ] **Step 1: Write conftest.py**

Create `tests/compat/conftest.py`:

```python
"""Black-box compatibility suite. Aim it at a live server:

    MYMCP_COMPAT_URL=http://127.0.0.1:8765 \
    MYMCP_COMPAT_RW_TOKEN=tok_... MYMCP_COMPAT_RO_TOKEN=tok_... \
    MYMCP_COMPAT_TMP=/tmp/compat-scratch \
    pytest tests/compat/ -v

The suite never imports server internals — except tool_definitions, used as
the golden source for schema comparison.
"""

import json
import os

import pytest
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

BASE_URL = os.environ.get("MYMCP_COMPAT_URL", "http://127.0.0.1:8765")
RW_TOKEN = os.environ.get("MYMCP_COMPAT_RW_TOKEN", "")
RO_TOKEN = os.environ.get("MYMCP_COMPAT_RO_TOKEN", "")
# Scratch dir that BOTH the test process and the server can read/write.
TMP = os.environ.get("MYMCP_COMPAT_TMP", "/tmp/mymcp-compat")


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def scratch():
    os.makedirs(TMP, exist_ok=True)
    return TMP


class Client:
    """One-shot MCP calls over streamable HTTP with a Bearer token."""

    def __init__(self, token: str):
        self.token = token

    async def list_tools(self):
        async with streamablehttp_client(
            f"{BASE_URL}/mcp", headers={"Authorization": f"Bearer {self.token}"}
        ) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                return (await session.list_tools()).tools

    async def call(self, name: str, args: dict) -> dict:
        async with streamablehttp_client(
            f"{BASE_URL}/mcp", headers={"Authorization": f"Bearer {self.token}"}
        ) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.call_tool(name, args)
                assert result.content and result.content[0].type == "text"
                return json.loads(result.content[0].text)


@pytest.fixture
def rw() -> Client:
    assert RW_TOKEN, "MYMCP_COMPAT_RW_TOKEN not set"
    return Client(RW_TOKEN)


@pytest.fixture
def ro() -> Client:
    if not RO_TOKEN:
        pytest.skip("MYMCP_COMPAT_RO_TOKEN not set")
    return Client(RO_TOKEN)
```

- [ ] **Step 2: Write the test modules**

Create `tests/compat/test_tools_list.py`:

```python
"""tools/list: the three read tools must be present with byte-identical schemas."""

import pytest

from mymcp.tool_definitions import TOOL_DEFS

M1_TOOLS = ("read_file", "glob", "grep")


@pytest.mark.anyio
@pytest.mark.parametrize("name", M1_TOOLS)
async def test_tool_present_with_exact_schema(rw, name):
    tools = {t.name: t for t in await rw.list_tools()}
    assert name in tools, f"{name} missing from tools/list"
    golden = TOOL_DEFS[name]
    got = tools[name]
    assert got.description == golden.description
    assert got.inputSchema == golden.inputSchema


@pytest.mark.anyio
async def test_ro_token_sees_read_tools(ro):
    names = {t.name for t in await ro.list_tools()}
    assert set(M1_TOOLS) <= names
```

Create `tests/compat/test_read_file.py`:

```python
import os

import pytest


@pytest.mark.anyio
async def test_basic_line_format(rw, scratch):
    p = os.path.join(scratch, "basic.txt")
    with open(p, "w") as f:
        f.write("alpha\nbeta\n")
    res = await rw.call("read_file", {"file_path": p})
    assert res["content"] == "   1\talpha\n   2\tbeta"
    assert res["total_lines"] == 2
    assert res["truncated"] is False


@pytest.mark.anyio
async def test_offset_limit_truncated(rw, scratch):
    p = os.path.join(scratch, "5lines.txt")
    with open(p, "w") as f:
        f.write("".join(f"l{i}\n" for i in range(1, 6)))
    res = await rw.call("read_file", {"file_path": p, "offset": 2, "limit": 2})
    assert res["content"] == "   2\tl2\n   3\tl3"
    assert res["truncated"] is True


@pytest.mark.anyio
async def test_missing_file_error_shape(rw, scratch):
    res = await rw.call("read_file", {"file_path": os.path.join(scratch, "nope.txt")})
    assert res["success"] is False
    assert res["error"] == "FileNotFoundError"
    assert res["message"].startswith("File not found: ")


@pytest.mark.anyio
async def test_directory_error_shape(rw, scratch):
    res = await rw.call("read_file", {"file_path": scratch})
    assert res["success"] is False
    assert res["error"] == "IsADirectoryError"


@pytest.mark.anyio
async def test_protected_path_denied(rw):
    # CI sets MYMCP_PROTECTED_PATHS to this directory for both servers.
    res = await rw.call("read_file", {"file_path": "/tmp/mymcp-compat-protected/x"})
    assert res["success"] is False
    assert res["error"] == "ProtectedPath"
    assert "protected directory" in res["message"]
```

Create `tests/compat/test_glob.py`:

```python
import os
import time

import pytest


@pytest.mark.anyio
async def test_recursive_glob_mtime_desc(rw, scratch):
    root = os.path.join(scratch, "globtest")
    os.makedirs(os.path.join(root, "sub"), exist_ok=True)
    older = os.path.join(root, "old.mark")
    newer = os.path.join(root, "sub", "new.mark")
    for p in (older, newer):
        with open(p, "w") as f:
            f.write("x")
    past = time.time() - 3600
    os.utime(older, (past, past))
    res = await rw.call("glob", {"pattern": "**/*.mark", "path": root})
    assert res["count"] == 2
    assert res["truncated"] is False
    assert res["files"][0].endswith("new.mark")
    assert res["files"][1].endswith("old.mark")


@pytest.mark.anyio
async def test_glob_no_match(rw, scratch):
    res = await rw.call("glob", {"pattern": "*.definitely-not-here", "path": scratch})
    assert res["count"] == 0
    assert res["files"] == []
    assert res["truncated"] is False
```

Create `tests/compat/test_grep.py`:

```python
import os

import pytest


@pytest.fixture
def grep_root(scratch):
    root = os.path.join(scratch, "greptest")
    os.makedirs(root, exist_ok=True)
    with open(os.path.join(root, "app.log"), "w") as f:
        f.write("ERROR boom\nok\nerror quiet\n")
    return root


@pytest.mark.anyio
async def test_content_mode(rw, grep_root):
    res = await rw.call("grep", {"pattern": "error", "path": grep_root})
    assert res["match_count"] == 1
    assert "app.log:3:error quiet" in res["results"]


@pytest.mark.anyio
async def test_case_insensitive_and_files_mode(rw, grep_root):
    res = await rw.call(
        "grep",
        {"pattern": "error", "path": grep_root, "case_insensitive": True, "output_mode": "files"},
    )
    assert res["match_count"] == 1
    assert res["results"].endswith("app.log")


@pytest.mark.anyio
async def test_count_mode_loose(rw, grep_root):
    # rg emits "path:2", the fallbacks emit "path: 2" — assert loosely.
    res = await rw.call(
        "grep",
        {"pattern": "error", "path": grep_root, "case_insensitive": True, "output_mode": "count"},
    )
    line = res["results"].strip()
    assert line.replace(" ", "").endswith("app.log:2")


@pytest.mark.anyio
async def test_truncation_marker(rw, scratch):
    root = os.path.join(scratch, "trunctest")
    os.makedirs(root, exist_ok=True)
    with open(os.path.join(root, "many.txt"), "w") as f:
        f.write("hit\n" * 10)
    res = await rw.call("grep", {"pattern": "hit", "path": root, "max_results": 3})
    assert res["match_count"] == 10
    assert "[TRUNCATED: 7 more matches not shown]" in res["results"]
```

Create `tests/compat/test_auth_http.py`:

```python
import os

import httpx
import pytest

BASE_URL = os.environ.get("MYMCP_COMPAT_URL", "http://127.0.0.1:8765")


def test_mcp_requires_bearer():
    r = httpx.post(f"{BASE_URL}/mcp", json={})
    assert r.status_code == 401
    assert r.json() == {"detail": "Missing Bearer token"}


def test_mcp_rejects_bad_token():
    r = httpx.post(f"{BASE_URL}/mcp", json={}, headers={"Authorization": "Bearer tok_bogus"})
    assert r.status_code == 401
    assert r.json() == {"detail": "Invalid or disabled token"}


def test_health_and_version_unauthenticated():
    h = httpx.get(f"{BASE_URL}/health").json()
    assert h["status"] == "ok" and h["version"]
    v = httpx.get(f"{BASE_URL}/version").json()
    assert v["version"] == h["version"]


@pytest.mark.anyio
async def test_unknown_tool_permission_denied(rw):
    res = await rw.call("no_such_tool", {})
    assert res == {
        "success": False,
        "error": "PermissionDenied",
        "message": "Unknown tool: no_such_tool",
    }
```

- [ ] **Step 3: Prove the suite against the PYTHON server locally**

```bash
mkdir -p /tmp/mymcp-compat /tmp/mymcp-compat-protected
cat > /tmp/compat-tokens.json <<'EOF'
{"tokens": {"tok_compat_rw_0000000000000000": {"name": "compat-rw", "created_at": "x", "last_used": null, "enabled": true, "role": "rw"},
            "tok_compat_ro_0000000000000000": {"name": "compat-ro", "created_at": "x", "last_used": null, "enabled": true, "role": "ro"}},
 "admin_token": "compat-admin"}
EOF
MYMCP_ADMIN_TOKEN=compat-admin MYMCP_TOKEN_FILE=/tmp/compat-tokens.json \
MYMCP_PROTECTED_PATHS=/tmp/mymcp-compat-protected MYMCP_PORT=18770 \
  .venv/bin/mymcp serve &
sleep 2
MYMCP_COMPAT_URL=http://127.0.0.1:18770 \
MYMCP_COMPAT_RW_TOKEN=tok_compat_rw_0000000000000000 \
MYMCP_COMPAT_RO_TOKEN=tok_compat_ro_0000000000000000 \
  .venv/bin/python -m pytest tests/compat/ -v --benchmark-disable
kill %1
```

Expected: all PASS against Python. Fix suite bugs (not server bugs) until green.

- [ ] **Step 4: Prove the suite against the GO server locally**

Same as Step 3 but boot `/tmp/mymcp-go serve --port 18771` with the same env vars, and aim `MYMCP_COMPAT_URL` at 18771.
Expected: all PASS. Failures here are Go parity bugs — fix the Go side, never relax an assertion that Python satisfies.

- [ ] **Step 5: Wire CI**

Add to `.github/workflows/ci.yml` (two jobs; reuse the file's existing setup steps for Python):

```yaml
  compat-python:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v7
      - uses: actions/setup-python@v6
        with:
          python-version: "3.12"
      - run: pip install -e ".[dev]" -c requirements-dev.txt
      - name: seed tokens and boot server
        run: |
          mkdir -p /tmp/mymcp-compat /tmp/mymcp-compat-protected
          cp tests/compat/ci-tokens.json /tmp/compat-tokens.json
          MYMCP_ADMIN_TOKEN=compat-admin MYMCP_TOKEN_FILE=/tmp/compat-tokens.json \
          MYMCP_PROTECTED_PATHS=/tmp/mymcp-compat-protected MYMCP_PORT=18770 \
            mymcp serve & sleep 2
      - name: run compat suite
        run: |
          MYMCP_COMPAT_URL=http://127.0.0.1:18770 \
          MYMCP_COMPAT_RW_TOKEN=tok_compat_rw_0000000000000000 \
          MYMCP_COMPAT_RO_TOKEN=tok_compat_ro_0000000000000000 \
            pytest tests/compat/ -v --benchmark-disable

  compat-go:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v7
      - uses: actions/setup-python@v6
        with:
          python-version: "3.12"
      - uses: actions/setup-go@v5
        with:
          go-version: "1.24"
          cache-dependency-path: go/go.sum
      - run: pip install -e ".[dev]" -c requirements-dev.txt
      - name: build and boot go server
        run: |
          cd go && go build -o /tmp/mymcp-go ./cmd/mymcp && cd ..
          mkdir -p /tmp/mymcp-compat /tmp/mymcp-compat-protected
          cp tests/compat/ci-tokens.json /tmp/compat-tokens.json
          MYMCP_ADMIN_TOKEN=compat-admin MYMCP_TOKEN_FILE=/tmp/compat-tokens.json \
          MYMCP_PROTECTED_PATHS=/tmp/mymcp-compat-protected MYMCP_PORT=18770 \
            /tmp/mymcp-go serve & sleep 2
      - name: run compat suite
        run: |
          MYMCP_COMPAT_URL=http://127.0.0.1:18770 \
          MYMCP_COMPAT_RW_TOKEN=tok_compat_rw_0000000000000000 \
          MYMCP_COMPAT_RO_TOKEN=tok_compat_ro_0000000000000000 \
            pytest tests/compat/ -v --benchmark-disable
```

Also create `tests/compat/ci-tokens.json` with the exact seed content from Step 3. (No `__init__.py` needed — each test module reads its env vars directly or via conftest fixtures.)

- [ ] **Step 6: CHANGELOG**

Under `## [Unreleased]` in `CHANGELOG.md` add:

```markdown
### Added
- (#TBD-PR) Go core M1 (read-only): `go/` module serving MCP over Streamable
  HTTP with token auth and `read_file` / `glob` / `grep`, behavior-compatible
  with the Python core; black-box compat suite (`tests/compat/`) runs against
  both implementations in CI. Part of the v3 Go rewrite
  (`docs/superpowers/specs/2026-07-04-go-core-rewrite-design.md`).
```

(Replace `#TBD-PR` with the real PR number when opening the PR.)

- [ ] **Step 7: Full local gate, commit, push**

```bash
cd go && gofmt -l . && go vet ./... && go test ./... && cd ..
.venv/bin/python -m pytest tests/ --benchmark-disable -q   # python suite unaffected
git add tests/compat/ .github/workflows/ci.yml CHANGELOG.md
git commit -m "test(compat): black-box read-only suite wired against python and go cores

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
git push -u origin feat/go-core-m1
```

---

## M1 Acceptance (from the spec)

- [ ] `compat-python` and `compat-go` CI jobs both green on the PR.
- [ ] A real MCP client (e.g. Claude Code) can connect to the Go server with a seeded token and use read_file/glob/grep end-to-end.
- [ ] `go test ./...`, gofmt, vet clean; Python suite untouched and green.
