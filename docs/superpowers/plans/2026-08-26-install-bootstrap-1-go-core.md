# Install Bootstrap — Plan 1: Go Configuration Engine and Guidance

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the `mymcp` binary a `mymcp init` wizard that takes a host from "binary on disk" to "running authenticated systemd service with a pasteable client config", a `mymcp doctor` that proves it works, and status-aware CLI output so a `pipx` user is never left guessing.

**Architecture:** A new `go/internal/setup` package holds every configuration decision. `wizard.go` turns a TTY session (or flags) into a `Plan` struct; `apply.go` turns a `Plan` into ordered idempotent filesystem/systemd steps; `system.go` is the only file that execs external commands, so everything else is testable against `t.TempDir()` with no TTY and no root. `doctor.go` reuses the same `System` seam.

**Tech Stack:** Go 1.25.0, stdlib only (`embed`, `text/template`, `os/exec`, `net/http`, `bufio`) — no new module dependencies. Python side: one flag addition to `mymcp-recorder --install-unit` (argparse, no new deps).

**Spec:** `docs/superpowers/specs/2026-08-26-install-bootstrap-design.md`

## Global Constraints

Every task's requirements implicitly include this section. Values are copied verbatim from the spec.

- **No new Go module dependencies.** `go/internal/setup` uses stdlib + `os/exec` only. Do not add `golang.org/x/term`; secret input disables echo by shelling out to `stty -echo`.
- **Defaults:** bind `0.0.0.0`, port `8765`, service user `root`, config dir `/etc/mymcp`, log dir `/var/log/mymcp`, recorder data dir `/var/lib/mymcp/recorder`, first client token name `default` role `rw`, recorder disabled.
- **File modes:** `.env` and `tokens.json` are `0600`; the three directories are `0750`; the systemd unit is `0644`.
- **`MYMCP_AUDIT_ENABLED=true` is always written explicitly** when audit is on. `go/internal/config/config.go` defaults it to `false`, so relying on the default ships an unaudited server — the project's stated SOC red line.
- **No logrotate config is ever installed.** `go/internal/audit/audit.go` self-rotates on `maxBytes`/`backupCount`; a logrotate rule would fight the writer for the same files.
- **Interactive prompts read `/dev/tty`, never stdin.** Under `curl … | sudo bash` stdin is the pipe. If `/dev/tty` cannot be opened, error and demand `-yes`.
- **Exit codes:** `0` success, `1` apply/check failure, `2` usage error.
- **Go gates after every task:** `cd go && go test ./... && go vet ./... && gofmt -l .` (the last must print nothing).
- **Python gates (Task 10 only):** run through the venv — `.venv/bin/python -m pytest tests/ -v --benchmark-disable`, `.venv/bin/python -m mypy src/mymcp`, `.venv/bin/python -m ruff check .`
- **The `rw` role sees 9 tools**; `tools/list` is role-filtered by `readTools`/`writeTools` in `go/internal/mcpserver/mcpserver.go`, so an `ro` token sees fewer.

---

## File Structure

| File | Responsibility |
|---|---|
| `go/internal/setup/plan.go` | `Plan` + `RecorderPlan` structs and their derived paths. The only thing `wizard.go` and `apply.go` share. |
| `go/internal/setup/system.go` | `System` interface (`Run`, `LookPath`) + real implementation. The only file that execs. |
| `go/internal/setup/fake_system_test.go` | `fakeSystem` test double: canned outputs, recorded calls. |
| `go/internal/setup/templates/env.tmpl` | Embedded, fully commented `.env` template. |
| `go/internal/setup/templates/mymcp.service.in` | Embedded main systemd unit template. |
| `go/internal/setup/env.go` | Render `.env`, compute owned keys, line-merge into an existing file, read back an existing admin token. |
| `go/internal/setup/unit.go` | Render the main unit. |
| `go/internal/setup/apply.go` | `Apply(plan, sys, out) ([]Result, error)` — the ordered idempotent step engine. |
| `go/internal/setup/preflight.go` | Root / systemd / existing-install / recorder-availability detection. |
| `go/internal/setup/prompt.go` | `/dev/tty` prompter: `Ask`, `AskSecret`, `Confirm`. |
| `go/internal/setup/wizard.go` | Flags → `Plan` and TTY session → `Plan`. |
| `go/internal/setup/summary.go` | The closing "here is your URL and token" block. |
| `go/internal/setup/doctor.go` | Check list, severities, remediation, text and JSON rendering. |
| `go/cmd/mymcp/main.go` | `init`, `doctor`, `config` subcommands; status-aware no-arg output; `-h`. |
| `src/mymcp/recorder/__main__.py` | `--install-unit` gains `--service-user` and `--env-file`. |

---

## Task 1: `Plan` struct and the `System` exec seam

**Files:**
- Create: `go/internal/setup/plan.go`
- Create: `go/internal/setup/system.go`
- Test: `go/internal/setup/fake_system_test.go`, `go/internal/setup/plan_test.go`

**Interfaces:**
- Consumes: nothing.
- Produces: `setup.Plan`, `setup.RecorderPlan`, `setup.DefaultPlan() *Plan`, `Plan.EnvPath() string`, `Plan.TokenPath() string`, `Plan.UnitPath() string`, `Plan.RecorderUnitPath() string`, `setup.System` interface with `Run(name string, args ...string) (string, error)` and `LookPath(file string) (string, error)`, `setup.RealSystem() System`, and the test-only `newFakeSystem() *fakeSystem`.

- [ ] **Step 1: Write the failing test**

`go/internal/setup/plan_test.go`:

```go
package setup

import "testing"

func TestDefaultPlanMatchesSpecDefaults(t *testing.T) {
	p := DefaultPlan()
	if p.Bind != "0.0.0.0" || p.Port != 8765 {
		t.Fatalf("bind/port = %s:%d, want 0.0.0.0:8765", p.Bind, p.Port)
	}
	if p.ServiceUser != "root" {
		t.Fatalf("ServiceUser = %q, want root", p.ServiceUser)
	}
	if p.ConfigDir != "/etc/mymcp" || p.LogDir != "/var/log/mymcp" {
		t.Fatalf("dirs = %s, %s", p.ConfigDir, p.LogDir)
	}
	if p.RecorderDataDir != "/var/lib/mymcp/recorder" {
		t.Fatalf("RecorderDataDir = %s", p.RecorderDataDir)
	}
	if !p.AuditEnabled {
		t.Fatal("AuditEnabled must default true (config.go defaults it false)")
	}
	if p.ClientName != "default" || p.ClientRole != "rw" {
		t.Fatalf("client = %s/%s", p.ClientName, p.ClientRole)
	}
	if p.Recorder.Enabled {
		t.Fatal("recorder must default off")
	}
	if !p.Start {
		t.Fatal("Start must default true")
	}
}

func TestPlanDerivedPaths(t *testing.T) {
	p := DefaultPlan()
	p.ConfigDir = "/tmp/x"
	if got := p.EnvPath(); got != "/tmp/x/.env" {
		t.Fatalf("EnvPath = %s", got)
	}
	if got := p.TokenPath(); got != "/tmp/x/tokens.json" {
		t.Fatalf("TokenPath = %s", got)
	}
	if got := p.UnitPath(); got != "/etc/systemd/system/mymcp.service" {
		t.Fatalf("UnitPath = %s", got)
	}
	if got := p.RecorderUnitPath(); got != "/etc/systemd/system/mymcp-recorder.service" {
		t.Fatalf("RecorderUnitPath = %s", got)
	}
}
```

`go/internal/setup/fake_system_test.go`:

```go
package setup

import (
	"fmt"
	"strings"
)

// fakeSystem records every exec and answers from canned tables. It is the
// single stub boundary for the whole package: nothing else in setup/ execs.
type fakeSystem struct {
	Calls    []string          // "systemctl daemon-reload"
	Outputs  map[string]string // exact command line -> stdout
	Errors   map[string]error  // exact command line -> error
	Paths    map[string]string // LookPath name -> resolved path
	LookErrs map[string]error
}

func newFakeSystem() *fakeSystem {
	return &fakeSystem{
		Outputs:  map[string]string{},
		Errors:   map[string]error{},
		Paths:    map[string]string{},
		LookErrs: map[string]error{},
	}
}

func (f *fakeSystem) Run(name string, args ...string) (string, error) {
	line := strings.TrimSpace(name + " " + strings.Join(args, " "))
	f.Calls = append(f.Calls, line)
	if err, ok := f.Errors[line]; ok {
		return f.Outputs[line], err
	}
	return f.Outputs[line], nil
}

func (f *fakeSystem) LookPath(file string) (string, error) {
	if err, ok := f.LookErrs[file]; ok {
		return "", err
	}
	if p, ok := f.Paths[file]; ok {
		return p, nil
	}
	return "", fmt.Errorf("exec: %q: executable file not found in $PATH", file)
}

func (f *fakeSystem) ran(line string) bool {
	for _, c := range f.Calls {
		if c == line {
			return true
		}
	}
	return false
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd go && go test ./internal/setup/ -v`
Expected: FAIL — `undefined: DefaultPlan`, `undefined: Plan`.

- [ ] **Step 3: Write minimal implementation**

`go/internal/setup/plan.go`:

```go
// Package setup implements `mymcp init` and `mymcp doctor`: it renders the
// .env and systemd unit, creates directories and tokens, and verifies the
// result. Every external command goes through the System interface so the
// rest of the package is testable against t.TempDir() with no root and no TTY.
package setup

import "path/filepath"

// RecorderPlan captures the optional sidecar decisions.
type RecorderPlan struct {
	Enabled     bool
	Provider    string // anthropic | openai
	Model       string
	APIKey      string
	NeedsInject bool // true when the [recorder] extra must be installed via pipx
}

// Plan is the complete product of the wizard (or of the non-interactive
// flags). It is the only value shared between wizard.go and apply.go.
type Plan struct {
	Bind            string
	Port            int
	ServiceUser     string
	ConfigDir       string
	LogDir          string
	RecorderDataDir string
	AuditEnabled    bool
	MetricsToken    string
	ClientName      string
	ClientRole      string
	Recorder        RecorderPlan
	InstallRipgrep  bool
	RipgrepBinary   string // pre-supplied binary (offline bundle); empty = use a package manager
	Start           bool
	DryRun          bool

	// HasSystemd is false in degraded mode (containers, WSL, OpenRC): every
	// unit and start step is skipped, everything else still runs.
	HasSystemd bool
	// ExecPath is the mymcp binary the unit's ExecStart will name.
	ExecPath string
	// UnitDir is overridable so tests can write units into t.TempDir().
	UnitDir string
}

func DefaultPlan() *Plan {
	return &Plan{
		Bind:            "0.0.0.0",
		Port:            8765,
		ServiceUser:     "root",
		ConfigDir:       "/etc/mymcp",
		LogDir:          "/var/log/mymcp",
		RecorderDataDir: "/var/lib/mymcp/recorder",
		AuditEnabled:    true,
		ClientName:      "default",
		ClientRole:      "rw",
		Start:           true,
		HasSystemd:      true,
		UnitDir:         "/etc/systemd/system",
	}
}

func (p *Plan) EnvPath() string   { return filepath.Join(p.ConfigDir, ".env") }
func (p *Plan) TokenPath() string { return filepath.Join(p.ConfigDir, "tokens.json") }
func (p *Plan) UnitPath() string  { return filepath.Join(p.UnitDir, "mymcp.service") }
func (p *Plan) RecorderUnitPath() string {
	return filepath.Join(p.UnitDir, "mymcp-recorder.service")
}
```

`go/internal/setup/system.go`:

```go
package setup

import (
	"os/exec"
	"strings"
)

// System is the seam for every external command. apply.go, preflight.go,
// prompt.go and doctor.go must go through it; nothing else may exec.
type System interface {
	Run(name string, args ...string) (string, error)
	LookPath(file string) (string, error)
}

type realSystem struct{}

// RealSystem returns the production System.
func RealSystem() System { return realSystem{} }

func (realSystem) Run(name string, args ...string) (string, error) {
	out, err := exec.Command(name, args...).CombinedOutput()
	return strings.TrimRight(string(out), "\n"), err
}

func (realSystem) LookPath(file string) (string, error) { return exec.LookPath(file) }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd go && go test ./internal/setup/ -v && go vet ./internal/setup/ && gofmt -l internal/setup/`
Expected: PASS, vet clean, `gofmt -l` prints nothing.

- [ ] **Step 5: Commit**

```bash
git add go/internal/setup/
git commit -m "feat(setup): Plan struct and System exec seam"
```

---

## Task 2: `.env` template, render, and line-merge

**Files:**
- Create: `go/internal/setup/templates/env.tmpl`
- Create: `go/internal/setup/env.go`
- Test: `go/internal/setup/env_test.go`

**Interfaces:**
- Consumes: `setup.Plan` (Task 1).
- Produces: `setup.RenderEnv(p *Plan, adminToken string) string`, `setup.OwnedKeys(p *Plan, adminToken string) map[string]string`, `setup.MergeEnv(existing string, owned map[string]string) string`, `setup.ExistingAdminToken(existing string) string`.

**Merge semantics (load-bearing — the spec calls this out):** only *uncommented* `KEY=` lines count as present. A present owned key has its value replaced in place. Absent owned keys are appended under a `# --- written by mymcp init ---` marker (created once, reused on re-runs). Every other line — comments, blank lines, keys the wizard does not own — is preserved byte-for-byte in its original order.

- [ ] **Step 1: Write the failing test**

`go/internal/setup/env_test.go`:

```go
package setup

import (
	"strings"
	"testing"
)

func TestRenderEnvContainsExplicitAuditTrue(t *testing.T) {
	p := DefaultPlan()
	out := RenderEnv(p, "tok_admin")
	// config.go defaults MYMCP_AUDIT_ENABLED to false, so init must be explicit.
	if !strings.Contains(out, "\nMYMCP_AUDIT_ENABLED=true\n") {
		t.Fatalf("rendered .env must set MYMCP_AUDIT_ENABLED=true explicitly:\n%s", out)
	}
	for _, want := range []string{
		"MYMCP_HOST=0.0.0.0",
		"MYMCP_PORT=8765",
		"MYMCP_ADMIN_TOKEN=tok_admin",
		"MYMCP_TOKEN_FILE=/etc/mymcp/tokens.json",
		"MYMCP_AUDIT_LOG_DIR=/var/log/mymcp",
	} {
		if !strings.Contains(out, want) {
			t.Errorf("missing %q", want)
		}
	}
	if !strings.Contains(out, "# --- Server ---") {
		t.Error("rendered .env must keep the commented section headers")
	}
}

func TestMergeReplacesOwnedKeyInPlace(t *testing.T) {
	existing := "# my note\nMYMCP_PORT=9999\nMYMCP_PROTECTED_PATHS=/root/.ssh\n"
	got := MergeEnv(existing, map[string]string{"MYMCP_PORT": "8765"})
	if !strings.Contains(got, "MYMCP_PORT=8765") {
		t.Fatalf("owned key not replaced:\n%s", got)
	}
	if strings.Contains(got, "9999") {
		t.Fatalf("old value survived:\n%s", got)
	}
	if !strings.Contains(got, "# my note") {
		t.Error("user comment must be preserved")
	}
	if !strings.Contains(got, "MYMCP_PROTECTED_PATHS=/root/.ssh") {
		t.Error("unowned user key must be preserved verbatim")
	}
}

func TestMergeAppendsMissingKeysUnderMarkerOnce(t *testing.T) {
	first := MergeEnv("MYMCP_PORT=8765\n", map[string]string{"MYMCP_HOST": "127.0.0.1"})
	if strings.Count(first, envMarker) != 1 {
		t.Fatalf("marker should appear once:\n%s", first)
	}
	second := MergeEnv(first, map[string]string{"MYMCP_METRICS_TOKEN": "tok_m"})
	if strings.Count(second, envMarker) != 1 {
		t.Fatalf("re-run must reuse the marker, not add another:\n%s", second)
	}
	if !strings.Contains(second, "MYMCP_HOST=127.0.0.1") ||
		!strings.Contains(second, "MYMCP_METRICS_TOKEN=tok_m") {
		t.Fatalf("both appended keys must survive:\n%s", second)
	}
}

func TestMergeIgnoresCommentedOutKeys(t *testing.T) {
	// .env.example ships keys commented out; those must not be treated as present.
	got := MergeEnv("# MYMCP_PORT=8765\n", map[string]string{"MYMCP_PORT": "9000"})
	if !strings.Contains(got, "\nMYMCP_PORT=9000") {
		t.Fatalf("commented key must not satisfy the owned key:\n%s", got)
	}
	if !strings.Contains(got, "# MYMCP_PORT=8765") {
		t.Fatalf("the comment itself must be preserved:\n%s", got)
	}
}

func TestExistingAdminTokenIsFoundAndCommentsIgnored(t *testing.T) {
	if got := ExistingAdminToken("# MYMCP_ADMIN_TOKEN=tok_old\n"); got != "" {
		t.Fatalf("commented admin token must not count, got %q", got)
	}
	if got := ExistingAdminToken("MYMCP_ADMIN_TOKEN=tok_live\n"); got != "tok_live" {
		t.Fatalf("got %q, want tok_live", got)
	}
	if got := ExistingAdminToken("MYMCP_ADMIN_TOKEN=\n"); got != "" {
		t.Fatalf("empty value must count as absent, got %q", got)
	}
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd go && go test ./internal/setup/ -run 'TestRenderEnv|TestMerge|TestExistingAdmin' -v`
Expected: FAIL — `undefined: RenderEnv`, `undefined: MergeEnv`, `undefined: envMarker`.

- [ ] **Step 3: Write minimal implementation**

`go/internal/setup/templates/env.tmpl` (this becomes the source of truth for the repo's `.env.example` in Plan 2):

```
# mymcp configuration, written by `mymcp init`. All keys use the MYMCP_ prefix.
# Re-running `mymcp init` rewrites only the keys it owns and preserves
# everything else in this file, including your comments.

# --- Server ---
MYMCP_HOST={{.Bind}}
MYMCP_PORT={{.Port}}

# --- Auth ---
MYMCP_ADMIN_TOKEN={{.AdminToken}}
MYMCP_TOKEN_FILE={{.TokenFile}}

# --- Paths ---
MYMCP_AUDIT_LOG_DIR={{.LogDir}}
# Extra paths file tools may not touch (the audit log dir is always protected).
# MYMCP_PROTECTED_PATHS=/etc/shadow,/root/.ssh

# --- Audit logging ---
# mymcp rotates this log itself (MYMCP_AUDIT_MAX_BYTES / _BACKUP_COUNT).
# Do NOT add a logrotate rule for it; the two would fight over the same files.
MYMCP_AUDIT_ENABLED={{.AuditEnabled}}
# MYMCP_AUDIT_MAX_BYTES=10485760
# MYMCP_AUDIT_BACKUP_COUNT=5

# --- Observability ---
# When set, /metrics requires Bearer <this>.
MYMCP_METRICS_TOKEN={{.MetricsToken}}

# --- File transfer ---
# MYMCP_TRANSFER_ENABLED=true
# Used to build absolute ticket URLs in responses.
# MYMCP_PUBLIC_BASE_URL=https://mymcp.example.com

# --- Recorder sidecar (optional) ---
MYMCP_RECORDER_ENABLED={{.RecorderEnabled}}
MYMCP_RECORDER_DATA_DIR={{.RecorderDataDir}}
{{- if .RecorderEnabled}}
MYMCP_RECORDER_LLM_PROVIDER={{.RecorderProvider}}
MYMCP_RECORDER_LLM_MODEL={{.RecorderModel}}
MYMCP_RECORDER_LLM_API_KEY={{.RecorderAPIKey}}
{{- end}}
# MYMCP_RECORDER_MERGE_INTERVAL_SEC=300
```

`go/internal/setup/env.go`:

```go
package setup

import (
	_ "embed"
	"fmt"
	"sort"
	"strings"
	"text/template"
)

//go:embed templates/env.tmpl
var envTemplate string

// envMarker heads the block where MergeEnv appends keys that the existing
// file did not already define. It is created once and reused on re-runs.
const envMarker = "# --- written by mymcp init ---"

type envFields struct {
	Bind, LogDir, TokenFile, MetricsToken string
	Port                                  int
	AdminToken                            string
	AuditEnabled                          bool
	RecorderEnabled                       bool
	RecorderDataDir                       string
	RecorderProvider                      string
	RecorderModel                         string
	RecorderAPIKey                        string
}

func fieldsFor(p *Plan, adminToken string) envFields {
	return envFields{
		Bind:             p.Bind,
		Port:             p.Port,
		AdminToken:       adminToken,
		TokenFile:        p.TokenPath(),
		LogDir:           p.LogDir,
		MetricsToken:     p.MetricsToken,
		AuditEnabled:     p.AuditEnabled,
		RecorderEnabled:  p.Recorder.Enabled,
		RecorderDataDir:  p.RecorderDataDir,
		RecorderProvider: p.Recorder.Provider,
		RecorderModel:    p.Recorder.Model,
		RecorderAPIKey:   p.Recorder.APIKey,
	}
}

// RenderEnv produces a complete, commented .env for a fresh install.
func RenderEnv(p *Plan, adminToken string) string {
	t := template.Must(template.New("env").Parse(envTemplate))
	var sb strings.Builder
	if err := t.Execute(&sb, fieldsFor(p, adminToken)); err != nil {
		panic(fmt.Sprintf("bad embedded env template: %v", err))
	}
	return sb.String()
}

// OwnedKeys are the only keys MergeEnv will rewrite in an existing file.
func OwnedKeys(p *Plan, adminToken string) map[string]string {
	m := map[string]string{
		"MYMCP_HOST":               p.Bind,
		"MYMCP_PORT":               fmt.Sprintf("%d", p.Port),
		"MYMCP_ADMIN_TOKEN":        adminToken,
		"MYMCP_TOKEN_FILE":         p.TokenPath(),
		"MYMCP_AUDIT_LOG_DIR":      p.LogDir,
		"MYMCP_AUDIT_ENABLED":      fmt.Sprintf("%t", p.AuditEnabled),
		"MYMCP_METRICS_TOKEN":      p.MetricsToken,
		"MYMCP_RECORDER_ENABLED":   fmt.Sprintf("%t", p.Recorder.Enabled),
		"MYMCP_RECORDER_DATA_DIR":  p.RecorderDataDir,
	}
	if p.Recorder.Enabled {
		m["MYMCP_RECORDER_LLM_PROVIDER"] = p.Recorder.Provider
		m["MYMCP_RECORDER_LLM_MODEL"] = p.Recorder.Model
		m["MYMCP_RECORDER_LLM_API_KEY"] = p.Recorder.APIKey
	}
	return m
}

// keyOf returns the key of an uncommented "KEY=value" line, else "".
func keyOf(line string) string {
	t := strings.TrimSpace(line)
	if t == "" || strings.HasPrefix(t, "#") {
		return ""
	}
	k, _, ok := strings.Cut(t, "=")
	if !ok {
		return ""
	}
	return strings.TrimSpace(k)
}

// MergeEnv rewrites owned keys in place and appends the rest under envMarker,
// preserving every other line — comments, blanks, and unowned user keys —
// byte-for-byte in its original order.
func MergeEnv(existing string, owned map[string]string) string {
	lines := strings.Split(existing, "\n")
	seen := map[string]bool{}
	for i, line := range lines {
		k := keyOf(line)
		if k == "" {
			continue
		}
		if v, ok := owned[k]; ok && !seen[k] {
			lines[i] = k + "=" + v
			seen[k] = true
		}
	}
	var missing []string
	for k := range owned {
		if !seen[k] {
			missing = append(missing, k)
		}
	}
	sort.Strings(missing) // deterministic output for tests and diffs
	out := strings.Join(lines, "\n")
	if len(missing) == 0 {
		return out
	}
	if !strings.HasSuffix(out, "\n") {
		out += "\n"
	}
	if !strings.Contains(out, envMarker) {
		out += "\n" + envMarker + "\n"
	}
	for _, k := range missing {
		out += k + "=" + owned[k] + "\n"
	}
	return out
}

// ExistingAdminToken returns a non-empty uncommented MYMCP_ADMIN_TOKEN value,
// else "". Re-running init must never regenerate a live admin token.
func ExistingAdminToken(existing string) string {
	for _, line := range strings.Split(existing, "\n") {
		if keyOf(line) != "MYMCP_ADMIN_TOKEN" {
			continue
		}
		_, v, _ := strings.Cut(strings.TrimSpace(line), "=")
		if v = strings.TrimSpace(v); v != "" {
			return v
		}
	}
	return ""
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd go && go test ./internal/setup/ -v && gofmt -l internal/setup/`
Expected: PASS; `gofmt -l` prints nothing (note: fix the alignment in the `OwnedKeys` map literal if gofmt complains).

- [ ] **Step 5: Commit**

```bash
git add go/internal/setup/env.go go/internal/setup/env_test.go go/internal/setup/templates/env.tmpl
git commit -m "feat(setup): embedded .env template with idempotent line merge"
```

---

## Task 3: systemd unit template and rendering

**Files:**
- Create: `go/internal/setup/templates/mymcp.service.in`
- Create: `go/internal/setup/unit.go`
- Test: `go/internal/setup/unit_test.go`

**Interfaces:**
- Consumes: `setup.Plan` (Task 1).
- Produces: `setup.RenderUnit(p *Plan) string`.

- [ ] **Step 1: Write the failing test**

`go/internal/setup/unit_test.go`:

```go
package setup

import (
	"strings"
	"testing"
)

func TestRenderUnitUsesResolvedBinaryAndEnvFile(t *testing.T) {
	p := DefaultPlan()
	p.ExecPath = "/usr/local/bin/mymcp"
	got := RenderUnit(p)
	want := "ExecStart=/usr/local/bin/mymcp serve -env-file /etc/mymcp/.env"
	if !strings.Contains(got, want) {
		t.Fatalf("missing %q in:\n%s", want, got)
	}
	if !strings.Contains(got, "EnvironmentFile=/etc/mymcp/.env") {
		t.Errorf("missing EnvironmentFile:\n%s", got)
	}
	if !strings.Contains(got, "User=root") {
		t.Errorf("default service user must be written explicitly:\n%s", got)
	}
	if !strings.Contains(got, "WorkingDirectory=/etc/mymcp") {
		t.Errorf("missing WorkingDirectory:\n%s", got)
	}
	if !strings.Contains(got, "WantedBy=multi-user.target") {
		t.Errorf("missing [Install]:\n%s", got)
	}
}

func TestRenderUnitHonoursNonRootServiceUser(t *testing.T) {
	p := DefaultPlan()
	p.ExecPath = "/usr/local/bin/mymcp"
	p.ServiceUser = "mymcp"
	got := RenderUnit(p)
	if !strings.Contains(got, "User=mymcp") {
		t.Fatalf("User not honoured:\n%s", got)
	}
}

func TestRenderUnitDoesNotSetNoNewPrivileges(t *testing.T) {
	// The main service exists to run privileged host commands via bash_execute;
	// NoNewPrivileges would break sudo-style escalation inside tool calls.
	// (The recorder unit, which never executes tools, does set it.)
	p := DefaultPlan()
	p.ExecPath = "/usr/local/bin/mymcp"
	if strings.Contains(RenderUnit(p), "NoNewPrivileges") {
		t.Fatal("main unit must not set NoNewPrivileges")
	}
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd go && go test ./internal/setup/ -run TestRenderUnit -v`
Expected: FAIL — `undefined: RenderUnit`.

- [ ] **Step 3: Write minimal implementation**

`go/internal/setup/templates/mymcp.service.in`:

```
[Unit]
Description=MyMCP Server (Linux system tools over MCP)
After=network.target

[Service]
Type=simple
User={{.ServiceUser}}
WorkingDirectory={{.ConfigDir}}
EnvironmentFile={{.EnvPath}}
ExecStart={{.ExecPath}} serve -env-file {{.EnvPath}}
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```

`go/internal/setup/unit.go`:

```go
package setup

import (
	_ "embed"
	"fmt"
	"strings"
	"text/template"
)

//go:embed templates/mymcp.service.in
var unitTemplate string

type unitFields struct {
	ServiceUser, ConfigDir, EnvPath, ExecPath string
}

// RenderUnit renders the main systemd unit. The recorder unit is NOT rendered
// here: src/mymcp/recorder/templates/mymcp-recorder.service.in stays the single
// source of truth for it, and apply.go shells out to
// `mymcp-recorder --install-unit` instead (see the design spec).
func RenderUnit(p *Plan) string {
	t := template.Must(template.New("unit").Parse(unitTemplate))
	var sb strings.Builder
	err := t.Execute(&sb, unitFields{
		ServiceUser: p.ServiceUser,
		ConfigDir:   p.ConfigDir,
		EnvPath:     p.EnvPath(),
		ExecPath:    p.ExecPath,
	})
	if err != nil {
		panic(fmt.Sprintf("bad embedded unit template: %v", err))
	}
	return sb.String()
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd go && go test ./internal/setup/ -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add go/internal/setup/unit.go go/internal/setup/unit_test.go go/internal/setup/templates/mymcp.service.in
git commit -m "feat(setup): embedded systemd unit template"
```

---

## Task 4: the idempotent apply engine

**Files:**
- Create: `go/internal/setup/apply.go`
- Test: `go/internal/setup/apply_test.go`

**Interfaces:**
- Consumes: `Plan`, `System` (Task 1); `RenderEnv`, `MergeEnv`, `OwnedKeys`, `ExistingAdminToken` (Task 2); `RenderUnit` (Task 3); `auth.NewTokenStore`, `auth.GenerateToken`, `store.CreateToken`, `store.ListTokens` from `go/internal/auth`.
- Produces: `setup.Status` (`StatusCreated`/`StatusUpdated`/`StatusUnchanged`/`StatusSkipped`), `setup.Result{Step, Status, Detail string}`, `setup.ApplyOutcome{Results []Result, AdminToken, ClientToken string}`, `setup.Apply(p *Plan, sys System) (ApplyOutcome, error)`.

The three load-bearing idempotency rules from the spec: admin token generated only when absent; client token deduplicated by name; `.env` line-merged with a `.env.bak-<timestamp>` written first.

- [ ] **Step 1: Write the failing test**

`go/internal/setup/apply_test.go`:

```go
package setup

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

// tempPlan returns a Plan whose every path lives under t.TempDir(), so Apply
// can run unprivileged in CI.
func tempPlan(t *testing.T) (*Plan, *fakeSystem) {
	t.Helper()
	root := t.TempDir()
	p := DefaultPlan()
	p.ConfigDir = filepath.Join(root, "etc")
	p.LogDir = filepath.Join(root, "log")
	p.RecorderDataDir = filepath.Join(root, "lib", "recorder")
	p.UnitDir = filepath.Join(root, "units")
	p.ExecPath = "/usr/local/bin/mymcp"
	p.Start = false
	if err := os.MkdirAll(p.UnitDir, 0o755); err != nil {
		t.Fatal(err)
	}
	return p, newFakeSystem()
}

func TestApplyCreatesEverythingOnFreshHost(t *testing.T) {
	p, sys := tempPlan(t)
	out, err := Apply(p, sys)
	if err != nil {
		t.Fatalf("Apply: %v", err)
	}
	for _, path := range []string{p.EnvPath(), p.TokenPath(), p.UnitPath()} {
		if _, err := os.Stat(path); err != nil {
			t.Errorf("expected %s to exist: %v", path, err)
		}
	}
	if out.AdminToken == "" || out.ClientToken == "" {
		t.Fatalf("both tokens must be reported: %+v", out)
	}
	st, err := os.Stat(p.EnvPath())
	if err != nil {
		t.Fatal(err)
	}
	if st.Mode().Perm() != 0o600 {
		t.Errorf(".env mode = %o, want 600 (it holds the admin token)", st.Mode().Perm())
	}
	if !sys.ran("systemctl daemon-reload") {
		t.Errorf("daemon-reload not issued; calls=%v", sys.Calls)
	}
}

func TestApplyIsIdempotentAndKeepsAdminToken(t *testing.T) {
	p, sys := tempPlan(t)
	first, err := Apply(p, sys)
	if err != nil {
		t.Fatal(err)
	}
	second, err := Apply(p, newFakeSystem())
	if err != nil {
		t.Fatal(err)
	}
	if second.AdminToken != first.AdminToken {
		t.Fatalf("admin token changed on re-run (%s -> %s); every existing admin client would break",
			first.AdminToken, second.AdminToken)
	}
	if second.ClientToken != first.ClientToken {
		t.Fatalf("client token %q duplicated on re-run (was %q)", second.ClientToken, first.ClientToken)
	}
	for _, r := range second.Results {
		if r.Status == StatusCreated {
			t.Errorf("step %q reported created on an unchanged re-run", r.Step)
		}
	}
	_ = sys
}

func TestApplyPreservesHandEditedEnvKeysAndBacksUp(t *testing.T) {
	p, sys := tempPlan(t)
	if _, err := Apply(p, sys); err != nil {
		t.Fatal(err)
	}
	// Operator hand-edits the file afterwards.
	raw, err := os.ReadFile(p.EnvPath())
	if err != nil {
		t.Fatal(err)
	}
	edited := string(raw) + "\nMYMCP_PROTECTED_PATHS=/root/.ssh\n# keep me\n"
	if err := os.WriteFile(p.EnvPath(), []byte(edited), 0o600); err != nil {
		t.Fatal(err)
	}

	p.Port = 9000
	if _, err := Apply(p, newFakeSystem()); err != nil {
		t.Fatal(err)
	}
	got, err := os.ReadFile(p.EnvPath())
	if err != nil {
		t.Fatal(err)
	}
	s := string(got)
	if !strings.Contains(s, "MYMCP_PORT=9000") {
		t.Errorf("owned key not updated:\n%s", s)
	}
	if !strings.Contains(s, "MYMCP_PROTECTED_PATHS=/root/.ssh") || !strings.Contains(s, "# keep me") {
		t.Errorf("hand-edited content was lost:\n%s", s)
	}
	matches, _ := filepath.Glob(filepath.Join(p.ConfigDir, ".env.bak-*"))
	if len(matches) == 0 {
		t.Error("no .env.bak-<timestamp> written before the merge")
	}
}

func TestApplyDryRunWritesNothing(t *testing.T) {
	p, sys := tempPlan(t)
	p.DryRun = true
	if _, err := Apply(p, sys); err != nil {
		t.Fatal(err)
	}
	if _, err := os.Stat(p.EnvPath()); !os.IsNotExist(err) {
		t.Errorf(".env must not exist after -dry-run (err=%v)", err)
	}
	if len(sys.Calls) != 0 {
		t.Errorf("-dry-run must exec nothing, got %v", sys.Calls)
	}
}

func TestApplyDegradedModeSkipsUnitAndSystemctl(t *testing.T) {
	p, sys := tempPlan(t)
	p.HasSystemd = false
	out, err := Apply(p, sys)
	if err != nil {
		t.Fatalf("degraded mode must not fail: %v", err)
	}
	if _, err := os.Stat(p.UnitPath()); !os.IsNotExist(err) {
		t.Error("no unit may be written without systemd")
	}
	if len(sys.Calls) != 0 {
		t.Errorf("no systemctl calls without systemd, got %v", sys.Calls)
	}
	if _, err := os.Stat(p.EnvPath()); err != nil {
		t.Error(".env must still be written in degraded mode")
	}
	var skipped bool
	for _, r := range out.Results {
		if r.Status == StatusSkipped {
			skipped = true
		}
	}
	if !skipped {
		t.Error("degraded mode must report skipped steps, not silently omit them")
	}
}

func TestApplyInstallsRipgrepViaDetectedPackageManager(t *testing.T) {
	p, sys := tempPlan(t)
	p.InstallRipgrep = true
	sys.Paths["apt-get"] = "/usr/bin/apt-get"
	if _, err := Apply(p, sys); err != nil {
		t.Fatal(err)
	}
	if !sys.ran("apt-get install -y ripgrep") {
		t.Fatalf("ripgrep not installed; calls=%v", sys.Calls)
	}
}

func TestApplyPrefersSuppliedRipgrepBinaryOverPackageManager(t *testing.T) {
	p, sys := tempPlan(t)
	p.InstallRipgrep = true
	src := filepath.Join(t.TempDir(), "rg")
	if err := os.WriteFile(src, []byte("#!/bin/sh\n"), 0o755); err != nil {
		t.Fatal(err)
	}
	p.RipgrepBinary = src
	sys.Paths["apt-get"] = "/usr/bin/apt-get"
	if _, err := Apply(p, sys); err != nil {
		t.Fatal(err)
	}
	for _, c := range sys.Calls {
		if strings.Contains(c, "install") {
			t.Fatalf("air-gapped host must not shell out to a package manager: %v", sys.Calls)
		}
	}
	if _, err := os.Stat("/usr/local/bin/rg"); err != nil {
		t.Skip("cannot write /usr/local/bin unprivileged; covered by the e2e smoke instead")
	}
}

func TestApplySkipsRipgrepWhenNotRequested(t *testing.T) {
	p, sys := tempPlan(t)
	sys.Paths["apt-get"] = "/usr/bin/apt-get"
	if _, err := Apply(p, sys); err != nil {
		t.Fatal(err)
	}
	for _, c := range sys.Calls {
		if strings.Contains(c, "ripgrep") {
			t.Fatalf("ripgrep install not requested but ran %q", c)
		}
	}
}

func TestApplyCreatesServiceUserOnlyWhenNotRoot(t *testing.T) {
	p, sys := tempPlan(t)
	p.ServiceUser = "mymcp"
	sys.Paths["useradd"] = "/usr/sbin/useradd"
	sys.Errors["id -u mymcp"] = errNoSuchUser
	if _, err := Apply(p, sys); err != nil {
		t.Fatal(err)
	}
	if !sys.ran("useradd -r -s /usr/sbin/nologin mymcp") {
		t.Fatalf("service user not created; calls=%v", sys.Calls)
	}

	p2, sys2 := tempPlan(t)
	if _, err := Apply(p2, sys2); err != nil {
		t.Fatal(err)
	}
	for _, c := range sys2.Calls {
		if strings.HasPrefix(c, "useradd") {
			t.Fatalf("must not useradd for the root service user: %v", sys2.Calls)
		}
	}
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd go && go test ./internal/setup/ -run TestApply -v`
Expected: FAIL — `undefined: Apply`, `undefined: errNoSuchUser`, `undefined: StatusCreated`.

- [ ] **Step 3: Write minimal implementation**

`go/internal/setup/apply.go`:

```go
package setup

import (
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"time"

	"github.com/algony-tony/mymcp/go/internal/auth"
)

type Status string

const (
	StatusCreated   Status = "created"
	StatusUpdated   Status = "updated"
	StatusUnchanged Status = "unchanged"
	StatusSkipped   Status = "skipped"
)

// errNoSuchUser is what a fake System returns for `id -u <name>` when the
// account does not exist; the real System returns exec's non-zero exit error.
var errNoSuchUser = errors.New("no such user")

type Result struct {
	Step   string
	Status Status
	Detail string
}

type ApplyOutcome struct {
	Results     []Result
	AdminToken  string
	ClientToken string
}

// Apply runs every step in order. Each step is idempotent, so a run that fails
// halfway can simply be re-run: there is deliberately no rollback, because a
// partial rollback is more dangerous than none.
func Apply(p *Plan, sys System) (ApplyOutcome, error) {
	out := ApplyOutcome{}
	add := func(step string, st Status, detail string) {
		out.Results = append(out.Results, Result{Step: step, Status: st, Detail: detail})
	}

	// 1. Service user.
	if p.ServiceUser != "root" && p.ServiceUser != "" {
		if _, err := sys.Run("id", "-u", p.ServiceUser); err != nil {
			if p.DryRun {
				add("service user", StatusSkipped, "would useradd "+p.ServiceUser)
			} else if _, err := sys.Run("useradd", "-r", "-s", "/usr/sbin/nologin", p.ServiceUser); err != nil {
				return out, fmt.Errorf("useradd %s: %w", p.ServiceUser, err)
			} else {
				add("service user", StatusCreated, p.ServiceUser)
			}
		} else {
			add("service user", StatusUnchanged, p.ServiceUser)
		}
	}

	// 2. Directories.
	for _, dir := range []string{p.ConfigDir, p.LogDir, p.RecorderDataDir} {
		st, err := ensureDir(p, dir)
		if err != nil {
			return out, err
		}
		add("dir "+dir, st, "")
	}

	// 3. .env — line-merged, never overwritten; admin token preserved.
	existing, _ := os.ReadFile(p.EnvPath())
	admin := ExistingAdminToken(string(existing))
	if admin == "" {
		tok, err := auth.GenerateToken()
		if err != nil {
			return out, err
		}
		admin = tok
	}
	out.AdminToken = admin

	var content string
	status := StatusUpdated
	if len(existing) == 0 {
		content = RenderEnv(p, admin)
		status = StatusCreated
	} else {
		content = MergeEnv(string(existing), OwnedKeys(p, admin))
		if content == string(existing) {
			status = StatusUnchanged
		}
	}
	if p.DryRun {
		add("env "+p.EnvPath(), StatusSkipped, "would write (dry-run)")
	} else {
		if status != StatusUnchanged {
			if len(existing) > 0 {
				bak := fmt.Sprintf("%s.bak-%s", p.EnvPath(), time.Now().UTC().Format("20060102T150405Z"))
				if err := os.WriteFile(bak, existing, 0o600); err != nil {
					return out, fmt.Errorf("backup %s: %w", bak, err)
				}
			}
			if err := os.WriteFile(p.EnvPath(), []byte(content), 0o600); err != nil {
				return out, fmt.Errorf("write %s: %w", p.EnvPath(), err)
			}
		}
		add("env "+p.EnvPath(), status, "")
	}

	// 4+5. Token store and the first client token, deduplicated by name.
	if p.DryRun {
		add("client token", StatusSkipped, "would create "+p.ClientName)
	} else {
		store, err := auth.NewTokenStore(p.TokenPath(), admin)
		if err != nil {
			return out, err
		}
		for tok, info := range store.ListTokens() {
			if info.Name == p.ClientName {
				out.ClientToken = tok
			}
		}
		if out.ClientToken == "" {
			tok, err := store.CreateToken(p.ClientName, p.ClientRole)
			if err != nil {
				return out, err
			}
			out.ClientToken = tok
			add("client token", StatusCreated, p.ClientName+" ("+p.ClientRole+")")
		} else {
			add("client token", StatusUnchanged, p.ClientName)
		}
	}

	// 5b. ripgrep. Optional: grep falls back to a native scan without it.
	if p.InstallRipgrep && !p.DryRun {
		st, detail, err := installRipgrep(p, sys)
		if err != nil {
			// A missing rg degrades grep; it must never fail the install.
			add("ripgrep", StatusSkipped, err.Error())
		} else {
			add("ripgrep", st, detail)
		}
	}

	// 6. Unit + daemon-reload. Skipped wholesale in degraded mode.
	if !p.HasSystemd {
		add("systemd unit", StatusSkipped, "systemd not present")
		add("service start", StatusSkipped, "systemd not present")
		return out, nil
	}
	st, err := writeIfChanged(p, p.UnitPath(), RenderUnit(p), 0o644)
	if err != nil {
		return out, err
	}
	add("systemd unit", st, p.UnitPath())
	if !p.DryRun {
		if _, err := sys.Run("systemctl", "daemon-reload"); err != nil {
			return out, fmt.Errorf("systemctl daemon-reload: %w", err)
		}
	}

	// 7. Start.
	if !p.Start || p.DryRun {
		add("service start", StatusSkipped, "-start=false or dry-run")
		return out, nil
	}
	if _, err := sys.Run("systemctl", "enable", "--now", "mymcp"); err != nil {
		return out, fmt.Errorf("systemctl enable --now mymcp: %w", err)
	}
	if _, err := sys.Run("systemctl", "restart", "mymcp"); err != nil {
		return out, fmt.Errorf("systemctl restart mymcp: %w", err)
	}
	add("service start", StatusUpdated, "enabled and running")
	return out, nil
}

// installRipgrep prefers a binary handed to us (the offline bundle's) and
// otherwise asks whichever package manager this distro has.
func installRipgrep(p *Plan, sys System) (Status, string, error) {
	if _, err := sys.LookPath("rg"); err == nil {
		return StatusUnchanged, "already installed", nil
	}
	if p.RipgrepBinary != "" {
		raw, err := os.ReadFile(p.RipgrepBinary)
		if err != nil {
			return StatusSkipped, "", err
		}
		dest := "/usr/local/bin/rg"
		if err := os.WriteFile(dest, raw, 0o755); err != nil {
			return StatusSkipped, "", err
		}
		return StatusCreated, dest, nil
	}
	for _, pm := range [][]string{
		{"apt-get", "install", "-y", "ripgrep"},
		{"dnf", "install", "-y", "ripgrep"},
		{"pacman", "-S", "--noconfirm", "ripgrep"},
	} {
		if _, err := sys.LookPath(pm[0]); err != nil {
			continue
		}
		if _, err := sys.Run(pm[0], pm[1:]...); err != nil {
			return StatusSkipped, "", fmt.Errorf("%s: %w", pm[0], err)
		}
		return StatusCreated, pm[0], nil
	}
	return StatusSkipped, "", fmt.Errorf("no supported package manager found")
}

func ensureDir(p *Plan, dir string) (Status, error) {
	if _, err := os.Stat(dir); err == nil {
		return StatusUnchanged, nil
	}
	if p.DryRun {
		return StatusSkipped, nil
	}
	if err := os.MkdirAll(dir, 0o750); err != nil {
		return StatusUnchanged, fmt.Errorf("mkdir %s: %w", dir, err)
	}
	return StatusCreated, nil
}

func writeIfChanged(p *Plan, path, content string, mode os.FileMode) (Status, error) {
	old, err := os.ReadFile(path)
	if err == nil && string(old) == content {
		return StatusUnchanged, nil
	}
	if p.DryRun {
		return StatusSkipped, nil
	}
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		return StatusUnchanged, err
	}
	if err := os.WriteFile(path, []byte(content), mode); err != nil {
		return StatusUnchanged, fmt.Errorf("write %s: %w", path, err)
	}
	if len(old) == 0 {
		return StatusCreated, nil
	}
	return StatusUpdated, nil
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd go && go test ./internal/setup/ -v && go vet ./... && gofmt -l .`
Expected: PASS, vet clean, gofmt silent.

- [ ] **Step 5: Commit**

```bash
git add go/internal/setup/apply.go go/internal/setup/apply_test.go
git commit -m "feat(setup): idempotent apply engine with .env merge and token reuse"
```

---

## Task 5: preflight detection and the `/dev/tty` prompter

**Files:**
- Create: `go/internal/setup/preflight.go`
- Create: `go/internal/setup/prompt.go`
- Test: `go/internal/setup/preflight_test.go`, `go/internal/setup/prompt_test.go`

**Interfaces:**
- Consumes: `System` (Task 1).
- Produces: `setup.RecorderAvail` (`RecorderReady`/`RecorderViaPipx`/`RecorderUnavailable`), `setup.Preflight{IsRoot, HasSystemd bool; ExistingEnv string; Recorder RecorderAvail}`, `setup.RunPreflight(configDir string, sys System) Preflight`, `setup.Prompter` with `Ask(question, def string) string`, `AskSecret(question string) string`, `Confirm(question string, def bool) bool`, `setup.NewPrompter(r io.Reader, w io.Writer, sys System) *Prompter`, `setup.OpenTTYPrompter(sys System) (*Prompter, error)`.

The recorder availability rule from the spec, verbatim: `mymcp-recorder` on `PATH` → `RecorderReady` (offer it, no dependency install); else `pipx` on `PATH` → `RecorderViaPipx` (offer it, run `pipx inject`); else `RecorderUnavailable` (skip the question with an explanation).

- [ ] **Step 1: Write the failing test**

`go/internal/setup/preflight_test.go`:

```go
package setup

import (
	"os"
	"path/filepath"
	"testing"
)

func TestRecorderAvailabilityThreeWayRule(t *testing.T) {
	cases := []struct {
		name    string
		paths   map[string]string
		want    RecorderAvail
	}{
		{"recorder already installed", map[string]string{"mymcp-recorder": "/usr/local/bin/mymcp-recorder"}, RecorderReady},
		{"pipx present only", map[string]string{"pipx": "/usr/bin/pipx"}, RecorderViaPipx},
		{"neither", map[string]string{}, RecorderUnavailable},
		{"both prefers ready", map[string]string{
			"mymcp-recorder": "/usr/local/bin/mymcp-recorder",
			"pipx":           "/usr/bin/pipx",
		}, RecorderReady},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			sys := newFakeSystem()
			sys.Paths = tc.paths
			if got := RunPreflight(t.TempDir(), sys).Recorder; got != tc.want {
				t.Fatalf("Recorder = %v, want %v", got, tc.want)
			}
		})
	}
}

func TestPreflightReadsExistingEnvForUpdateMode(t *testing.T) {
	dir := t.TempDir()
	if err := os.WriteFile(filepath.Join(dir, ".env"), []byte("MYMCP_PORT=9000\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	pf := RunPreflight(dir, newFakeSystem())
	if pf.ExistingEnv == "" {
		t.Fatal("existing .env must be read so the wizard can seed its defaults")
	}
}
```

`go/internal/setup/prompt_test.go`:

```go
package setup

import (
	"bytes"
	"strings"
	"testing"
)

func TestAskReturnsDefaultOnEmptyLine(t *testing.T) {
	p := NewPrompter(strings.NewReader("\n"), &bytes.Buffer{}, newFakeSystem())
	if got := p.Ask("Port", "8765"); got != "8765" {
		t.Fatalf("Ask = %q, want the default", got)
	}
}

func TestAskTrimsAndReturnsTypedValue(t *testing.T) {
	p := NewPrompter(strings.NewReader("  9000  \n"), &bytes.Buffer{}, newFakeSystem())
	if got := p.Ask("Port", "8765"); got != "9000" {
		t.Fatalf("Ask = %q, want 9000", got)
	}
}

func TestConfirmParsesYesNoAndFallsBackToDefault(t *testing.T) {
	for _, tc := range []struct {
		in   string
		def  bool
		want bool
	}{{"y\n", false, true}, {"n\n", true, false}, {"\n", true, true}, {"\n", false, false}} {
		p := NewPrompter(strings.NewReader(tc.in), &bytes.Buffer{}, newFakeSystem())
		if got := p.Confirm("ok?", tc.def); got != tc.want {
			t.Errorf("Confirm(%q, %v) = %v, want %v", tc.in, tc.def, got, tc.want)
		}
	}
}

func TestAskSecretDisablesAndRestoresEcho(t *testing.T) {
	sys := newFakeSystem()
	p := NewPrompter(strings.NewReader("sk-secret\n"), &bytes.Buffer{}, sys)
	if got := p.AskSecret("API key"); got != "sk-secret" {
		t.Fatalf("AskSecret = %q", got)
	}
	if !sys.ran("stty -echo") || !sys.ran("stty echo") {
		t.Fatalf("echo must be disabled then restored; calls=%v", sys.Calls)
	}
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd go && go test ./internal/setup/ -run 'TestRecorderAvail|TestPreflight|TestAsk|TestConfirm' -v`
Expected: FAIL — `undefined: RunPreflight`, `undefined: NewPrompter`.

- [ ] **Step 3: Write minimal implementation**

`go/internal/setup/preflight.go`:

```go
package setup

import (
	"os"
	"path/filepath"
)

// RecorderAvail is how init decides whether to even ask about the recorder.
type RecorderAvail int

const (
	// RecorderUnavailable: no Python tooling — the raw-binary install path.
	RecorderUnavailable RecorderAvail = iota
	// RecorderViaPipx: pipx is present, so `pipx inject` can add the extra.
	RecorderViaPipx
	// RecorderReady: mymcp-recorder is already on PATH; nothing to install.
	RecorderReady
)

type Preflight struct {
	IsRoot      bool
	HasSystemd  bool
	ExistingEnv string // "" when no .env exists
	Recorder    RecorderAvail
}

// RunPreflight gathers everything init must know BEFORE asking any question.
// Failing a user after a questionnaire is hostile.
func RunPreflight(configDir string, sys System) Preflight {
	pf := Preflight{IsRoot: os.Geteuid() == 0}
	if st, err := os.Stat("/run/systemd/system"); err == nil && st.IsDir() {
		pf.HasSystemd = true
	}
	if raw, err := os.ReadFile(filepath.Join(configDir, ".env")); err == nil {
		pf.ExistingEnv = string(raw)
	}
	switch {
	case lookOK(sys, "mymcp-recorder"):
		pf.Recorder = RecorderReady
	case lookOK(sys, "pipx"):
		pf.Recorder = RecorderViaPipx
	default:
		pf.Recorder = RecorderUnavailable
	}
	return pf
}

func lookOK(sys System, name string) bool {
	_, err := sys.LookPath(name)
	return err == nil
}
```

`go/internal/setup/prompt.go`:

```go
package setup

import (
	"bufio"
	"fmt"
	"io"
	"os"
	"strings"
)

// Prompter asks questions. In production its reader is /dev/tty, never stdin:
// under `curl … | sudo bash` stdin is the pipe and a stdin prompt reads EOF.
type Prompter struct {
	in  *bufio.Reader
	out io.Writer
	sys System
	tty *os.File
}

func NewPrompter(r io.Reader, w io.Writer, sys System) *Prompter {
	return &Prompter{in: bufio.NewReader(r), out: w, sys: sys}
}

// OpenTTYPrompter opens /dev/tty. The error is the caller's cue to demand -yes.
func OpenTTYPrompter(sys System) (*Prompter, error) {
	f, err := os.OpenFile("/dev/tty", os.O_RDWR, 0)
	if err != nil {
		return nil, fmt.Errorf("no interactive terminal (/dev/tty): %w", err)
	}
	p := NewPrompter(f, f, sys)
	p.tty = f
	return p, nil
}

func (p *Prompter) Close() {
	if p.tty != nil {
		_ = p.tty.Close()
	}
}

func (p *Prompter) readLine() string {
	line, _ := p.in.ReadString('\n')
	return strings.TrimSpace(line)
}

func (p *Prompter) Ask(question, def string) string {
	if def != "" {
		fmt.Fprintf(p.out, "%s [%s]: ", question, def)
	} else {
		fmt.Fprintf(p.out, "%s: ", question)
	}
	if v := p.readLine(); v != "" {
		return v
	}
	return def
}

func (p *Prompter) Confirm(question string, def bool) bool {
	hint := "y/N"
	if def {
		hint = "Y/n"
	}
	fmt.Fprintf(p.out, "%s [%s]: ", question, hint)
	switch strings.ToLower(p.readLine()) {
	case "y", "yes":
		return true
	case "n", "no":
		return false
	default:
		return def
	}
}

// AskSecret suppresses echo via stty so an API key never lands on screen.
// stdlib has no termios helper and the project forbids new dependencies.
func (p *Prompter) AskSecret(question string) string {
	fmt.Fprintf(p.out, "%s: ", question)
	_, _ = p.sys.Run("stty", "-echo")
	v := p.readLine()
	_, _ = p.sys.Run("stty", "echo")
	fmt.Fprintln(p.out)
	return v
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd go && go test ./internal/setup/ -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add go/internal/setup/preflight.go go/internal/setup/prompt.go go/internal/setup/preflight_test.go go/internal/setup/prompt_test.go
git commit -m "feat(setup): preflight detection and /dev/tty prompter"
```

---

## Task 6: the wizard — flags and questions produce a `Plan`

**Files:**
- Create: `go/internal/setup/wizard.go`
- Test: `go/internal/setup/wizard_test.go`

**Interfaces:**
- Consumes: `Plan`, `DefaultPlan` (Task 1); `Preflight`, `RecorderAvail`, `Prompter` (Task 5).
- Produces: `setup.Options` (the parsed flag values), `setup.PlanFromOptions(o Options, pf Preflight, sys System) (*Plan, error)`, `setup.PlanFromWizard(o Options, pf Preflight, pr *Prompter, sys System) (*Plan, error)`, `setup.PortInUse(sys System, bind string, port int) bool`, `setup.FirewallHint(sys System, port int) string`, `setup.ExposureWarning(bind string) string`.

- [ ] **Step 1: Write the failing test**

`go/internal/setup/wizard_test.go`:

```go
package setup

import (
	"bytes"
	"strings"
	"testing"
)

func defaultOptions() Options {
	return Options{
		Bind: "0.0.0.0", Port: 8765, ServiceUser: "root",
		ConfigDir: "/etc/mymcp", LogDir: "/var/log/mymcp",
		RecorderDataDir: "/var/lib/mymcp/recorder",
		Audit:           true, ClientName: "default", ClientRole: "rw", Start: true,
	}
}

func TestPlanFromOptionsGeneratesMetricsTokenByDefault(t *testing.T) {
	pf := Preflight{IsRoot: true, HasSystemd: true}
	p, err := PlanFromOptions(defaultOptions(), pf, newFakeSystem())
	if err != nil {
		t.Fatal(err)
	}
	if p.MetricsToken == "" {
		t.Fatal("a metrics token must be generated by default")
	}
	if !p.HasSystemd {
		t.Fatal("HasSystemd must be carried from preflight into the Plan")
	}
}

func TestPlanFromOptionsRefusesRecorderWhenUnavailable(t *testing.T) {
	o := defaultOptions()
	o.Recorder = true
	pf := Preflight{IsRoot: true, HasSystemd: true, Recorder: RecorderUnavailable}
	if _, err := PlanFromOptions(o, pf, newFakeSystem()); err == nil {
		t.Fatal("-recorder with no pipx and no mymcp-recorder must be a clear error, not a late failure")
	}
}

func TestPlanFromOptionsSetsNeedsInjectOnlyForPipxPath(t *testing.T) {
	o := defaultOptions()
	o.Recorder = true
	o.RecorderAPIKey = "sk-test"
	viaPipx, err := PlanFromOptions(o, Preflight{IsRoot: true, HasSystemd: true, Recorder: RecorderViaPipx}, newFakeSystem())
	if err != nil {
		t.Fatal(err)
	}
	if !viaPipx.Recorder.NeedsInject {
		t.Error("pipx path must request an inject")
	}
	ready, err := PlanFromOptions(o, Preflight{IsRoot: true, HasSystemd: true, Recorder: RecorderReady}, newFakeSystem())
	if err != nil {
		t.Fatal(err)
	}
	if ready.Recorder.NeedsInject {
		t.Error("already-installed recorder must not re-inject")
	}
}

func TestWizardSeedsDefaultsFromExistingEnv(t *testing.T) {
	pf := Preflight{
		IsRoot: true, HasSystemd: true,
		ExistingEnv: "MYMCP_PORT=9000\nMYMCP_HOST=127.0.0.1\n",
	}
	// All answers blank -> every default is accepted.
	in := strings.NewReader(strings.Repeat("\n", 20))
	pr := NewPrompter(in, &bytes.Buffer{}, newFakeSystem())
	p, err := PlanFromWizard(defaultOptions(), pf, pr, newFakeSystem())
	if err != nil {
		t.Fatal(err)
	}
	if p.Port != 9000 || p.Bind != "127.0.0.1" {
		t.Fatalf("update mode must seed from the existing .env, got %s:%d", p.Bind, p.Port)
	}
}

func TestExposureWarningWildcardOnly(t *testing.T) {
	if ExposureWarning("0.0.0.0") == "" {
		t.Error("binding 0.0.0.0 must warn: a reachable port plus a token is a root shell")
	}
	if ExposureWarning("127.0.0.1") != "" {
		t.Error("loopback bind needs no exposure warning")
	}
}

func TestFirewallHintMatchesDetectedFirewall(t *testing.T) {
	ufw := newFakeSystem()
	ufw.Paths["ufw"] = "/usr/sbin/ufw"
	if got := FirewallHint(ufw, 8765); !strings.Contains(got, "ufw allow 8765") {
		t.Errorf("ufw hint = %q", got)
	}
	fw := newFakeSystem()
	fw.Paths["firewall-cmd"] = "/usr/bin/firewall-cmd"
	if got := FirewallHint(fw, 8765); !strings.Contains(got, "firewall-cmd") {
		t.Errorf("firewalld hint = %q", got)
	}
	if got := FirewallHint(newFakeSystem(), 8765); got != "" {
		t.Errorf("no firewall detected should give no hint, got %q", got)
	}
}

func TestPortInUseParsesSs(t *testing.T) {
	sys := newFakeSystem()
	sys.Outputs["ss -tlnH"] = "LISTEN 0 4096 0.0.0.0:8765 0.0.0.0:*\n"
	if !PortInUse(sys, "0.0.0.0", 8765) {
		t.Error("occupied port not detected")
	}
	if PortInUse(sys, "0.0.0.0", 9999) {
		t.Error("free port reported busy")
	}
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd go && go test ./internal/setup/ -run 'TestPlanFrom|TestWizard|TestExposure|TestFirewall|TestPortInUse' -v`
Expected: FAIL — `undefined: Options`, `undefined: PlanFromOptions`.

- [ ] **Step 3: Write minimal implementation**

`go/internal/setup/wizard.go`:

```go
package setup

import (
	"fmt"
	"strconv"
	"strings"

	"github.com/algony-tony/mymcp/go/internal/auth"
)

// Options are the raw flag values of `mymcp init`.
type Options struct {
	Yes             bool
	Bind            string
	Port            int
	ServiceUser     string
	ConfigDir       string
	LogDir          string
	RecorderDataDir string
	Audit           bool
	MetricsToken    string
	NoMetricsToken  bool
	ClientName      string
	ClientRole      string
	Recorder        bool
	RecorderProvider string
	RecorderModel    string
	RecorderAPIKey   string
	InstallRipgrep   bool
	RipgrepBinary    string
	Start            bool
	DryRun           bool
}

// envValue pulls one uncommented key out of an existing .env, else "".
func envValue(existing, key string) string {
	for _, line := range strings.Split(existing, "\n") {
		if keyOf(line) != key {
			continue
		}
		_, v, _ := strings.Cut(strings.TrimSpace(line), "=")
		return strings.TrimSpace(v)
	}
	return ""
}

// PlanFromOptions is the non-interactive path (-yes, CI, Ansible).
func PlanFromOptions(o Options, pf Preflight, sys System) (*Plan, error) {
	p := DefaultPlan()
	p.Bind, p.Port = o.Bind, o.Port
	p.ServiceUser = o.ServiceUser
	p.ConfigDir, p.LogDir, p.RecorderDataDir = o.ConfigDir, o.LogDir, o.RecorderDataDir
	p.AuditEnabled = o.Audit
	p.ClientName, p.ClientRole = o.ClientName, o.ClientRole
	p.InstallRipgrep, p.RipgrepBinary = o.InstallRipgrep, o.RipgrepBinary
	p.Start, p.DryRun = o.Start, o.DryRun
	p.HasSystemd = pf.HasSystemd

	switch {
	case o.NoMetricsToken:
		p.MetricsToken = ""
	case o.MetricsToken != "":
		p.MetricsToken = o.MetricsToken
	default:
		tok, err := auth.GenerateToken()
		if err != nil {
			return nil, err
		}
		p.MetricsToken = tok
	}

	if o.Recorder {
		if pf.Recorder == RecorderUnavailable {
			return nil, fmt.Errorf("-recorder requested but neither mymcp-recorder nor pipx is on PATH; " +
				"install the extra first: pipx inject algony-mymcp \"algony-mymcp[recorder]\"")
		}
		p.Recorder = RecorderPlan{
			Enabled:     true,
			Provider:    o.RecorderProvider,
			Model:       o.RecorderModel,
			APIKey:      o.RecorderAPIKey,
			NeedsInject: pf.Recorder == RecorderViaPipx,
		}
	}

	if path, err := sys.LookPath("mymcp"); err == nil {
		p.ExecPath = path
	} else {
		p.ExecPath = "/usr/local/bin/mymcp"
	}
	return p, nil
}

// PlanFromWizard asks the seven questions, seeding defaults from any existing
// .env (update mode), then hands off to PlanFromOptions for the rest.
func PlanFromWizard(o Options, pf Preflight, pr *Prompter, sys System) (*Plan, error) {
	if v := envValue(pf.ExistingEnv, "MYMCP_HOST"); v != "" {
		o.Bind = v
	}
	if v := envValue(pf.ExistingEnv, "MYMCP_PORT"); v != "" {
		if n, err := strconv.Atoi(v); err == nil {
			o.Port = n
		}
	}

	// 1. Bind + port.
	o.Bind = pr.Ask("Bind address", o.Bind)
	if w := ExposureWarning(o.Bind); w != "" {
		fmt.Fprintln(pr.out, w)
		if h := FirewallHint(sys, o.Port); h != "" {
			fmt.Fprintln(pr.out, h)
		}
	}
	for {
		v := pr.Ask("Port", strconv.Itoa(o.Port))
		n, err := strconv.Atoi(v)
		if err != nil || n < 1 || n > 65535 {
			fmt.Fprintln(pr.out, "  not a valid port")
			continue
		}
		if PortInUse(sys, o.Bind, n) {
			fmt.Fprintf(pr.out, "  port %d is already listening; choose another\n", n)
			continue
		}
		o.Port = n
		break
	}

	// 2. Service user.
	fmt.Fprintln(pr.out, serviceUserWarning)
	o.ServiceUser = pr.Ask("Run the service as", o.ServiceUser)

	// 3. Audit.
	o.Audit = pr.Confirm("Enable the audit log", o.Audit)

	// 4. First client token.
	o.ClientName = pr.Ask("Name for the first client token", o.ClientName)
	o.ClientRole = pr.Ask("Role for it (rw = all tools, ro = read-only, safer)", o.ClientRole)

	// 5. Recorder.
	if pf.Recorder == RecorderUnavailable {
		fmt.Fprintln(pr.out, "Overview recorder: unavailable (no pipx and no mymcp-recorder on PATH) — skipping.")
	} else if pr.Confirm("Enable the overview recorder sidecar", o.Recorder) {
		o.Recorder = true
		o.RecorderProvider = pr.Ask("LLM provider (anthropic|openai)", "anthropic")
		o.RecorderModel = pr.Ask("Model (blank = adapter default)", o.RecorderModel)
		o.RecorderAPIKey = pr.AskSecret("API key")
	}

	// 6. ripgrep.
	if _, err := sys.LookPath("rg"); err != nil {
		o.InstallRipgrep = pr.Confirm("ripgrep is missing (grep falls back to a native scan). Install it", true)
	} else {
		o.InstallRipgrep = false
	}

	return PlanFromOptions(o, pf, sys)
}

const serviceUserWarning = `
  SECURITY: running as root gives every token holder a root shell —
  bash_execute is deliberately NOT subject to protected paths. Issue 'ro'
  tokens to clients you do not fully trust. 'root' is still the default
  because operating the host is what mymcp is for.`

// ExposureWarning returns a warning for wildcard binds, else "".
func ExposureWarning(bind string) string {
	if bind != "0.0.0.0" && bind != "::" && bind != "*" {
		return ""
	}
	return "  WARNING: binding " + bind + " exposes mymcp to every reachable network.\n" +
		"  Anyone who reaches this port with a valid token controls this host.\n" +
		"  Safer: bind 127.0.0.1 and put a TLS reverse proxy in front."
}

// FirewallHint returns a concrete allow command for whichever firewall is present.
func FirewallHint(sys System, port int) string {
	if _, err := sys.LookPath("ufw"); err == nil {
		return fmt.Sprintf("  Firewall: sudo ufw allow %d/tcp", port)
	}
	if _, err := sys.LookPath("firewall-cmd"); err == nil {
		return fmt.Sprintf("  Firewall: sudo firewall-cmd --add-port=%d/tcp --permanent && sudo firewall-cmd --reload", port)
	}
	return ""
}

// PortInUse reports whether anything is already listening on port.
func PortInUse(sys System, bind string, port int) bool {
	out, err := sys.Run("ss", "-tlnH")
	if err != nil {
		return false // no ss: do not block the install on a missing tool
	}
	needle := ":" + strconv.Itoa(port)
	for _, line := range strings.Split(out, "\n") {
		for _, f := range strings.Fields(line) {
			if strings.HasSuffix(f, needle) {
				return true
			}
		}
	}
	return false
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd go && go test ./internal/setup/ -v && go vet ./... && gofmt -l .`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add go/internal/setup/wizard.go go/internal/setup/wizard_test.go
git commit -m "feat(setup): wizard producing a Plan from flags or a TTY session"
```

---

## Task 7: `mymcp init` subcommand and the closing summary

**Files:**
- Create: `go/internal/setup/summary.go`
- Modify: `go/cmd/mymcp/main.go` (add `init` to the `run` switch)
- Test: `go/internal/setup/summary_test.go`, `go/cmd/mymcp/init_cli_test.go`

**Interfaces:**
- Consumes: everything from Tasks 1-6.
- Produces: `setup.Summary(p *Plan, out ApplyOutcome, w io.Writer)`, `setup.PrimaryAddress(bind string) string`, `setup.RunInit(args []string) int`.

- [ ] **Step 1: Write the failing test**

`go/internal/setup/summary_test.go`:

```go
package setup

import (
	"bytes"
	"strings"
	"testing"
)

func TestSummaryNeverPrintsWildcardAsClientURL(t *testing.T) {
	p := DefaultPlan()
	p.Bind = "0.0.0.0"
	var buf bytes.Buffer
	Summary(p, ApplyOutcome{AdminToken: "tok_admin", ClientToken: "tok_client"}, &buf)
	s := buf.String()
	if strings.Contains(s, "http://0.0.0.0:") {
		t.Fatalf("0.0.0.0 is not usable in a client config:\n%s", s)
	}
	if !strings.Contains(s, "tok_client") {
		t.Error("client token must be shown")
	}
	if !strings.Contains(s, "tok_admin") {
		t.Error("admin token must be shown once")
	}
	if !strings.Contains(s, "claude mcp add") {
		t.Error("summary must include a pasteable client command")
	}
	if !strings.Contains(s, "mymcp doctor") {
		t.Error("summary must point at the next step")
	}
}

func TestSummaryKeepsExplicitBind(t *testing.T) {
	p := DefaultPlan()
	p.Bind = "127.0.0.1"
	var buf bytes.Buffer
	Summary(p, ApplyOutcome{AdminToken: "a", ClientToken: "c"}, &buf)
	if !strings.Contains(buf.String(), "http://127.0.0.1:8765/mcp") {
		t.Fatalf("explicit bind must be used verbatim:\n%s", buf.String())
	}
}

func TestPrimaryAddressResolvesWildcard(t *testing.T) {
	got := PrimaryAddress("0.0.0.0")
	if got == "0.0.0.0" || got == "" {
		t.Fatalf("PrimaryAddress(0.0.0.0) = %q, want a concrete address", got)
	}
}
```

`go/cmd/mymcp/init_cli_test.go`:

```go
package main

import "testing"

func TestInitRejectsNonInteractiveWithoutYes(t *testing.T) {
	// No TTY in `go test`, so init must refuse rather than hang or take
	// silent defaults.
	if code := run([]string{"init", "-config-dir", t.TempDir(), "-dry-run"}); code == 0 {
		t.Fatal("init without -yes and without a TTY must fail")
	}
}

func TestInitDryRunWithYesSucceedsAndWritesNothing(t *testing.T) {
	dir := t.TempDir()
	code := run([]string{
		"init", "-yes", "-dry-run",
		"-config-dir", dir,
		"-log-dir", dir + "/log",
		"-recorder-data-dir", dir + "/rec",
		"-start=false",
	})
	if code != 0 {
		t.Fatalf("init -yes -dry-run exit = %d, want 0", code)
	}
}

func TestUnknownInitFlagIsUsageError(t *testing.T) {
	if code := run([]string{"init", "-nope"}); code != 2 {
		t.Fatalf("exit = %d, want 2 for a usage error", code)
	}
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd go && go test ./internal/setup/ ./cmd/mymcp/ -run 'TestSummary|TestPrimaryAddress|TestInit|TestUnknownInit' -v`
Expected: FAIL — `undefined: Summary`, and `unknown command: init`.

- [ ] **Step 3: Write minimal implementation**

`go/internal/setup/summary.go`:

```go
package setup

import (
	"fmt"
	"io"
	"net"
)

// PrimaryAddress turns a wildcard bind into an address a client can actually
// dial. This is the last metre of "paste it and it works".
func PrimaryAddress(bind string) string {
	if bind != "0.0.0.0" && bind != "::" && bind != "*" {
		return bind
	}
	// No packet is sent; this just asks the kernel which source address it
	// would pick for an off-box destination.
	conn, err := net.Dial("udp", "192.0.2.1:9")
	if err == nil {
		defer conn.Close()
		if host, _, err := net.SplitHostPort(conn.LocalAddr().String()); err == nil {
			return host
		}
	}
	if host, err := net.LookupHost("localhost"); err == nil && len(host) > 0 {
		return host[0]
	}
	return "127.0.0.1"
}

func Summary(p *Plan, out ApplyOutcome, w io.Writer) {
	url := fmt.Sprintf("http://%s:%d/mcp", PrimaryAddress(p.Bind), p.Port)
	fmt.Fprintf(w, "\n✓ mymcp is configured on %s:%d\n\n", p.Bind, p.Port)
	fmt.Fprintf(w, "  URL     %s\n", url)
	fmt.Fprintf(w, "  Token   %s   (%s, name=%s)\n\n", out.ClientToken, p.ClientRole, p.ClientName)
	fmt.Fprintf(w, "  claude mcp add --transport http mymcp %s \\\n", url)
	fmt.Fprintf(w, "      --header \"Authorization: Bearer %s\"\n\n", out.ClientToken)
	fmt.Fprintf(w, "  {\"mcpServers\":{\"mymcp\":{\"type\":\"http\",\"url\":%q,"+
		"\"headers\":{\"Authorization\":\"Bearer %s\"}}}}\n\n", url, out.ClientToken)
	fmt.Fprintf(w, "  Admin token: %s   (shown once; also in %s)\n\n", out.AdminToken, p.EnvPath())
	if !p.HasSystemd {
		fmt.Fprintf(w, "  No systemd here — run it yourself:\n    mymcp serve -env-file %s\n\n", p.EnvPath())
	}
	fmt.Fprintf(w, "  Next: mymcp doctor  |  journalctl -u mymcp -f\n")
	fmt.Fprintf(w, "  To remove: systemctl disable --now mymcp && rm %s && systemctl daemon-reload\n", p.UnitPath())
}
```

Add to `go/cmd/mymcp/main.go` — a `case "init":` in `run`'s switch plus this function:

```go
func runInit(args []string) int {
	o := setup.Options{}
	fs := flag.NewFlagSet("init", flag.ContinueOnError)
	fs.BoolVar(&o.Yes, "yes", false, "non-interactive; accept every default")
	fs.StringVar(&o.Bind, "bind", "0.0.0.0", "bind address")
	fs.IntVar(&o.Port, "port", 8765, "bind port")
	fs.StringVar(&o.ServiceUser, "service-user", "root", "systemd User=")
	fs.StringVar(&o.ConfigDir, "config-dir", "/etc/mymcp", "config directory")
	fs.StringVar(&o.LogDir, "log-dir", "/var/log/mymcp", "audit log directory")
	fs.StringVar(&o.RecorderDataDir, "recorder-data-dir", "/var/lib/mymcp/recorder", "recorder data directory")
	fs.BoolVar(&o.Audit, "audit", true, "enable the audit log")
	fs.StringVar(&o.MetricsToken, "metrics-token", "", "explicit /metrics token")
	fs.BoolVar(&o.NoMetricsToken, "no-metrics-token", false, "leave /metrics unauthenticated")
	fs.StringVar(&o.ClientName, "client-name", "default", "name of the first client token")
	fs.StringVar(&o.ClientRole, "client-role", "rw", "role of the first client token: ro or rw")
	fs.BoolVar(&o.Recorder, "recorder", false, "enable the overview recorder sidecar")
	fs.StringVar(&o.RecorderProvider, "recorder-provider", "anthropic", "anthropic or openai")
	fs.StringVar(&o.RecorderModel, "recorder-model", "", "recorder LLM model")
	fs.StringVar(&o.RecorderAPIKey, "recorder-api-key", os.Getenv("MYMCP_RECORDER_LLM_API_KEY"), "recorder LLM API key")
	fs.BoolVar(&o.InstallRipgrep, "install-ripgrep", true, "install ripgrep when missing")
	fs.StringVar(&o.RipgrepBinary, "ripgrep-binary", "", "use this ripgrep binary instead of a package manager")
	fs.BoolVar(&o.Start, "start", true, "enable and start the service")
	fs.BoolVar(&o.DryRun, "dry-run", false, "print what would change and write nothing")
	if err := fs.Parse(args); err != nil {
		return 2
	}

	sys := setup.RealSystem()
	pf := setup.RunPreflight(o.ConfigDir, sys)
	if !pf.IsRoot && !o.DryRun {
		fmt.Fprintln(os.Stderr, "mymcp init must run as root: sudo mymcp init")
		return 1
	}
	if !pf.HasSystemd {
		fmt.Fprintln(os.Stderr, "[mymcp] systemd not detected — configuring files only (degraded mode)")
	}

	var plan *setup.Plan
	var err error
	if o.Yes {
		plan, err = setup.PlanFromOptions(o, pf, sys)
	} else {
		pr, terr := setup.OpenTTYPrompter(sys)
		if terr != nil {
			fmt.Fprintf(os.Stderr, "mymcp init: %v\n  re-run with -yes for a non-interactive install\n", terr)
			return 1
		}
		defer pr.Close()
		plan, err = setup.PlanFromWizard(o, pf, pr, sys)
	}
	if err != nil {
		fmt.Fprintln(os.Stderr, "mymcp init:", err)
		return 1
	}

	outcome, err := setup.Apply(plan, sys)
	for _, r := range outcome.Results {
		fmt.Fprintf(os.Stderr, "  %-9s %s %s\n", r.Status, r.Step, r.Detail)
	}
	if err != nil {
		fmt.Fprintln(os.Stderr, "mymcp init failed:", err)
		fmt.Fprintln(os.Stderr, "every step is idempotent — fix the cause and re-run `mymcp init` to resume")
		return 1
	}
	setup.Summary(plan, outcome, os.Stdout)
	return 0
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd go && go build ./... && go test ./... -v && go vet ./... && gofmt -l .`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add go/internal/setup/summary.go go/internal/setup/summary_test.go go/cmd/mymcp/
git commit -m "feat(cli): mymcp init wizard with pasteable client config summary"
```

---

## Task 8: `mymcp doctor`

**Files:**
- Create: `go/internal/setup/doctor.go`
- Modify: `go/cmd/mymcp/main.go` (add `doctor` to the switch; call it at the end of `runInit`)
- Test: `go/internal/setup/doctor_test.go`

**Interfaces:**
- Consumes: `Plan`, `System` (Task 1); `keyOf`, `envValue` (Tasks 2, 6).
- Produces: `setup.Severity` (`SevOK`/`SevWarn`/`SevFail`), `setup.Check{Group, Name string; Severity Severity; Detail, Remedy string}`, `setup.Doctor(configDir string, sys System) []Check`, `setup.RenderChecks(checks []Check, w io.Writer)`, `setup.RenderChecksJSON(checks []Check, w io.Writer) error`, `setup.DoctorExitCode(checks []Check, strict bool) int`.

Checks are grouped `INSTALL` / `CONFIG` / `RUNTIME` / `FUNCTIONAL` / `RECORDER` as in the spec. The `FUNCTIONAL` check reads an enabled token from `tokens.json`, **preferring `rw`**, and issues a real `POST /mcp` `tools/list`: with `rw` it asserts 9 tools; with only `ro` it asserts a non-empty list whose names are all read tools, because `tools/list` is role-filtered.

- [ ] **Step 1: Write the failing test**

`go/internal/setup/doctor_test.go`:

```go
package setup

import (
	"bytes"
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func writeEnv(t *testing.T, dir, content string, mode os.FileMode) string {
	t.Helper()
	p := filepath.Join(dir, ".env")
	if err := os.WriteFile(p, []byte(content), mode); err != nil {
		t.Fatal(err)
	}
	return p
}

func findCheck(checks []Check, name string) *Check {
	for i := range checks {
		if checks[i].Name == name {
			return &checks[i]
		}
	}
	return nil
}

func TestDoctorFlagsLooseEnvPermissions(t *testing.T) {
	dir := t.TempDir()
	writeEnv(t, dir, "MYMCP_ADMIN_TOKEN=tok_a\n", 0o644)
	c := findCheck(Doctor(dir, newFakeSystem()), "env permissions")
	if c == nil {
		t.Fatal("missing 'env permissions' check")
	}
	if c.Severity != SevFail {
		t.Fatalf("0644 .env holds the admin token; severity = %v, want SevFail", c.Severity)
	}
	if c.Remedy == "" {
		t.Error("a failing check must carry a pasteable remedy")
	}
}

func TestDoctorWarnsWhenAuditDisabled(t *testing.T) {
	dir := t.TempDir()
	writeEnv(t, dir, "MYMCP_ADMIN_TOKEN=tok_a\nMYMCP_AUDIT_ENABLED=false\n", 0o600)
	c := findCheck(Doctor(dir, newFakeSystem()), "audit enabled")
	if c == nil || c.Severity != SevWarn {
		t.Fatalf("audit-disabled must warn, got %+v", c)
	}
}

func TestDoctorFailsOnMissingAdminToken(t *testing.T) {
	dir := t.TempDir()
	writeEnv(t, dir, "MYMCP_PORT=8765\n", 0o600)
	c := findCheck(Doctor(dir, newFakeSystem()), "admin token")
	if c == nil || c.Severity != SevFail {
		t.Fatalf("missing admin token must fail, got %+v", c)
	}
}

func TestDoctorDetectsDuplicateBinariesOnPath(t *testing.T) {
	dir := t.TempDir()
	writeEnv(t, dir, "MYMCP_ADMIN_TOKEN=tok_a\n", 0o600)
	sys := newFakeSystem()
	sys.Paths["mymcp"] = "/usr/local/bin/mymcp"
	sys.Outputs["which -a mymcp"] = "/usr/local/bin/mymcp\n/root/.local/bin/mymcp\n"
	c := findCheck(Doctor(dir, sys), "duplicate binaries")
	if c == nil || c.Severity != SevFail {
		t.Fatalf("two mymcp on PATH must fail (upgrades hit the wrong copy), got %+v", c)
	}
	if !strings.Contains(c.Detail, "/root/.local/bin/mymcp") {
		t.Errorf("detail must name both copies: %q", c.Detail)
	}
}

func TestDoctorExitCodeAndStrict(t *testing.T) {
	ok := []Check{{Name: "a", Severity: SevOK}}
	warn := []Check{{Name: "a", Severity: SevWarn}}
	fail := []Check{{Name: "a", Severity: SevFail}}
	if DoctorExitCode(ok, false) != 0 || DoctorExitCode(warn, false) != 0 {
		t.Error("warnings alone must exit 0 without -strict")
	}
	if DoctorExitCode(warn, true) != 1 {
		t.Error("-strict must promote warnings to failure")
	}
	if DoctorExitCode(fail, false) != 1 {
		t.Error("a failure must exit 1")
	}
}

func TestRenderChecksJSONIsMachineReadable(t *testing.T) {
	var buf bytes.Buffer
	if err := RenderChecksJSON([]Check{{Group: "CONFIG", Name: "a", Severity: SevWarn, Detail: "d"}}, &buf); err != nil {
		t.Fatal(err)
	}
	var got []map[string]any
	if err := json.Unmarshal(buf.Bytes(), &got); err != nil {
		t.Fatalf("not valid JSON: %v\n%s", err, buf.String())
	}
	if got[0]["severity"] != "warn" {
		t.Fatalf("severity must serialise as a string, got %v", got[0]["severity"])
	}
}

func TestRenderChecksGroupsAndCounts(t *testing.T) {
	var buf bytes.Buffer
	RenderChecks([]Check{
		{Group: "INSTALL", Name: "binary", Severity: SevOK, Detail: "/usr/local/bin/mymcp"},
		{Group: "CONFIG", Name: "audit enabled", Severity: SevWarn, Remedy: "set MYMCP_AUDIT_ENABLED=true"},
	}, &buf)
	s := buf.String()
	if !strings.Contains(s, "INSTALL") || !strings.Contains(s, "CONFIG") {
		t.Errorf("groups must be headed:\n%s", s)
	}
	if !strings.Contains(s, "0 problems, 1 warning") {
		t.Errorf("missing the tally line:\n%s", s)
	}
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd go && go test ./internal/setup/ -run TestDoctor -v`
Expected: FAIL — `undefined: Doctor`.

- [ ] **Step 3: Write minimal implementation**

`go/internal/setup/doctor.go` — implement in this order (each bullet is one small function called by `Doctor`):

```go
package setup

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"time"

	"github.com/algony-tony/mymcp/go/internal/auth"
)

type Severity int

const (
	SevOK Severity = iota
	SevWarn
	SevFail
)

func (s Severity) String() string {
	switch s {
	case SevOK:
		return "ok"
	case SevWarn:
		return "warn"
	default:
		return "fail"
	}
}

func (s Severity) MarshalJSON() ([]byte, error) { return json.Marshal(s.String()) }

func (s Severity) glyph() string {
	switch s {
	case SevOK:
		return "✓"
	case SevWarn:
		return "⚠"
	default:
		return "✗"
	}
}

type Check struct {
	Group    string   `json:"group"`
	Name     string   `json:"name"`
	Severity Severity `json:"severity"`
	Detail   string   `json:"detail,omitempty"`
	Remedy   string   `json:"remedy,omitempty"`
}

// Doctor runs every check. It never mutates the system: the only request it
// makes is a read-only tools/list.
func Doctor(configDir string, sys System) []Check {
	var checks []Check
	add := func(c Check) { checks = append(checks, c) }

	// --- INSTALL ---
	if path, err := sys.LookPath("mymcp"); err == nil {
		add(Check{Group: "INSTALL", Name: "binary", Severity: SevOK, Detail: path})
	} else {
		add(Check{Group: "INSTALL", Name: "binary", Severity: SevFail,
			Detail: "mymcp is not on PATH", Remedy: "pipx install algony-mymcp"})
	}
	if out, err := sys.Run("which", "-a", "mymcp"); err == nil {
		paths := nonEmptyLines(out)
		if len(paths) > 1 {
			add(Check{Group: "INSTALL", Name: "duplicate binaries", Severity: SevFail,
				Detail: strings.Join(paths, ", "),
				Remedy: "keep one copy; the unit's ExecStart decides which runs: pipx uninstall algony-mymcp"})
		} else {
			add(Check{Group: "INSTALL", Name: "duplicate binaries", Severity: SevOK, Detail: "1 copy"})
		}
	}
	if _, err := sys.LookPath("rg"); err != nil {
		add(Check{Group: "INSTALL", Name: "ripgrep", Severity: SevWarn,
			Detail: "not installed; grep falls back to a native scan",
			Remedy: "apt install -y ripgrep"})
	} else {
		add(Check{Group: "INSTALL", Name: "ripgrep", Severity: SevOK})
	}

	// --- CONFIG ---
	envPath := filepath.Join(configDir, ".env")
	raw, err := os.ReadFile(envPath)
	if err != nil {
		add(Check{Group: "CONFIG", Name: "env file", Severity: SevFail,
			Detail: envPath + " is missing", Remedy: "sudo mymcp init"})
		return checks // nothing downstream is meaningful without config
	}
	add(Check{Group: "CONFIG", Name: "env file", Severity: SevOK, Detail: envPath})
	if st, err := os.Stat(envPath); err == nil && st.Mode().Perm() != 0o600 {
		add(Check{Group: "CONFIG", Name: "env permissions", Severity: SevFail,
			Detail: fmt.Sprintf("%s is mode %o and holds the admin token", envPath, st.Mode().Perm()),
			Remedy: "chmod 600 " + envPath})
	} else {
		add(Check{Group: "CONFIG", Name: "env permissions", Severity: SevOK, Detail: "0600"})
	}
	admin := ExistingAdminToken(string(raw))
	if admin == "" {
		add(Check{Group: "CONFIG", Name: "admin token", Severity: SevFail,
			Detail: "MYMCP_ADMIN_TOKEN is empty; the server will refuse to start",
			Remedy: "sudo mymcp init"})
	} else {
		add(Check{Group: "CONFIG", Name: "admin token", Severity: SevOK})
	}
	if envValue(string(raw), "MYMCP_AUDIT_ENABLED") != "true" {
		add(Check{Group: "CONFIG", Name: "audit enabled", Severity: SevWarn,
			Detail: "audit logging is off; silent audit loss is this project's stated SOC red line",
			Remedy: "set MYMCP_AUDIT_ENABLED=true in " + envPath + " and restart"})
	} else {
		add(Check{Group: "CONFIG", Name: "audit enabled", Severity: SevOK})
	}
	tokenPath := envValue(string(raw), "MYMCP_TOKEN_FILE")
	if tokenPath == "" {
		tokenPath = filepath.Join(configDir, "tokens.json")
	}
	checks = append(checks, tokenStoreChecks(tokenPath)...)

	// --- RUNTIME ---
	checks = append(checks, runtimeChecks(sys)...)

	// --- FUNCTIONAL ---
	port := envValue(string(raw), "MYMCP_PORT")
	if port == "" {
		port = "8765"
	}
	checks = append(checks, functionalChecks(tokenPath, port)...)

	// --- RECORDER ---
	if envValue(string(raw), "MYMCP_RECORDER_ENABLED") == "true" {
		checks = append(checks, recorderChecks(sys, string(raw), port)...)
	}
	return checks
}

func nonEmptyLines(s string) []string {
	var out []string
	for _, l := range strings.Split(s, "\n") {
		if l = strings.TrimSpace(l); l != "" {
			out = append(out, l)
		}
	}
	return out
}
```

Then implement these four helpers in the same file:

```go
func tokenStoreChecks(path string) []Check {
	st, err := os.Stat(path)
	if err != nil {
		return []Check{{Group: "CONFIG", Name: "token store", Severity: SevFail,
			Detail: path + " is missing", Remedy: "sudo mymcp init"}}
	}
	out := []Check{}
	if st.Mode().Perm() != 0o600 {
		out = append(out, Check{Group: "CONFIG", Name: "token store permissions", Severity: SevFail,
			Detail: fmt.Sprintf("%s is mode %o", path, st.Mode().Perm()),
			Remedy: "chmod 600 " + path})
	} else {
		out = append(out, Check{Group: "CONFIG", Name: "token store permissions", Severity: SevOK})
	}
	store, err := auth.NewTokenStore(path, "unused-by-list")
	if err != nil {
		return append(out, Check{Group: "CONFIG", Name: "token store", Severity: SevFail,
			Detail: err.Error(), Remedy: "sudo mymcp init"})
	}
	var enabled int
	for _, info := range store.ListTokens() {
		if info.Enabled {
			enabled++
		}
	}
	if enabled == 0 {
		return append(out, Check{Group: "CONFIG", Name: "client tokens", Severity: SevFail,
			Detail: "no enabled tokens; no client can connect",
			Remedy: "mymcp token add --role rw default"})
	}
	return append(out, Check{Group: "CONFIG", Name: "client tokens", Severity: SevOK,
		Detail: fmt.Sprintf("%d enabled", enabled)})
}

func runtimeChecks(sys System) []Check {
	if _, err := os.Stat("/run/systemd/system"); err != nil {
		return []Check{{Group: "RUNTIME", Name: "systemd", Severity: SevWarn,
			Detail: "not present; this host runs mymcp in the foreground",
			Remedy: "mymcp serve -env-file /etc/mymcp/.env"}}
	}
	out := []Check{{Group: "RUNTIME", Name: "systemd", Severity: SevOK}}
	if got, err := sys.Run("systemctl", "is-enabled", "mymcp"); err != nil ||
		strings.TrimSpace(got) != "enabled" {
		out = append(out, Check{Group: "RUNTIME", Name: "unit enabled", Severity: SevWarn,
			Detail: "mymcp.service will not start at boot",
			Remedy: "systemctl enable mymcp"})
	} else {
		out = append(out, Check{Group: "RUNTIME", Name: "unit enabled", Severity: SevOK})
	}
	if got, err := sys.Run("systemctl", "is-active", "mymcp"); err != nil ||
		strings.TrimSpace(got) != "active" {
		out = append(out, Check{Group: "RUNTIME", Name: "unit active", Severity: SevFail,
			Detail: "mymcp.service is not running",
			Remedy: "systemctl start mymcp && journalctl -u mymcp -n 50"})
	} else {
		out = append(out, Check{Group: "RUNTIME", Name: "unit active", Severity: SevOK})
	}
	out = append(out, execStartCheck(sys))
	return out
}

// execStartCheck pins down WHICH copy of the binary systemd actually runs.
// With two install channels (pipx and the raw binary) an upgrade can land on
// the copy the unit does not use, which presents as "I upgraded but nothing
// changed".
func execStartCheck(sys System) Check {
	raw, err := os.ReadFile("/etc/systemd/system/mymcp.service")
	if err != nil {
		return Check{Group: "RUNTIME", Name: "unit ExecStart", Severity: SevWarn,
			Detail: "no unit file at /etc/systemd/system/mymcp.service",
			Remedy: "sudo mymcp init"}
	}
	var unitBin string
	for _, line := range strings.Split(string(raw), "\n") {
		if after, ok := strings.CutPrefix(strings.TrimSpace(line), "ExecStart="); ok {
			unitBin = strings.Fields(after)[0]
			break
		}
	}
	pathBin, _ := sys.LookPath("mymcp")
	switch {
	case unitBin == "":
		return Check{Group: "RUNTIME", Name: "unit ExecStart", Severity: SevWarn,
			Detail: "no ExecStart= line", Remedy: "sudo mymcp init"}
	case pathBin != "" && unitBin != pathBin:
		return Check{Group: "RUNTIME", Name: "unit ExecStart", Severity: SevFail,
			Detail: fmt.Sprintf("unit runs %s but PATH resolves %s", unitBin, pathBin),
			Remedy: "sudo mymcp init   # repoints ExecStart at the current binary"}
	default:
		return Check{Group: "RUNTIME", Name: "unit ExecStart", Severity: SevOK, Detail: unitBin}
	}
}

func functionalChecks(tokenPath, port string) []Check {
	store, err := auth.NewTokenStore(tokenPath, "unused-by-list")
	if err != nil {
		return []Check{{Group: "FUNCTIONAL", Name: "tools/list", Severity: SevFail,
			Detail: "cannot read the token store: " + err.Error()}}
	}
	// Prefer rw: only an rw token sees the full tool set, because tools/list
	// is role-filtered in mcpserver.go.
	var token, role string
	for tok, info := range store.ListTokens() {
		if !info.Enabled {
			continue
		}
		if info.Role == "rw" {
			token, role = tok, "rw"
			break
		}
		if token == "" {
			token, role = tok, info.Role
		}
	}
	if token == "" {
		return []Check{{Group: "FUNCTIONAL", Name: "tools/list", Severity: SevFail,
			Detail: "no enabled token to test with",
			Remedy: "mymcp token add --role rw default"}}
	}

	start := time.Now()
	code, body, err := httpJSON("http://127.0.0.1:"+port+"/mcp", token,
		`{"jsonrpc":"2.0","id":1,"method":"tools/list"}`)
	elapsed := time.Since(start).Round(time.Millisecond)
	if err != nil {
		return []Check{{Group: "FUNCTIONAL", Name: "tools/list", Severity: SevFail,
			Detail: err.Error(), Remedy: "systemctl start mymcp && journalctl -u mymcp -n 50"}}
	}
	if code != http.StatusOK {
		return []Check{{Group: "FUNCTIONAL", Name: "tools/list", Severity: SevFail,
			Detail: fmt.Sprintf("HTTP %d: %s", code, truncate(body, 120)),
			Remedy: "journalctl -u mymcp -n 50"}}
	}
	n := strings.Count(body, `"inputSchema"`)
	if role == "rw" && n != 9 {
		return []Check{{Group: "FUNCTIONAL", Name: "tools/list", Severity: SevFail,
			Detail: fmt.Sprintf("rw token saw %d tools, want 9", n),
			Remedy: "journalctl -u mymcp -n 50"}}
	}
	if n == 0 {
		return []Check{{Group: "FUNCTIONAL", Name: "tools/list", Severity: SevFail,
			Detail: "empty tool list", Remedy: "journalctl -u mymcp -n 50"}}
	}
	return []Check{{Group: "FUNCTIONAL", Name: "tools/list", Severity: SevOK,
		Detail: fmt.Sprintf("%d tools (%s token, %s)", n, role, elapsed)}}
}

func truncate(s string, n int) string {
	if len(s) <= n {
		return s
	}
	return s[:n] + "…"
}

// metricValue pulls a single unlabelled gauge/counter out of a /metrics scrape.
func metricValue(scrape, name string) (float64, bool) {
	for _, line := range strings.Split(scrape, "\n") {
		if !strings.HasPrefix(line, name+" ") {
			continue
		}
		var v float64
		if _, err := fmt.Sscanf(strings.TrimPrefix(line, name+" "), "%g", &v); err == nil {
			return v, true
		}
	}
	return 0, false
}

func recorderChecks(sys System, env, port string) []Check {
	out := []Check{}
	if _, err := sys.LookPath("mymcp-recorder"); err != nil {
		return append(out, Check{Group: "RECORDER", Name: "sidecar installed", Severity: SevFail,
			Detail: "MYMCP_RECORDER_ENABLED=true but mymcp-recorder is not on PATH",
			Remedy: `pipx inject algony-mymcp "algony-mymcp[recorder]" && sudo mymcp init`})
	}
	out = append(out, Check{Group: "RECORDER", Name: "sidecar installed", Severity: SevOK})
	if got, err := sys.Run("systemctl", "is-active", "mymcp-recorder"); err != nil ||
		strings.TrimSpace(got) != "active" {
		out = append(out, Check{Group: "RECORDER", Name: "sidecar active", Severity: SevFail,
			Detail: "mymcp-recorder.service is not running",
			Remedy: "systemctl start mymcp-recorder && journalctl -u mymcp-recorder -n 50"})
		return out
	}
	out = append(out, Check{Group: "RECORDER", Name: "sidecar active", Severity: SevOK})

	code, scrape, err := httpJSON("http://127.0.0.1:"+port+"/metrics", envValue(env, "MYMCP_METRICS_TOKEN"), "")
	if err != nil || code != http.StatusOK {
		out = append(out, Check{Group: "RECORDER", Name: "backlog", Severity: SevWarn,
			Detail: "could not scrape /metrics to judge the backlog"})
		return out
	}
	if open, ok := metricValue(scrape, "mymcp_recorder_circuit_open"); ok && open == 1 {
		out = append(out, Check{Group: "RECORDER", Name: "circuit breaker", Severity: SevFail,
			Detail: "the merge circuit breaker is open",
			Remedy: "journalctl -u mymcp-recorder -n 50"})
	}
	interval := 300.0
	if v := envValue(env, "MYMCP_RECORDER_MERGE_INTERVAL_SEC"); v != "" {
		fmt.Sscanf(v, "%g", &interval)
	}
	pending, okP := metricValue(scrape, "mymcp_recorder_pending_events")
	lastAttempt, okA := metricValue(scrape, "mymcp_recorder_merge_last_attempt_timestamp")
	age := float64(time.Now().Unix()) - lastAttempt
	switch {
	case !okP || !okA:
		out = append(out, Check{Group: "RECORDER", Name: "backlog", Severity: SevWarn,
			Detail: "recorder metrics not exported yet"})
	case pending > 0 && age > 2*interval:
		// The project's own stall predicate (CLAUDE.md). This is the failure
		// that went unnoticed for four weeks in production.
		out = append(out, Check{Group: "RECORDER", Name: "backlog", Severity: SevFail,
			Detail: fmt.Sprintf("%.0f events pending, last merge attempt %.0fs ago", pending, age),
			Remedy: "journalctl -u mymcp-recorder -n 50"})
	default:
		out = append(out, Check{Group: "RECORDER", Name: "backlog", Severity: SevOK,
			Detail: fmt.Sprintf("%.0f pending", pending)})
	}
	return out
}
```

Note `httpJSON` is used for `/metrics` with an empty body; make it issue a `GET`
when `body == ""` and a `POST` otherwise.

And the renderers:

```go
func DoctorExitCode(checks []Check, strict bool) int {
	for _, c := range checks {
		if c.Severity == SevFail || (strict && c.Severity == SevWarn) {
			return 1
		}
	}
	return 0
}

func RenderChecksJSON(checks []Check, w io.Writer) error {
	enc := json.NewEncoder(w)
	enc.SetIndent("", "  ")
	return enc.Encode(checks)
}

func RenderChecks(checks []Check, w io.Writer) {
	var problems, warnings int
	group := ""
	for _, c := range checks {
		if c.Group != group {
			group = c.Group
			fmt.Fprintf(w, "\n%s\n", group)
		}
		fmt.Fprintf(w, "  %s %-20s %s\n", c.Severity.glyph(), c.Name, c.Detail)
		if c.Remedy != "" && c.Severity != SevOK {
			fmt.Fprintf(w, "    → %s\n", c.Remedy)
		}
		switch c.Severity {
		case SevFail:
			problems++
		case SevWarn:
			warnings++
		}
	}
	fmt.Fprintf(w, "\n%d %s, %d %s.\n",
		problems, plural(problems, "problem"), warnings, plural(warnings, "warning"))
}

func plural(n int, word string) string {
	if n == 1 {
		return word
	}
	return word + "s"
}

// httpJSON is the shared helper for the functional and recorder checks.
func httpJSON(url, token, body string) (int, string, error) {
	method := http.MethodPost
	if body == "" {
		method = http.MethodGet
	}
	req, err := http.NewRequest(method, url, bytes.NewBufferString(body))
	if err != nil {
		return 0, "", err
	}
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Accept", "application/json, text/event-stream")
	if token != "" {
		req.Header.Set("Authorization", "Bearer "+token)
	}
	resp, err := (&http.Client{Timeout: 5 * time.Second}).Do(req)
	if err != nil {
		return 0, "", err
	}
	defer resp.Body.Close()
	raw, _ := io.ReadAll(resp.Body)
	return resp.StatusCode, string(raw), nil
}
```

Add to `go/cmd/mymcp/main.go` a `case "doctor":` calling:

```go
func runDoctor(args []string) int {
	fs := flag.NewFlagSet("doctor", flag.ContinueOnError)
	configDir := fs.String("config-dir", "/etc/mymcp", "config directory")
	strict := fs.Bool("strict", false, "treat warnings as failures")
	asJSON := fs.Bool("json", false, "emit machine-readable JSON")
	if err := fs.Parse(args); err != nil {
		return 2
	}
	checks := setup.Doctor(*configDir, setup.RealSystem())
	if *asJSON {
		if err := setup.RenderChecksJSON(checks, os.Stdout); err != nil {
			fmt.Fprintln(os.Stderr, "doctor:", err)
			return 1
		}
	} else {
		setup.RenderChecks(checks, os.Stdout)
	}
	return setup.DoctorExitCode(checks, *strict)
}
```

And at the end of `runInit`, after `setup.Summary(...)`, when `plan.Start && !plan.DryRun`:

```go
	fmt.Fprintln(os.Stdout, "\nRunning mymcp doctor…")
	setup.RenderChecks(setup.Doctor(plan.ConfigDir, sys), os.Stdout)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd go && go test ./... -v && go vet ./... && gofmt -l .`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add go/internal/setup/doctor.go go/internal/setup/doctor_test.go go/cmd/mymcp/
git commit -m "feat(cli): mymcp doctor proving the install with a real tools/list"
```

---

## Task 9: recorder integration (Python flags + apply step)

**Files:**
- Modify: `src/mymcp/recorder/__main__.py:45-120` (`render_unit`, `main`)
- Modify: `go/internal/setup/apply.go` (recorder step before the start step)
- Test: `tests/recorder/test_sidecar_packaging.py`, `go/internal/setup/apply_test.go`

**Interfaces:**
- Consumes: `Plan.Recorder` (Task 1), `Apply` (Task 4).
- Produces: `render_unit(service_user: str = "mymcp", env_file: str | None = None) -> str` in Python; the recorder branch of `Apply` which runs, in order, `pipx inject algony-mymcp algony-mymcp[recorder]` (only when `NeedsInject`), then `mymcp-recorder --install-unit --service-user <user> --env-file <path> --output <RecorderUnitPath>`, then `systemctl enable --now mymcp-recorder`.

The recorder unit template stays the single source of truth in Python; Go never renders it.

- [ ] **Step 1: Write the failing tests**

Append to `tests/recorder/test_sidecar_packaging.py`:

```python
def test_render_unit_accepts_service_user_and_env_file():
    """mymcp init must be able to match the main service's User= and .env."""
    from mymcp.recorder.__main__ import render_unit

    unit = render_unit(service_user="root", env_file="/opt/mymcp/.env")
    assert "User=root" in unit
    assert "EnvironmentFile=/opt/mymcp/.env" in unit


def test_render_unit_defaults_are_unchanged():
    from mymcp.recorder.__main__ import render_unit

    unit = render_unit()
    assert "User=mymcp" in unit
    assert "NoNewPrivileges=true" in unit


def test_install_unit_cli_passes_through_service_user(tmp_path, capsys):
    from mymcp.recorder.__main__ import main

    dest = tmp_path / "mymcp-recorder.service"
    rc = main(["--install-unit", "--service-user", "root", "--output", str(dest)])
    assert rc == 0
    assert "User=root" in dest.read_text()
```

Append to `go/internal/setup/apply_test.go`:

```go
func TestApplyRecorderInjectsThenRendersUnit(t *testing.T) {
	p, sys := tempPlan(t)
	p.Recorder = RecorderPlan{Enabled: true, Provider: "anthropic", APIKey: "sk-x", NeedsInject: true}
	if _, err := Apply(p, sys); err != nil {
		t.Fatal(err)
	}
	if !sys.ran(`pipx inject algony-mymcp algony-mymcp[recorder]`) {
		t.Errorf("missing pipx inject; calls=%v", sys.Calls)
	}
	want := "mymcp-recorder --install-unit --service-user root --env-file " +
		p.EnvPath() + " --output " + p.RecorderUnitPath()
	if !sys.ran(want) {
		t.Errorf("recorder unit must be rendered by the Python owner of the template.\nwant: %s\ngot:  %v", want, sys.Calls)
	}
}

func TestApplyRecorderSkipsInjectWhenAlreadyInstalled(t *testing.T) {
	p, sys := tempPlan(t)
	p.Recorder = RecorderPlan{Enabled: true, Provider: "anthropic", NeedsInject: false}
	if _, err := Apply(p, sys); err != nil {
		t.Fatal(err)
	}
	for _, c := range sys.Calls {
		if strings.HasPrefix(c, "pipx inject") {
			t.Fatalf("must not re-inject an installed recorder: %v", sys.Calls)
		}
	}
}

func TestApplySkipsRecorderEntirelyWhenDisabled(t *testing.T) {
	p, sys := tempPlan(t)
	if _, err := Apply(p, sys); err != nil {
		t.Fatal(err)
	}
	for _, c := range sys.Calls {
		if strings.Contains(c, "recorder") {
			t.Fatalf("recorder disabled but ran %q", c)
		}
	}
}
```

- [ ] **Step 2: Run tests to verify they fail**

Run:
```bash
cd /home/zhu/repos/mymcp && .venv/bin/python -m pytest tests/recorder/test_sidecar_packaging.py -v
cd go && go test ./internal/setup/ -run TestApplyRecorder -v
```
Expected: FAIL — `render_unit() got an unexpected keyword argument 'service_user'`; Go: the expected calls are absent.

- [ ] **Step 3: Write minimal implementation**

In `src/mymcp/recorder/__main__.py`, change `render_unit` to take overrides while keeping today's behaviour as the default:

```python
def render_unit(service_user: str = "mymcp", env_file: str | None = None) -> str:
    """Render the packaged systemd unit template with this install's values.

    `mymcp init` passes service_user/env_file so the sidecar matches the main
    service; called with no arguments the behaviour is unchanged from v3.
    """
    template = (
        resources.files("mymcp.recorder.templates")
        .joinpath("mymcp-recorder.service.in")
        .read_text(encoding="utf-8")
    )
    exec_start = shutil.which("mymcp-recorder") or "/usr/local/bin/mymcp-recorder"
    if env_file is None:
        discovered = _discover_env_file()
        env_file = discovered if discovered and Path(discovered).is_absolute() else "/etc/mymcp/.env"
    return template.format(
        service_user=service_user,
        working_directory="/etc/mymcp",
        env_file=env_file,
        exec_start=exec_start,
    )
```

Add the two flags in `main`, before `args = parser.parse_args(argv)`:

```python
    parser.add_argument(
        "--service-user",
        default="mymcp",
        metavar="NAME",
        help="with --install-unit, the systemd User= (mymcp init passes root)",
    )
    parser.add_argument(
        "--env-file",
        metavar="PATH",
        help="with --install-unit, the EnvironmentFile= path",
    )
```

and change the call site to `unit = render_unit(service_user=args.service_user, env_file=args.env_file)`.

In `go/internal/setup/apply.go`, insert this block after the unit write / `daemon-reload` and before the start step:

```go
	// 6b. Recorder sidecar. The unit template is owned by the Python package;
	// we shell out to it rather than keeping a second copy of the template.
	if p.Recorder.Enabled && !p.DryRun {
		if p.Recorder.NeedsInject {
			if _, err := sys.Run("pipx", "inject", "algony-mymcp", "algony-mymcp[recorder]"); err != nil {
				return out, fmt.Errorf("pipx inject recorder extra: %w", err)
			}
			add("recorder deps", StatusCreated, "pipx inject")
		}
		if _, err := sys.Run("mymcp-recorder", "--install-unit",
			"--service-user", p.ServiceUser,
			"--env-file", p.EnvPath(),
			"--output", p.RecorderUnitPath()); err != nil {
			return out, fmt.Errorf("render recorder unit: %w", err)
		}
		add("recorder unit", StatusCreated, p.RecorderUnitPath())
		if _, err := sys.Run("systemctl", "daemon-reload"); err != nil {
			return out, err
		}
		if p.Start {
			if _, err := sys.Run("systemctl", "enable", "--now", "mymcp-recorder"); err != nil {
				return out, fmt.Errorf("start mymcp-recorder: %w", err)
			}
			add("recorder service", StatusUpdated, "enabled and running")
		}
	}
```

- [ ] **Step 4: Run tests to verify they pass**

Run:
```bash
cd /home/zhu/repos/mymcp && .venv/bin/python -m pytest tests/ -v --benchmark-disable && .venv/bin/python -m mypy src/mymcp && .venv/bin/python -m ruff check . && .venv/bin/python -m ruff format --check .
cd go && go test ./... && go vet ./... && gofmt -l .
```
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/mymcp/recorder/__main__.py tests/recorder/test_sidecar_packaging.py go/internal/setup/apply.go go/internal/setup/apply_test.go
git commit -m "feat(recorder): --install-unit takes service-user/env-file; init wires the sidecar"
```

---

## Task 10: status-aware CLI surface and stale copy

**Files:**
- Modify: `go/cmd/mymcp/main.go` (no-arg output, `-h`/`--help`, `config example`)
- Modify: `go/internal/httpserver/httpserver.go:236-254` (temp-token message) and `:266` (admin-token error)
- Modify: `scripts/install-offline.sh:44-47`
- Test: `go/cmd/mymcp/main_test.go`, `go/cmd/mymcp/cli_more_test.go`

**Interfaces:**
- Consumes: `setup.RunPreflight` (Task 5), `setup.RenderEnv` (Task 2).
- Produces: `statusHint(configDir string, sys setup.System, w io.Writer)` in `main`; `mymcp config example` printing the embedded template.

- [ ] **Step 1: Write the failing test**

Append to `go/cmd/mymcp/main_test.go`:

```go
func TestBareInvocationTellsUserWhatToDoNext(t *testing.T) {
	var buf bytes.Buffer
	statusHint(t.TempDir(), setup.RealSystem(), &buf)
	s := buf.String()
	if !strings.Contains(s, "not initialised") {
		t.Errorf("an uninitialised host must be told so:\n%s", s)
	}
	if !strings.Contains(s, "mymcp init") {
		t.Errorf("the next step must be named:\n%s", s)
	}
	if !strings.Contains(s, "mymcp serve") {
		t.Errorf("the trial-run option must be offered:\n%s", s)
	}
}

func TestBareInvocationExitsZeroNotUsageError(t *testing.T) {
	// Running `mymcp` with no args is a question, not a mistake.
	if code := run(nil); code != 0 {
		t.Fatalf("exit = %d, want 0", code)
	}
}

func TestHelpFlagIsRecognised(t *testing.T) {
	for _, arg := range []string{"-h", "--help", "help"} {
		if code := run([]string{arg}); code != 0 {
			t.Errorf("run(%q) = %d, want 0", arg, code)
		}
	}
}

func TestConfigExamplePrintsTemplate(t *testing.T) {
	if code := run([]string{"config", "example"}); code != 0 {
		t.Fatalf("exit = %d, want 0", code)
	}
}
```

Add to `go/cmd/mymcp/cli_more_test.go`:

```go
func TestUnknownCommandStillExitsTwo(t *testing.T) {
	if code := run([]string{"frobnicate"}); code != 2 {
		t.Fatalf("exit = %d, want 2", code)
	}
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd go && go test ./cmd/mymcp/ -v`
Expected: FAIL — `undefined: statusHint`; `run(nil)` returns 2; `--help` returns 2.

- [ ] **Step 3: Write minimal implementation**

In `go/cmd/mymcp/main.go`:

```go
// statusHint replaces the old bare-usage line. pipx has no post-install hook,
// so the binary itself has to say what the next step is.
func statusHint(configDir string, sys setup.System, w io.Writer) {
	fmt.Fprintf(w, "mymcp %s\n\n", version.Version)
	pf := setup.RunPreflight(configDir, sys)
	switch {
	case pf.ExistingEnv == "":
		fmt.Fprintf(w, "  ✗ not initialised (%s/.env does not exist)\n\n", configDir)
		fmt.Fprintln(w, "  Next:")
		fmt.Fprintln(w, "    sudo mymcp init      install as a systemd service (recommended)")
		fmt.Fprintln(w, "    mymcp serve          foreground trial run; tokens vanish on exit")
	default:
		active := false
		if out, err := sys.Run("systemctl", "is-active", "mymcp"); err == nil &&
			strings.TrimSpace(out) == "active" {
			active = true
		}
		if active {
			fmt.Fprintf(w, "  ✓ configured and running (%s/.env)\n\n", configDir)
			fmt.Fprintln(w, "  Next: mymcp doctor  |  mymcp token list")
		} else {
			fmt.Fprintf(w, "  ⚠ configured but not running (%s/.env)\n\n", configDir)
			fmt.Fprintln(w, "  Next:")
			fmt.Fprintln(w, "    sudo systemctl start mymcp")
			fmt.Fprintln(w, "    mymcp doctor")
		}
	}
	fmt.Fprintln(w, "\n  Commands: serve | init | doctor | token | config | version")
}
```

In `run`, replace the `len(args) == 0` branch with `statusHint("/etc/mymcp", setup.RealSystem(), os.Stdout); return 0`, add `case "-h", "--help", "help":` printing the command table and returning `0`, and add `case "config":` handling the single `example` subcommand by printing `setup.RenderEnv(setup.DefaultPlan(), "<generate-with-mymcp-init>")`.

In `go/internal/httpserver/httpserver.go`, extend the temp-token block:

```go
		fmt.Fprintln(os.Stderr, "[mymcp] tokens are in-memory; they vanish on exit.")
		fmt.Fprintln(os.Stderr, "[mymcp] to install as a persistent service: sudo mymcp init")
```

and make the missing-admin-token error actionable:

```go
	if cfg.AdminToken == "" {
		return fmt.Errorf("MYMCP_ADMIN_TOKEN is not set in %s — run `sudo mymcp init` to configure this host, or `mymcp doctor` to diagnose", config.DiscoveredEnvFile())
	}
```

In `scripts/install-offline.sh`, replace the closing block:

```bash
echo
echo "Done. Next steps:"
echo "  sudo mymcp init                         # configure + install the service"
echo "  mymcp serve                             # dev / quick try"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd go && go test ./... -v && go vet ./... && gofmt -l .` then `bash -n scripts/install-offline.sh`
Expected: PASS; `bash -n` silent.

- [ ] **Step 5: Commit**

```bash
git add go/cmd/mymcp/ go/internal/httpserver/httpserver.go scripts/install-offline.sh
git commit -m "feat(cli): status-aware guidance, -h, config example; drop stale install-service copy"
```

---

## Task 11: end-to-end smoke test

**Files:**
- Create: `tests/compat/test_init_doctor_e2e.py`
- Modify: `.github/workflows/ci.yml` (run it in the existing compat job)

**Interfaces:**
- Consumes: the built `mymcp` binary; the existing `tests/compat/conftest.py` fixtures.
- Produces: nothing consumed by later tasks. This is the backstop: every unit test above can pass while the pieces fail to connect.

- [ ] **Step 1: Write the failing test**

`tests/compat/test_init_doctor_e2e.py`:

```python
"""End-to-end: init -> serve -> doctor -> tools/list, against the real binary.

Runs unprivileged with every path redirected into tmp_path, and with
-start=false because CI has no systemd. Degraded mode is exactly the shape
this exercises, which is also what a container install gets.
"""

import json
import os
import subprocess
import time
import urllib.request

import pytest

BINARY = os.environ.get("MYMCP_BINARY", "/tmp/mymcp")


@pytest.mark.skipif(not os.path.exists(BINARY), reason="build /tmp/mymcp first")
def test_init_then_serve_then_doctor(tmp_path):
    cfg = tmp_path / "etc"
    port = 18765

    rc = subprocess.run(
        [
            BINARY, "init", "-yes",
            "-config-dir", str(cfg),
            "-log-dir", str(tmp_path / "log"),
            "-recorder-data-dir", str(tmp_path / "rec"),
            "-port", str(port),
            "-bind", "127.0.0.1",
            "-start=false",
        ],
        capture_output=True,
        text=True,
    )
    assert rc.returncode == 0, rc.stderr

    env_text = (cfg / ".env").read_text()
    assert "MYMCP_AUDIT_ENABLED=true" in env_text
    assert (cfg / ".env").stat().st_mode & 0o777 == 0o600
    tokens = json.loads((cfg / "tokens.json").read_text())
    client_token = next(
        tok for tok, info in tokens["tokens"].items() if info["name"] == "default"
    )

    server = subprocess.Popen(
        [BINARY, "serve", "-env-file", str(cfg / ".env")],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        _wait_for_port(port)

        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/mcp",
            data=json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}).encode(),
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream",
                "Authorization": f"Bearer {client_token}",
            },
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            body = resp.read().decode()
        assert '"name"' in body, body

        doctor = subprocess.run(
            [BINARY, "doctor", "-config-dir", str(cfg), "-json"],
            capture_output=True,
            text=True,
        )
        checks = json.loads(doctor.stdout)
        by_name = {c["name"]: c for c in checks}
        assert by_name["env permissions"]["severity"] == "ok"
        assert by_name["admin token"]["severity"] == "ok"
        assert by_name["audit enabled"]["severity"] == "ok"
    finally:
        server.terminate()
        server.wait(timeout=10)


def _wait_for_port(port, timeout=10.0):
    # A plain TCP connect, not an HTTP probe: init generates a metrics token by
    # default, so /metrics answers 401 and urlopen would raise forever.
    import socket

    deadline = time.time() + timeout
    while time.time() < deadline:
        with socket.socket() as s:
            s.settimeout(1)
            if s.connect_ex(("127.0.0.1", port)) == 0:
                return
        time.sleep(0.1)
    raise AssertionError(f"server never listened on {port}")


@pytest.mark.skipif(not os.path.exists(BINARY), reason="build /tmp/mymcp first")
def test_init_is_idempotent_and_keeps_tokens(tmp_path):
    cfg = tmp_path / "etc"
    args = [
        BINARY, "init", "-yes",
        "-config-dir", str(cfg),
        "-log-dir", str(tmp_path / "log"),
        "-recorder-data-dir", str(tmp_path / "rec"),
        "-start=false",
    ]
    assert subprocess.run(args, capture_output=True).returncode == 0
    first_env = (cfg / ".env").read_text()
    first_tokens = (cfg / "tokens.json").read_text()

    assert subprocess.run(args, capture_output=True).returncode == 0
    assert (cfg / ".env").read_text() == first_env, "re-run must not change .env"
    assert (cfg / "tokens.json").read_text() == first_tokens, "re-run must not add tokens"
```

- [ ] **Step 2: Run test to verify it fails**

Run:
```bash
cd go && go build -o /tmp/mymcp ./cmd/mymcp
cd /home/zhu/repos/mymcp && .venv/bin/python -m pytest tests/compat/test_init_doctor_e2e.py -v
```
Expected: FAIL before Tasks 1-10 land; PASS after. Run it now to confirm it exercises the real binary rather than skipping — a skipped test is not a passing test.

- [ ] **Step 3: Wire it into CI**

In `.github/workflows/ci.yml`, in the job that already builds the Go binary and runs `tests/compat/`, ensure `MYMCP_BINARY` points at the built binary so the skip guard does not silently disable the test:

```yaml
      - name: End-to-end init/doctor smoke
        env:
          MYMCP_BINARY: /tmp/mymcp
        run: |
          go build -o /tmp/mymcp ./go/cmd/mymcp
          python -m pytest tests/compat/test_init_doctor_e2e.py -v
```

- [ ] **Step 4: Run the full gate**

Run:
```bash
cd go && go test ./... && go vet ./... && gofmt -l .
cd /home/zhu/repos/mymcp && .venv/bin/python -m pytest tests/ -v --benchmark-disable && .venv/bin/python -m ruff check . && .venv/bin/python -m mypy src/mymcp
```
Expected: all PASS, `gofmt -l` silent.

- [ ] **Step 5: Commit**

```bash
git add tests/compat/test_init_doctor_e2e.py .github/workflows/ci.yml
git commit -m "test: end-to-end init -> serve -> doctor -> tools/list smoke"
```

---

## Done criteria for Plan 1

- `sudo mymcp init` on a fresh host with the binary already installed produces a running service and prints a client config that pastes into Claude Code unmodified.
- Re-running `mymcp init` changes nothing and rotates no tokens.
- `mymcp doctor` exits 1 with a named remedy for: loose `.env` mode, missing admin token, disabled audit, duplicate binaries on `PATH`, inactive unit, a stalled recorder.
- `mymcp`, `mymcp -h`, and a failed `mymcp serve` all name the next command to run.
- No new Go module dependency appears in `go/go.mod`.

Plan 2 (`install.sh`, release assets, offline bundle, README/CLAUDE.md, `.env.example` drift gate) builds on this and can be executed independently afterwards.
