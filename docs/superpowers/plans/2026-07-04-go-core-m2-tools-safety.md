# Go Core M2 (Full Tool Surface + Safety) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend the Go core to the full mutating tool surface (`bash_execute`, `write_file`, `edit_file`) with process-group cleanup and protected-path enforcement, plus the two cross-cutting safety systems the recorder and dashboards depend on: a JSON-lines audit writer (byte-compatible with the Python `RotatingFileHandler`, `audit.log.N` naming) and native Prometheus `/metrics`.

**Architecture:** A single `callTool` choke point in `mcpserver` (port of `src/mymcp/mcp_server.py:call_tool`) runs permission check → dispatch → result classification → audit → metrics for every tool, unknown or known, ok/error/denied. Audit is a mutex-guarded rotating file writer; on write failure the call returns `InternalError` (SOC red line). Metrics are a dedicated `prometheus.Registry` with `mymcp_*` names identical to the Python OTel→Prometheus output. bash spawns children in their own session (`Setsid`) and registers them in an in-flight table that `SIGTERM`/`SIGKILL` on shutdown.

**Tech Stack:** Go 1.25, `github.com/modelcontextprotocol/go-sdk`, `github.com/bmatcuk/doublestar/v4`, `github.com/prometheus/client_golang` (new), stdlib everything else. Tests: `go test`; black-box compat: pytest against a live server; the M2 acceptance test drives both servers and reads the audit log with the real Python `EventTailer`.

**Spec:** `docs/superpowers/specs/2026-07-04-go-core-rewrite-design.md` (milestone table row **M2**).
**Predecessor plan:** `docs/superpowers/plans/2026-07-04-go-core-m1-readonly.md` (merged as PR #66).
**Branch:** `feat/go-core-m2` off master (create it).
**Reference implementation (read when in doubt):** `src/mymcp/tools/bash.py`, `src/mymcp/tools/files.py`, `src/mymcp/audit.py`, `src/mymcp/audit_output.py`, `src/mymcp/observability/instruments.py`, `src/mymcp/server.py:71-201`, `src/mymcp/mcp_server.py:110-262`.

---

## Global Parity Rules (apply to every task)

- Response JSON key names, `error` codes, and marker strings (`"[TRUNCATED: total N bytes, showing first M bytes]"`, `"Command timed out after Ns"`, `"old_string appears N times. …"`) are copied byte-for-byte from the Python source.
- Error *messages* embedding OS strerror text need not match Python word-for-word; the compat suite asserts `success:false` + `error` code, not message prose.
- All limits/defaults come from config, never hardcoded at call sites.
- Audit JSON key set and values match `src/mymcp/audit.py`; `trace_id`/`span_id` are omitted (no OTel — both keys are optional today, so the recorder tailer is unaffected).
- Prometheus **metric names and label sets** match the Python output exactly; histogram **bucket boundaries** need not (dashboards query `_bucket`/`histogram_quantile`, which work with any boundaries).

## Known, Documented Divergences (intentional; record in commit messages)

1. **audit uses a custom rotating writer, not lumberjack.** The spec suggested `lumberjack`, but its backups are named `audit.log-<timestamp>.log`, whereas the recorder `EventTailer` (`src/mymcp/recorder/events.py:80-91`) specifically reads `audit.log.1` on rotation and Python's `RotatingFileHandler` rotates at a byte threshold. We port `RotatingFileHandler` semantics directly (~70 lines, zero new deps) so the tailer's rotation path and the byte threshold match.
2. **edit_file does not replicate Python's universal-newline translation.** Python `open(..., errors="replace")` in text mode silently converts `\r\n`→`\n` on read; the Go port preserves bytes. No compat test exercises CRLF edits; replicating the quirk would mean intentionally corrupting CRLF files. Documented, not replicated.
3. **bash exit code for self-signaled children is `-1`** (Go `ProcessState.ExitCode()`), where Python `returncode` would be the negative signal number. Not asserted by compat; the normal exit-code path matches exactly.
4. **request_id is an opaque 32-hex string**, not a UUIDv4. The tailer treats it as opaque; format is not part of any contract.

---

## File Map (what M2 creates / modifies)

```
go/
├── go.mod, go.sum                        # + github.com/prometheus/client_golang
├── internal/
│   ├── config/config.go                  # MODIFY: + M2 knobs, getBool()
│   ├── config/config_test.go             # MODIFY: + bool + new-int tests
│   ├── audit/audit.go                    # CREATE: rotating writer + Entry + output summaries
│   ├── audit/audit_test.go               # CREATE
│   ├── metrics/metrics.go                # CREATE: prometheus registry + collectors + Handler
│   ├── metrics/metrics_test.go           # CREATE
│   ├── tools/bash.go                      # CREATE: RunBash, in-flight table, ShutdownInflight
│   ├── tools/bash_test.go                 # CREATE
│   ├── tools/writefile.go                 # CREATE
│   ├── tools/writefile_test.go            # CREATE
│   ├── tools/editfile.go                  # CREATE
│   ├── tools/editfile_test.go             # CREATE
│   ├── mcpserver/tooldefs.go              # MODIFY: + bash_execute/write_file/edit_file schemas
│   ├── mcpserver/mcpserver.go             # MODIFY: Server struct + callTool choke point
│   ├── mcpserver/mcpserver_test.go        # MODIFY: 6 tools, audit+metrics wiring
│   ├── httpserver/httpserver.go           # MODIFY: /metrics, http metrics mw, request-id, bash shutdown, audit close
│   └── httpserver/httpserver_test.go      # MODIFY: new BuildMux signature, /metrics tests
tests/compat/
│   ├── conftest.py                        # MODIFY: metrics token + audit dir env
│   ├── test_tools_list.py                 # MODIFY: assert the 3 write tools too
│   ├── test_write_file.py                 # CREATE
│   ├── test_edit_file.py                  # CREATE
│   ├── test_bash.py                       # CREATE
│   ├── test_metrics.py                    # CREATE
│   └── test_audit.py                      # CREATE (M2 acceptance: EventTailer reads Go audit.log)
.github/workflows/ci.yml                   # MODIFY: both compat jobs enable audit + metrics
CHANGELOG.md                               # MODIFY: Unreleased entry
```

Tool visibility after M2: the Go registry contains **6** tools (`read_file`, `glob`, `grep`, `bash_execute`, `write_file`, `edit_file`). `prepare_upload`/`prepare_download`/`server_overview` remain M3. The compat suite still asserts *subset* presence with exact schemas; the full-set (9) assertion arrives in M3.

---

## Task 0: Branch

- [ ] **Step 1: Create the M2 branch off master**

```bash
cd /home/zhu/repos/mymcp
git checkout master && git pull
git checkout -b feat/go-core-m2
```

---

## Task 1: Config — M2 knobs

**Files:**
- Modify: `go/internal/config/config.go`
- Test: `go/internal/config/config_test.go`

- [ ] **Step 1: Write the failing tests**

Append to `go/internal/config/config_test.go`:

```go
func TestLoadM2Defaults(t *testing.T) {
	// Ensure a clean env: none of the M2 vars set.
	for _, k := range []string{
		"MYMCP_METRICS_TOKEN", "MYMCP_AUDIT_ENABLED", "MYMCP_AUDIT_MAX_BYTES",
		"MYMCP_AUDIT_BACKUP_COUNT", "MYMCP_BASH_MAX_OUTPUT_BYTES",
		"MYMCP_BASH_MAX_OUTPUT_BYTES_HARD", "MYMCP_WRITE_FILE_MAX_BYTES",
		"MYMCP_EDIT_STRING_MAX_BYTES", "MYMCP_AUDIT_OUTPUT_BASH_HEAD_BYTES",
		"MYMCP_AUDIT_OUTPUT_BASH_TAIL_BYTES",
	} {
		t.Setenv(k, "") // t.Setenv restores afterwards; set-then-unset below
		os.Unsetenv(k)
	}
	cfg, err := Load()
	if err != nil {
		t.Fatal(err)
	}
	if cfg.MetricsToken != "" {
		t.Fatalf("MetricsToken default = %q", cfg.MetricsToken)
	}
	if cfg.AuditEnabled {
		t.Fatal("AuditEnabled default must be false")
	}
	if cfg.AuditMaxBytes != 10*1024*1024 {
		t.Fatalf("AuditMaxBytes = %d", cfg.AuditMaxBytes)
	}
	if cfg.AuditBackupCount != 5 {
		t.Fatalf("AuditBackupCount = %d", cfg.AuditBackupCount)
	}
	if cfg.BashMaxOutputBytes != 102400 || cfg.BashMaxOutputBytesHard != 1048576 {
		t.Fatalf("bash byte defaults wrong: %d %d", cfg.BashMaxOutputBytes, cfg.BashMaxOutputBytesHard)
	}
	if cfg.WriteFileMaxBytes != 10*1024*1024 || cfg.EditStringMaxBytes != 1024*1024 {
		t.Fatalf("write/edit defaults wrong: %d %d", cfg.WriteFileMaxBytes, cfg.EditStringMaxBytes)
	}
	if cfg.AuditOutputBashHeadBytes != 4096 || cfg.AuditOutputBashTailBytes != 4096 {
		t.Fatalf("audit output defaults wrong: %d %d", cfg.AuditOutputBashHeadBytes, cfg.AuditOutputBashTailBytes)
	}
}

func TestLoadAuditEnabledBoolSpellings(t *testing.T) {
	for _, v := range []string{"true", "1", "yes", "on", "TRUE", "On"} {
		t.Setenv("MYMCP_AUDIT_ENABLED", v)
		cfg, err := Load()
		if err != nil || !cfg.AuditEnabled {
			t.Fatalf("%q should parse true (err=%v)", v, err)
		}
	}
	for _, v := range []string{"false", "0", "no", "off"} {
		t.Setenv("MYMCP_AUDIT_ENABLED", v)
		cfg, err := Load()
		if err != nil || cfg.AuditEnabled {
			t.Fatalf("%q should parse false (err=%v)", v, err)
		}
	}
	t.Setenv("MYMCP_AUDIT_ENABLED", "maybe")
	if _, err := Load(); err == nil {
		t.Fatal("invalid bool must error naming the variable")
	}
}
```

- [ ] **Step 2: Run to verify failure**

Run: `cd go && go test ./internal/config/ -run 'TestLoadM2Defaults|TestLoadAuditEnabledBoolSpellings' -v`
Expected: compile failure (`cfg.MetricsToken` undefined).

- [ ] **Step 3: Add the fields and loader lines**

In `go/internal/config/config.go`, extend the `Config` struct (add after `TokenFile`):

```go
	MetricsToken string

	BashMaxOutputBytes     int
	BashMaxOutputBytesHard int
	WriteFileMaxBytes      int
	EditStringMaxBytes     int
```

and after `AuditLogDir`:

```go
	AuditEnabled     bool
	AuditMaxBytes    int64
	AuditBackupCount int

	AuditOutputBashHeadBytes int
	AuditOutputBashTailBytes int
```

In `Load()`, after the `cfg.TokenFile = …` line add:

```go
	cfg.MetricsToken = getStr(get, "MYMCP_METRICS_TOKEN", "")
	if cfg.BashMaxOutputBytes, err = getInt(get, "MYMCP_BASH_MAX_OUTPUT_BYTES", 102400); err != nil {
		return nil, err
	}
	if cfg.BashMaxOutputBytesHard, err = getInt(get, "MYMCP_BASH_MAX_OUTPUT_BYTES_HARD", 1048576); err != nil {
		return nil, err
	}
	if cfg.WriteFileMaxBytes, err = getInt(get, "MYMCP_WRITE_FILE_MAX_BYTES", 10*1024*1024); err != nil {
		return nil, err
	}
	if cfg.EditStringMaxBytes, err = getInt(get, "MYMCP_EDIT_STRING_MAX_BYTES", 1024*1024); err != nil {
		return nil, err
	}
```

After the `cfg.AuditLogDir = …` line add:

```go
	if cfg.AuditEnabled, err = getBool(get, "MYMCP_AUDIT_ENABLED", false); err != nil {
		return nil, err
	}
	auditMax, err := getInt(get, "MYMCP_AUDIT_MAX_BYTES", 10*1024*1024)
	if err != nil {
		return nil, err
	}
	cfg.AuditMaxBytes = int64(auditMax)
	if cfg.AuditBackupCount, err = getInt(get, "MYMCP_AUDIT_BACKUP_COUNT", 5); err != nil {
		return nil, err
	}
	if cfg.AuditOutputBashHeadBytes, err = getInt(get, "MYMCP_AUDIT_OUTPUT_BASH_HEAD_BYTES", 4096); err != nil {
		return nil, err
	}
	if cfg.AuditOutputBashTailBytes, err = getInt(get, "MYMCP_AUDIT_OUTPUT_BASH_TAIL_BYTES", 4096); err != nil {
		return nil, err
	}
```

Add the `getBool` helper next to `getInt` (and drop the "Reserved for M2" comment on `ParseBool`):

```go
func getBool(get getter, key string, def bool) (bool, error) {
	v, ok := get(key)
	if !ok {
		return def, nil
	}
	return ParseBool(key, v)
}
```

- [ ] **Step 4: Run to verify pass**

Run: `cd go && go test ./internal/config/ -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
gofmt -w go/internal/config/
git add go/internal/config/
git commit -m "feat(go/config): add M2 knobs (audit, metrics, bash/write/edit limits)"
```

---

## Task 2: Audit writer + output summaries

**Files:**
- Create: `go/internal/audit/audit.go`
- Test: `go/internal/audit/audit_test.go`

Port `src/mymcp/audit.py` (writer + `RotatingFileHandler` semantics) and `src/mymcp/audit_output.py` (summaries). The `EventTailer` (`src/mymcp/recorder/events.py`) is the consumer; its rotation path reads `audit.log.1`.

- [ ] **Step 1: Write the failing tests**

Create `go/internal/audit/audit_test.go`:

```go
package audit

import (
	"bufio"
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func readLines(t *testing.T, path string) []map[string]any {
	t.Helper()
	f, err := os.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer f.Close()
	var out []map[string]any
	sc := bufio.NewScanner(f)
	for sc.Scan() {
		line := strings.TrimSpace(sc.Text())
		if line == "" {
			continue
		}
		var m map[string]any
		if err := json.Unmarshal([]byte(line), &m); err != nil {
			t.Fatalf("line not JSON object: %q", line)
		}
		out = append(out, m)
	}
	return out
}

func TestDisabledWriterIsNoop(t *testing.T) {
	dir := t.TempDir()
	w, err := New(false, dir, 1<<20, 5)
	if err != nil {
		t.Fatal(err)
	}
	if err := w.Log(Entry{TS: "t", Tool: "x", Result: "ok", Params: map[string]any{}}); err != nil {
		t.Fatal(err)
	}
	if _, err := os.Stat(filepath.Join(dir, "audit.log")); !os.IsNotExist(err) {
		t.Fatal("disabled writer must not create audit.log")
	}
}

func TestLogWritesJSONLinesInOrder(t *testing.T) {
	dir := t.TempDir()
	w, err := New(true, dir, 1<<20, 5)
	if err != nil {
		t.Fatal(err)
	}
	dur := 12
	if err := w.Log(Entry{
		TS: "2026-07-04T00:00:00Z", TokenName: "n", Role: "rw", IP: "1.2.3.4",
		Tool: "write_file", Params: map[string]any{"file_path": "/x"}, Result: "ok",
		DurationMs: &dur, Output: map[string]any{"path": "/x"},
	}); err != nil {
		t.Fatal(err)
	}
	lines := readLines(t, filepath.Join(dir, "audit.log"))
	if len(lines) != 1 {
		t.Fatalf("want 1 line, got %d", len(lines))
	}
	e := lines[0]
	for _, k := range []string{"ts", "token_name", "role", "ip", "tool", "params", "result", "duration_ms", "output"} {
		if _, ok := e[k]; !ok {
			t.Fatalf("missing key %q in %v", k, e)
		}
	}
	// Optional keys absent when unset.
	if _, ok := e["reason"]; ok {
		t.Fatal("reason must be omitted when empty")
	}
	if _, ok := e["error_code"]; ok {
		t.Fatal("error_code must be omitted when empty")
	}
}

func TestParamsAlwaysEmittedEvenWhenEmpty(t *testing.T) {
	dir := t.TempDir()
	w, _ := New(true, dir, 1<<20, 5)
	_ = w.Log(Entry{TS: "t", Tool: "x", Result: "ok", Params: map[string]any{}})
	line := readLines(t, filepath.Join(dir, "audit.log"))[0]
	if _, ok := line["params"].(map[string]any); !ok {
		t.Fatalf("params must serialize as an object, got %T", line["params"])
	}
}

func TestRotationRenamesToDotOne(t *testing.T) {
	dir := t.TempDir()
	// Tiny threshold so the second record forces a rollover.
	w, err := New(true, dir, 120, 5)
	if err != nil {
		t.Fatal(err)
	}
	big := strings.Repeat("A", 80)
	for i := 0; i < 3; i++ {
		if err := w.Log(Entry{TS: "t", Tool: "bash_execute", Result: "ok",
			Params: map[string]any{"command": big}}); err != nil {
			t.Fatal(err)
		}
	}
	if _, err := os.Stat(filepath.Join(dir, "audit.log.1")); err != nil {
		t.Fatalf("audit.log.1 must exist after rotation: %v", err)
	}
	// New file exists and inode differs from the rotated one (tailer relies on this).
	cur, _ := os.Stat(filepath.Join(dir, "audit.log"))
	old, _ := os.Stat(filepath.Join(dir, "audit.log.1"))
	if os.SameFile(cur, old) {
		t.Fatal("post-rotation audit.log must be a new inode")
	}
}

func TestTruncateBashOutputHeadTail(t *testing.T) {
	raw := []byte(strings.Repeat("x", 10) + strings.Repeat("y", 10))
	got := TruncateBashOutput(raw, 4, 4)
	if got["stdout_head"] != "xxxx" || got["stdout_tail"] != "yyyy" {
		t.Fatalf("head/tail wrong: %v", got)
	}
	if got["stdout_truncated_bytes"] != 12 {
		t.Fatalf("truncated_bytes = %v", got["stdout_truncated_bytes"])
	}
	if s, _ := got["stdout_sha256"].(string); len(s) != 64 {
		t.Fatalf("sha256 len = %d", len(s))
	}
	small := TruncateBashOutput([]byte("hi"), 4, 4)
	if small["stdout_head"] != "hi" || small["stdout_tail"] != "" || small["stdout_truncated_bytes"] != 0 {
		t.Fatalf("small case wrong: %v", small)
	}
}

func TestWriteFileOutputFirstLine(t *testing.T) {
	got := WriteFileOutput("/p", []byte("first\nsecond\n"))
	if got["path"] != "/p" || got["size_bytes"] != 13 || got["first_line"] != "first" {
		t.Fatalf("write output wrong: %v", got)
	}
}
```

- [ ] **Step 2: Run to verify failure**

Run: `cd go && go test ./internal/audit/ -v`
Expected: build failure (package `audit` does not exist).

- [ ] **Step 3: Implement `go/internal/audit/audit.go`**

```go
// Package audit writes JSON-lines audit records with size-based rotation,
// byte-compatible with the Python core (src/mymcp/audit.py). Rotation matches
// logging.handlers.RotatingFileHandler: rollover when the current size plus the
// incoming record (incl. newline) meets maxBytes, backups named audit.log.1,
// audit.log.2, … (higher = older) so the recorder EventTailer's rotation path
// (src/mymcp/recorder/events.py) works unchanged.
package audit

import (
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"sync"

	"github.com/algony-tony/mymcp/go/internal/fsutil"
)

// Entry mirrors the audit JSON object. Field order matches Python insertion
// order (cosmetic — the tailer parses by key). Params is always emitted (even
// {}); other optionals use omitempty. DurationMs is a pointer so a genuine 0 is
// emitted while an unset duration (denied calls) is omitted, matching Python.
type Entry struct {
	TS           string         `json:"ts"`
	TokenName    string         `json:"token_name"`
	Role         string         `json:"role"`
	IP           string         `json:"ip"`
	Tool         string         `json:"tool"`
	Params       map[string]any `json:"params"`
	Result       string         `json:"result"`
	RequestID    string         `json:"request_id,omitempty"`
	Reason       string         `json:"reason,omitempty"`
	ErrorCode    string         `json:"error_code,omitempty"`
	ErrorMessage string         `json:"error_message,omitempty"`
	DurationMs   *int           `json:"duration_ms,omitempty"`
	Output       map[string]any `json:"output,omitempty"`
}

// Writer is a thread-safe rotating JSON-lines writer.
type Writer struct {
	mu          sync.Mutex
	enabled     bool
	path        string
	maxBytes    int64
	backupCount int
	f           *os.File
	size        int64
}

// New opens the writer. When enabled is false the writer is a no-op that never
// touches the filesystem (audit disabled → src/mymcp/audit.py returns None).
func New(enabled bool, logDir string, maxBytes int64, backupCount int) (*Writer, error) {
	w := &Writer{enabled: enabled, maxBytes: maxBytes, backupCount: backupCount}
	if !enabled {
		return w, nil
	}
	if err := os.MkdirAll(logDir, 0o755); err != nil {
		return nil, err
	}
	w.path = filepath.Join(logDir, "audit.log")
	if err := w.open(); err != nil {
		return nil, err
	}
	return w, nil
}

func (w *Writer) open() error {
	f, err := os.OpenFile(w.path, os.O_CREATE|os.O_WRONLY|os.O_APPEND, 0o644)
	if err != nil {
		return err
	}
	st, err := f.Stat()
	if err != nil {
		f.Close()
		return err
	}
	w.f = f
	w.size = st.Size()
	return nil
}

// Log serializes and appends one record. Returns an error on any write/rotate
// failure; the caller increments mymcp_audit_write_failures_total and returns
// InternalError to the client (silent audit loss is a SOC red line).
func (w *Writer) Log(e Entry) error {
	if !w.enabled {
		return nil
	}
	raw, err := json.Marshal(e)
	if err != nil {
		return err
	}
	w.mu.Lock()
	defer w.mu.Unlock()
	// RotatingFileHandler.shouldRollover compares size + len(msg+"\n") >= maxBytes.
	if w.maxBytes > 0 && w.size+int64(len(raw))+1 >= w.maxBytes {
		if err := w.rotate(); err != nil {
			return err
		}
	}
	n, err := w.f.Write(append(raw, '\n'))
	w.size += int64(n)
	return err
}

// rotate mirrors RotatingFileHandler.doRollover: close, shift audit.log.i →
// audit.log.(i+1), audit.log → audit.log.1, reopen fresh.
func (w *Writer) rotate() error {
	if w.f != nil {
		w.f.Close()
		w.f = nil
	}
	if w.backupCount > 0 {
		for i := w.backupCount - 1; i >= 1; i-- {
			src := fmt.Sprintf("%s.%d", w.path, i)
			dst := fmt.Sprintf("%s.%d", w.path, i+1)
			if _, err := os.Stat(src); err == nil {
				_ = os.Rename(src, dst) // best-effort, as in stdlib logging
			}
		}
		_ = os.Rename(w.path, w.path+".1")
	}
	return w.open()
}

// Close flushes and releases the file (no-op when disabled).
func (w *Writer) Close() error {
	w.mu.Lock()
	defer w.mu.Unlock()
	if w.f == nil {
		return nil
	}
	err := w.f.Close()
	w.f = nil
	return err
}

// --- output summaries (port of src/mymcp/audit_output.py) ---

// TruncateBashOutput summarises stdout into head/tail with a sha256 of the whole.
func TruncateBashOutput(raw []byte, headBytes, tailBytes int) map[string]any {
	total := len(raw)
	sum := sha256.Sum256(raw)
	sha := hex.EncodeToString(sum[:])
	if total <= headBytes+tailBytes {
		return map[string]any{
			"stdout_head": fsutil.DecodeReplace(raw), "stdout_tail": "",
			"stdout_truncated_bytes": 0, "stdout_sha256": sha,
		}
	}
	return map[string]any{
		"stdout_head":            fsutil.DecodeReplace(raw[:headBytes]),
		"stdout_tail":            fsutil.DecodeReplace(raw[total-tailBytes:]),
		"stdout_truncated_bytes": total - headBytes - tailBytes,
		"stdout_sha256":          sha,
	}
}

// WriteFileOutput summarises a write_file effect.
func WriteFileOutput(path string, content []byte) map[string]any {
	firstLine := ""
	if len(content) > 0 {
		if i := bytes.IndexByte(content, '\n'); i >= 0 {
			firstLine = fsutil.DecodeReplace(content[:i])
		} else {
			firstLine = fsutil.DecodeReplace(content)
		}
	}
	sum := sha256.Sum256(content)
	return map[string]any{
		"path": path, "size_bytes": len(content),
		"sha256": hex.EncodeToString(sum[:]), "first_line": firstLine,
	}
}

// EditFileOutput summarises an edit_file effect.
func EditFileOutput(path string, linesAdded, linesRemoved, hunkCount int) map[string]any {
	return map[string]any{
		"path": path, "lines_added": linesAdded,
		"lines_removed": linesRemoved, "hunk_count": hunkCount,
	}
}
```

- [ ] **Step 4: Run to verify pass**

Run: `cd go && go test ./internal/audit/ -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
gofmt -w go/internal/audit/
git add go/internal/audit/
git commit -m "feat(go/audit): rotating JSON-lines writer + output summaries"
```

---

## Task 3: Metrics (Prometheus)

**Files:**
- Modify: `go/go.mod`, `go/go.sum`
- Create: `go/internal/metrics/metrics.go`
- Test: `go/internal/metrics/metrics_test.go`

Names must match the Python OTel→Prometheus output (`src/mymcp/observability/instruments.py`): `mymcp_tool_calls_total{tool,role,result}`, `mymcp_tool_duration_seconds{tool}`, `mymcp_http_requests_total{path,method,status}`, `mymcp_audit_write_failures_total`, `mymcp_bash_inflight_processes`.

- [ ] **Step 1: Add the dependency**

```bash
cd go && go get github.com/prometheus/client_golang@latest && go mod tidy && cd ..
```

- [ ] **Step 2: Write the failing tests**

Create `go/internal/metrics/metrics_test.go`:

```go
package metrics

import (
	"io"
	"net/http/httptest"
	"strings"
	"testing"
)

func scrape(t *testing.T, m *Metrics) string {
	t.Helper()
	req := httptest.NewRequest("GET", "/metrics", nil)
	rec := httptest.NewRecorder()
	m.Handler().ServeHTTP(rec, req)
	body, _ := io.ReadAll(rec.Result().Body)
	return string(body)
}

func TestMetricNamesPresent(t *testing.T) {
	inflight := 3
	m := New(func() float64 { return float64(inflight) })
	m.ToolCalls.WithLabelValues("read_file", "rw", "ok").Inc()
	m.ToolDuration.WithLabelValues("read_file").Observe(0.01)
	m.HTTPRequests.WithLabelValues("/mcp", "POST", "200").Inc()
	m.IncAuditFailure()

	out := scrape(t, m)
	for _, want := range []string{
		`mymcp_tool_calls_total{result="ok",role="rw",tool="read_file"} 1`,
		"mymcp_tool_duration_seconds_bucket",
		`mymcp_http_requests_total{method="POST",path="/mcp",status="200"} 1`,
		"mymcp_audit_write_failures_total 1",
		"mymcp_bash_inflight_processes 3",
	} {
		if !strings.Contains(out, want) {
			t.Fatalf("scrape missing %q\n---\n%s", want, out)
		}
	}
}

func TestRegistryIsIsolated(t *testing.T) {
	// A dedicated registry must not leak Go runtime collectors into the output.
	m := New(func() float64 { return 0 })
	out := scrape(t, m)
	if strings.Contains(out, "go_goroutines") || strings.Contains(out, "process_cpu_seconds_total") {
		t.Fatalf("registry should contain only mymcp_* metrics:\n%s", out)
	}
}
```

- [ ] **Step 3: Implement `go/internal/metrics/metrics.go`**

```go
// Package metrics exposes the mymcp_* Prometheus metrics on a dedicated
// registry. Names and label sets are identical to the Python core's
// OTel→Prometheus output (src/mymcp/observability/instruments.py) so the shipped
// Grafana dashboards keep working. Histogram buckets need not match OTel.
package metrics

import (
	"net/http"

	"github.com/prometheus/client_golang/prometheus"
	"github.com/prometheus/client_golang/prometheus/promhttp"
)

type Metrics struct {
	registry      *prometheus.Registry
	ToolCalls     *prometheus.CounterVec
	ToolDuration  *prometheus.HistogramVec
	HTTPRequests  *prometheus.CounterVec
	auditFailures prometheus.Counter
}

// New builds the registry. inflight is a callback returning the live bash
// subprocess count (backs the mymcp_bash_inflight_processes gauge); pass a
// closure over tools.InflightCount so this package stays free of a tools import.
func New(inflight func() float64) *Metrics {
	reg := prometheus.NewRegistry()
	m := &Metrics{
		registry: reg,
		ToolCalls: prometheus.NewCounterVec(prometheus.CounterOpts{
			Name: "mymcp_tool_calls_total", Help: "Total MCP tool calls",
		}, []string{"tool", "role", "result"}),
		ToolDuration: prometheus.NewHistogramVec(prometheus.HistogramOpts{
			Name: "mymcp_tool_duration_seconds", Help: "MCP tool call duration",
			Buckets: prometheus.DefBuckets,
		}, []string{"tool"}),
		HTTPRequests: prometheus.NewCounterVec(prometheus.CounterOpts{
			Name: "mymcp_http_requests_total", Help: "Total HTTP requests",
		}, []string{"path", "method", "status"}),
		auditFailures: prometheus.NewCounter(prometheus.CounterOpts{
			Name: "mymcp_audit_write_failures_total", Help: "Audit log write failures",
		}),
	}
	reg.MustRegister(m.ToolCalls, m.ToolDuration, m.HTTPRequests, m.auditFailures)
	reg.MustRegister(prometheus.NewGaugeFunc(prometheus.GaugeOpts{
		Name: "mymcp_bash_inflight_processes",
		Help: "Live count of tracked bash subprocesses",
	}, inflight))
	return m
}

// Handler serves the metrics in Prometheus text format.
func (m *Metrics) Handler() http.Handler {
	return promhttp.HandlerFor(m.registry, promhttp.HandlerOpts{})
}

// IncAuditFailure bumps mymcp_audit_write_failures_total.
func (m *Metrics) IncAuditFailure() { m.auditFailures.Inc() }
```

- [ ] **Step 4: Run to verify pass**

Run: `cd go && go test ./internal/metrics/ -v`
Expected: PASS. (Label order in the scrape string is deterministic — Prometheus sorts label names alphabetically, which is why the test expects `method,path,status` and `result,role,tool`.)

- [ ] **Step 5: Commit**

```bash
gofmt -w go/internal/metrics/
git add go/go.mod go/go.sum go/internal/metrics/
git commit -m "feat(go/metrics): native Prometheus registry with mymcp_* names"
```

---

## Task 4: bash_execute tool

**Files:**
- Create: `go/internal/tools/bash.go`
- Test: `go/internal/tools/bash_test.go`

Port `src/mymcp/tools/bash.py`. Process-group cleanup, timeout, truncation, in-flight table + `ShutdownInflight`/`InflightCount` (used later by httpserver and metrics).

- [ ] **Step 1: Write the failing tests**

Create `go/internal/tools/bash_test.go`:

```go
package tools

import (
	"strings"
	"testing"
	"time"
)

func TestRunBashBasic(t *testing.T) {
	d := testDeps(t)
	res := RunBash(d, "printf 'hi'", 30, "/", d.Cfg.BashMaxOutputBytes)
	if res["stdout"] != "hi" || res["exit_code"] != 0 || res["timed_out"] != false {
		t.Fatalf("res = %v", res)
	}
}

func TestRunBashNonZeroExit(t *testing.T) {
	d := testDeps(t)
	res := RunBash(d, "exit 3", 30, "/", d.Cfg.BashMaxOutputBytes)
	if res["exit_code"] != 3 || res["timed_out"] != false {
		t.Fatalf("res = %v", res)
	}
}

func TestRunBashTimeout(t *testing.T) {
	d := testDeps(t)
	start := time.Now()
	res := RunBash(d, "sleep 5", 1, "/", d.Cfg.BashMaxOutputBytes)
	if res["timed_out"] != true || res["exit_code"] != -1 {
		t.Fatalf("res = %v", res)
	}
	if res["stderr"] != "Command timed out after 1s" {
		t.Fatalf("stderr = %v", res["stderr"])
	}
	if time.Since(start) > 4*time.Second {
		t.Fatalf("timeout took too long: %v", time.Since(start))
	}
}

func TestRunBashOutputTruncation(t *testing.T) {
	d := testDeps(t)
	res := RunBash(d, "printf 'aaaaaaaaaa'", 30, "/", 4) // limit 4
	out := res["stdout"].(string)
	if !strings.HasPrefix(out, "aaaa\n[TRUNCATED: total 10 bytes, showing first 4 bytes]") {
		t.Fatalf("truncation wrong: %q", out)
	}
}

func TestRunBashBadWorkingDir(t *testing.T) {
	d := testDeps(t)
	res := RunBash(d, "true", 30, "/no/such/dir/xyz", d.Cfg.BashMaxOutputBytes)
	if res["success"] != false || res["error"] != "FileNotFoundError" {
		t.Fatalf("res = %v", res)
	}
}

func TestShutdownInflightKillsProcessGroup(t *testing.T) {
	d := testDeps(t)
	done := make(chan map[string]any, 1)
	go func() { done <- RunBash(d, "sleep 30", 600, "/", d.Cfg.BashMaxOutputBytes) }()
	// Wait for the child to register.
	deadline := time.Now().Add(2 * time.Second)
	for InflightCount() == 0 && time.Now().Before(deadline) {
		time.Sleep(10 * time.Millisecond)
	}
	if InflightCount() == 0 {
		t.Fatal("child never registered in the in-flight table")
	}
	ShutdownInflight(1) // TERM, 1s grace, KILL
	select {
	case res := <-done:
		// Killed process: negative/-1 exit or timed_out — either way it returned.
		_ = res
	case <-time.After(5 * time.Second):
		t.Fatal("ShutdownInflight did not stop the sleeping child")
	}
	if InflightCount() != 0 {
		t.Fatalf("in-flight table not drained: %d", InflightCount())
	}
}
```

- [ ] **Step 2: Run to verify failure**

Run: `cd go && go test ./internal/tools/ -run 'RunBash|ShutdownInflight' -v`
Expected: build failure (`RunBash` undefined).

- [ ] **Step 3: Implement `go/internal/tools/bash.go`**

```go
package tools

import (
	"bytes"
	"errors"
	"fmt"
	"io/fs"
	"os/exec"
	"sync"
	"syscall"
	"time"

	"github.com/algony-tony/mymcp/go/internal/fsutil"
)

var (
	inflightMu sync.Mutex
	inflight   = map[*exec.Cmd]struct{}{}
)

func trackProcess(c *exec.Cmd) {
	inflightMu.Lock()
	inflight[c] = struct{}{}
	inflightMu.Unlock()
}

func untrackProcess(c *exec.Cmd) {
	inflightMu.Lock()
	delete(inflight, c)
	inflightMu.Unlock()
}

func stillTracked(c *exec.Cmd) bool {
	inflightMu.Lock()
	defer inflightMu.Unlock()
	_, ok := inflight[c]
	return ok
}

// InflightCount is the live count of tracked bash subprocesses (metrics gauge).
func InflightCount() int {
	inflightMu.Lock()
	defer inflightMu.Unlock()
	return len(inflight)
}

// signalProcessGroup sends sig to the child's process group. With Setsid the
// child leads its own group (pgid == pid). If the child unexpectedly shares our
// group, fall back to a per-process signal so we never SIGTERM the server —
// parity with _signal_process_tree in src/mymcp/tools/bash.py.
func signalProcessGroup(c *exec.Cmd, sig syscall.Signal) {
	if c.Process == nil {
		return
	}
	pgid, err := syscall.Getpgid(c.Process.Pid)
	if err != nil {
		return
	}
	if pgid == syscall.Getpgrp() {
		_ = c.Process.Signal(sig)
		return
	}
	_ = syscall.Kill(-pgid, sig)
}

// ShutdownInflight SIGTERMs every tracked process group, waits graceSec, then
// SIGKILLs survivors. Safe to call from the shutdown path; mirrors
// shutdown_inflight_processes in src/mymcp/tools/bash.py.
func ShutdownInflight(graceSec int) {
	inflightMu.Lock()
	snapshot := make([]*exec.Cmd, 0, len(inflight))
	for c := range inflight {
		snapshot = append(snapshot, c)
	}
	inflightMu.Unlock()

	for _, c := range snapshot {
		signalProcessGroup(c, syscall.SIGTERM)
	}
	if graceSec < 0 {
		graceSec = 0
	}
	deadline := time.Now().Add(time.Duration(graceSec) * time.Second)
	for time.Now().Before(deadline) {
		if allExited(snapshot) {
			return
		}
		time.Sleep(50 * time.Millisecond)
	}
	for _, c := range snapshot {
		signalProcessGroup(c, syscall.SIGKILL)
	}
}

func allExited(cmds []*exec.Cmd) bool {
	for _, c := range cmds {
		if stillTracked(c) {
			return false
		}
	}
	return true
}

// RunBash runs command via /bin/sh -c in its own session, capturing stdout and
// stderr, each truncated to maxOutputBytes. Return keys are the compat contract.
func RunBash(d Deps, command string, timeout int, workingDir string, maxOutputBytes int) map[string]any {
	if timeout < 1 {
		timeout = 1
	}
	if timeout > 600 {
		timeout = 600
	}
	if maxOutputBytes < 1 {
		maxOutputBytes = 1
	}
	if maxOutputBytes > d.Cfg.BashMaxOutputBytesHard {
		maxOutputBytes = d.Cfg.BashMaxOutputBytesHard
	}

	cmd := exec.Command("/bin/sh", "-c", command)
	cmd.Dir = workingDir
	cmd.SysProcAttr = &syscall.SysProcAttr{Setsid: true}
	var stdout, stderr bytes.Buffer
	cmd.Stdout = &stdout
	cmd.Stderr = &stderr

	if err := cmd.Start(); err != nil {
		switch {
		case errors.Is(err, fs.ErrNotExist):
			return map[string]any{
				"success": false, "error": "FileNotFoundError",
				"message":    "Working directory not found: " + workingDir,
				"suggestion": "Check that the working_dir path exists",
			}
		case errors.Is(err, fs.ErrPermission):
			return map[string]any{
				"success": false, "error": "PermissionError",
				"message": err.Error(), "suggestion": "Check directory permissions",
			}
		default:
			return map[string]any{"success": false, "error": "OSError", "message": err.Error()}
		}
	}

	trackProcess(cmd)
	defer untrackProcess(cmd)

	done := make(chan struct{})
	go func() { _ = cmd.Wait(); close(done) }()

	select {
	case <-time.After(time.Duration(timeout) * time.Second):
		signalProcessGroup(cmd, syscall.SIGTERM)
		select {
		case <-done:
		case <-time.After(2 * time.Second):
			signalProcessGroup(cmd, syscall.SIGKILL)
			<-done
		}
		return map[string]any{
			"stdout": "", "stderr": fmt.Sprintf("Command timed out after %ds", timeout),
			"exit_code": -1, "timed_out": true,
		}
	case <-done:
	}

	return map[string]any{
		"stdout":    truncateOutput(stdout.Bytes(), maxOutputBytes),
		"stderr":    truncateOutput(stderr.Bytes(), maxOutputBytes),
		"exit_code": cmd.ProcessState.ExitCode(),
		"timed_out": false,
	}
}

func truncateOutput(data []byte, limit int) string {
	if len(data) <= limit {
		return fsutil.DecodeReplace(data)
	}
	shown := fsutil.DecodeReplace(data[:limit])
	return fmt.Sprintf("%s\n[TRUNCATED: total %d bytes, showing first %d bytes]", shown, len(data), limit)
}
```

- [ ] **Step 4: Run to verify pass**

Run: `cd go && go test ./internal/tools/ -run 'RunBash|ShutdownInflight' -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
gofmt -w go/internal/tools/
git add go/internal/tools/bash.go go/internal/tools/bash_test.go
git commit -m "feat(go/tools): bash_execute with process-group cleanup and timeout"
```

---

## Task 5: write_file tool

**Files:**
- Create: `go/internal/tools/writefile.go`
- Test: `go/internal/tools/writefile_test.go`

Port `write_file` from `src/mymcp/tools/files.py:154-179`.

- [ ] **Step 1: Write the failing tests**

Create `go/internal/tools/writefile_test.go`:

```go
package tools

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestWriteFileCreatesAndReports(t *testing.T) {
	d := testDeps(t)
	p := filepath.Join(t.TempDir(), "sub", "new.txt")
	res := WriteFile(d, p, "hello\n")
	if res["success"] != true || res["bytes_written"] != 6 {
		t.Fatalf("res = %v", res)
	}
	got, _ := os.ReadFile(p)
	if string(got) != "hello\n" {
		t.Fatalf("file = %q", got)
	}
}

func TestWriteFileProtected(t *testing.T) {
	d := testDeps(t)
	dir := t.TempDir()
	d.Protected = append(d.Protected, protectedAll(dir))
	res := WriteFile(d, filepath.Join(dir, "x"), "no")
	if res["success"] != false || res["error"] != "ProtectedPath" {
		t.Fatalf("res = %v", res)
	}
}

func TestWriteFileTooLarge(t *testing.T) {
	d := testDeps(t)
	d.Cfg.WriteFileMaxBytes = 4
	res := WriteFile(d, filepath.Join(t.TempDir(), "x"), "toolong")
	if res["success"] != false || res["error"] != "FileTooLarge" {
		t.Fatalf("res = %v", res)
	}
	if !strings.Contains(res["message"].(string), "max is 4") {
		t.Fatalf("message = %v", res["message"])
	}
}
```

- [ ] **Step 2: Run to verify failure**

Run: `cd go && go test ./internal/tools/ -run TestWriteFile -v`
Expected: build failure (`WriteFile` undefined).

- [ ] **Step 3: Implement `go/internal/tools/writefile.go`**

```go
package tools

import (
	"errors"
	"fmt"
	"io/fs"
	"os"
	"path/filepath"

	"github.com/algony-tony/mymcp/go/internal/fsutil"
)

// WriteFile ports write_file: create/overwrite a file, creating parent dirs.
func WriteFile(d Deps, filePath, content string) map[string]any {
	if msg := fsutil.CheckProtectedPath(filePath, fsutil.ModeWrite, d.Protected); msg != "" {
		return map[string]any{"success": false, "error": "ProtectedPath", "message": msg}
	}
	contentBytes := []byte(content)
	if len(contentBytes) > d.Cfg.WriteFileMaxBytes {
		return map[string]any{
			"success": false, "error": "FileTooLarge",
			"message": fmt.Sprintf("Content is %d bytes, max is %d (10MB)",
				len(contentBytes), d.Cfg.WriteFileMaxBytes),
			"suggestion": "Use the /files/upload endpoint for large files",
		}
	}
	if err := writeTextFile(filePath, contentBytes); err != nil {
		if errors.Is(err, fs.ErrPermission) {
			return map[string]any{
				"success": false, "error": "PermissionError",
				"message": err.Error(), "suggestion": "Check write permissions",
			}
		}
		return map[string]any{"success": false, "error": "OSError", "message": err.Error()}
	}
	return map[string]any{"success": true, "bytes_written": len(contentBytes)}
}

// writeTextFile mirrors _write_text: makedirs(parent), then write. Shared with
// edit_file.
func writeTextFile(path string, data []byte) error {
	parent := filepath.Dir(absOrSelf(path))
	if err := os.MkdirAll(parent, 0o755); err != nil {
		return err
	}
	return os.WriteFile(path, data, 0o644)
}

func absOrSelf(p string) string {
	if abs, err := filepath.Abs(p); err == nil {
		return abs
	}
	return p
}
```

- [ ] **Step 4: Run to verify pass**

Run: `cd go && go test ./internal/tools/ -run TestWriteFile -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
gofmt -w go/internal/tools/
git add go/internal/tools/writefile.go go/internal/tools/writefile_test.go
git commit -m "feat(go/tools): write_file with protected-path + size checks"
```

---

## Task 6: edit_file tool

**Files:**
- Create: `go/internal/tools/editfile.go`
- Test: `go/internal/tools/editfile_test.go`

Port `edit_file` from `src/mymcp/tools/files.py:187-249`.

- [ ] **Step 1: Write the failing tests**

Create `go/internal/tools/editfile_test.go`:

```go
package tools

import (
	"os"
	"path/filepath"
	"testing"
)

func writeEditFile(t *testing.T, content string) (Deps, string) {
	t.Helper()
	d := testDeps(t)
	p := filepath.Join(t.TempDir(), "e.txt")
	if err := os.WriteFile(p, []byte(content), 0o644); err != nil {
		t.Fatal(err)
	}
	return d, p
}

func TestEditFileSingleReplacement(t *testing.T) {
	d, p := writeEditFile(t, "alpha beta alpha")
	res := EditFile(d, p, "beta", "BETA", false)
	if res["success"] != true || res["replacements"] != 1 {
		t.Fatalf("res = %v", res)
	}
	got, _ := os.ReadFile(p)
	if string(got) != "alpha BETA alpha" {
		t.Fatalf("file = %q", got)
	}
}

func TestEditFileAmbiguousWithoutReplaceAll(t *testing.T) {
	d, p := writeEditFile(t, "x x x")
	res := EditFile(d, p, "x", "y", false)
	if res["success"] != false || res["error"] != "AmbiguousMatch" {
		t.Fatalf("res = %v", res)
	}
	if res["message"] != "x appears 3 times. Set replace_all=true to replace all occurrences." {
		t.Fatalf("message = %v", res["message"])
	}
}

func TestEditFileReplaceAll(t *testing.T) {
	d, p := writeEditFile(t, "x x x")
	res := EditFile(d, p, "x", "y", true)
	if res["success"] != true || res["replacements"] != 3 {
		t.Fatalf("res = %v", res)
	}
}

func TestEditFileStringNotFound(t *testing.T) {
	d, p := writeEditFile(t, "hello")
	res := EditFile(d, p, "absent", "z", false)
	if res["success"] != false || res["error"] != "StringNotFound" {
		t.Fatalf("res = %v", res)
	}
}

func TestEditFileMissingFile(t *testing.T) {
	d := testDeps(t)
	res := EditFile(d, filepath.Join(t.TempDir(), "nope.txt"), "a", "b", false)
	if res["success"] != false || res["error"] != "FileNotFoundError" {
		t.Fatalf("res = %v", res)
	}
}
```

- [ ] **Step 2: Run to verify failure**

Run: `cd go && go test ./internal/tools/ -run TestEditFile -v`
Expected: build failure (`EditFile` undefined).

- [ ] **Step 3: Implement `go/internal/tools/editfile.go`**

```go
package tools

import (
	"errors"
	"fmt"
	"io/fs"
	"os"
	"strings"

	"github.com/algony-tony/mymcp/go/internal/fsutil"
)

// EditFile ports edit_file: replace old_string (unique unless replaceAll).
// The file is read with UTF-8 replacement decoding, matching Python's
// open(errors="replace"). (Python's text-mode CRLF→LF translation is not
// replicated — see plan divergence #2.)
func EditFile(d Deps, filePath, oldString, newString string, replaceAll bool) map[string]any {
	if msg := fsutil.CheckProtectedPath(filePath, fsutil.ModeWrite, d.Protected); msg != "" {
		return map[string]any{"success": false, "error": "ProtectedPath", "message": msg}
	}
	if len(oldString) > d.Cfg.EditStringMaxBytes {
		return map[string]any{"success": false, "error": "FileTooLarge", "message": "old_string exceeds 1MB limit"}
	}
	if len(newString) > d.Cfg.EditStringMaxBytes {
		return map[string]any{"success": false, "error": "FileTooLarge", "message": "new_string exceeds 1MB limit"}
	}

	raw, err := os.ReadFile(filePath)
	if err != nil {
		if errors.Is(err, fs.ErrNotExist) {
			return map[string]any{"success": false, "error": "FileNotFoundError", "message": "File not found: " + filePath}
		}
		if errors.Is(err, fs.ErrPermission) {
			return map[string]any{"success": false, "error": "PermissionError", "message": err.Error()}
		}
		return map[string]any{"success": false, "error": "OSError", "message": err.Error()}
	}
	content := fsutil.DecodeReplace(raw)

	count := strings.Count(content, oldString)
	if count == 0 {
		return map[string]any{"success": false, "error": "StringNotFound", "message": "old_string not found in file"}
	}
	if count > 1 && !replaceAll {
		return map[string]any{
			"success": false, "error": "AmbiguousMatch",
			"message": fmt.Sprintf("%s appears %d times. Set replace_all=true to replace all occurrences.", oldString, count),
		}
	}

	var newContent string
	var replacements int
	if replaceAll {
		newContent = strings.ReplaceAll(content, oldString, newString)
		replacements = count
	} else {
		newContent = strings.Replace(content, oldString, newString, 1)
		replacements = 1
	}

	if err := writeTextFile(filePath, []byte(newContent)); err != nil {
		if errors.Is(err, fs.ErrPermission) {
			return map[string]any{"success": false, "error": "PermissionError", "message": err.Error()}
		}
		return map[string]any{"success": false, "error": "OSError", "message": err.Error()}
	}
	return map[string]any{"success": true, "replacements": replacements}
}
```

> **Parity note for the reviewer:** Python's `AmbiguousMatch` message is `f"old_string appears {count} times. …"` — the literal words "old_string", not the value. Wait: read `src/mymcp/tools/files.py:231-235` — it is the literal string `"old_string appears {count} times."`. **Correct the implementation** to use the literal token, not the value:
> ```go
> "message": fmt.Sprintf("old_string appears %d times. Set replace_all=true to replace all occurrences.", count),
> ```
> and update `TestEditFileAmbiguousWithoutReplaceAll` to expect `"old_string appears 3 times. Set replace_all=true to replace all occurrences."`.

- [ ] **Step 4: Apply the parity correction above, then run to verify pass**

Run: `cd go && go test ./internal/tools/ -run TestEditFile -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
gofmt -w go/internal/tools/
git add go/internal/tools/editfile.go go/internal/tools/editfile_test.go
git commit -m "feat(go/tools): edit_file with uniqueness + size checks"
```

---

## Task 7: mcpserver — Server struct + callTool choke point

**Files:**
- Modify: `go/internal/mcpserver/tooldefs.go` (add 3 write-tool schemas)
- Modify: `go/internal/mcpserver/mcpserver.go` (Server struct, callTool, register write tools, dispatch cases)
- Test: `go/internal/mcpserver/mcpserver_test.go`

This is the heart of M2: one `callTool` runs permission → dispatch → classify → audit → metrics for every call, porting `src/mymcp/mcp_server.py:110-262`.

- [ ] **Step 1: Add the three write-tool schemas to `tooldefs.go`**

Append to the `toolDefs` slice in `go/internal/mcpserver/tooldefs.go` (verbatim from `src/mymcp/tool_definitions.py`):

```go
	{
		Name: "bash_execute",
		Description: "Execute any shell command on the Linux server. " +
			"Stateless: each call is a fresh subprocess, no persistent shell state.\n\n" +
			"WARNING: bash_execute is NOT subject to MYMCP_PROTECTED_PATHS. It can read " +
			"or modify any path the service user can access (including audit logs and " +
			"tokens.json). Untrusted clients should be issued ro tokens, which cannot " +
			"call this tool.\n\n" +
			"Defaults: working_dir='/' if omitted; timeout 30s (max 600s, clamped). " +
			"On timeout, exit_code is -1 and timed_out is true.",
		SchemaJSON: `{
  "type": "object",
  "properties": {
    "command": {"type": "string", "description": "Shell command to run"},
    "timeout": {"type": "integer", "description": "Timeout seconds (default 30, max 600)"},
    "working_dir": {"type": "string", "description": "Working directory (default /)"},
    "max_output_bytes": {"type": "integer", "description": "Max stdout/stderr bytes each (default 102400)"}
  },
  "required": ["command"],
  "additionalProperties": false
}`,
	},
	{
		Name:        "write_file",
		Description: "Create or overwrite a file. Max 10MB.",
		SchemaJSON: `{
  "type": "object",
  "properties": {
    "file_path": {"type": "string", "description": "Absolute path"},
    "content": {"type": "string", "description": "File content (max 10MB)"}
  },
  "required": ["file_path", "content"],
  "additionalProperties": false
}`,
	},
	{
		Name:        "edit_file",
		Description: "Replace a string in a file. old_string must be unique unless replace_all=true.",
		SchemaJSON: `{
  "type": "object",
  "properties": {
    "file_path": {"type": "string"},
    "old_string": {"type": "string", "description": "String to find (max 1MB)"},
    "new_string": {"type": "string", "description": "Replacement string (max 1MB)"},
    "replace_all": {"type": "boolean", "description": "Replace every occurrence (default false)"}
  },
  "required": ["file_path", "old_string", "new_string"],
  "additionalProperties": false
}`,
	},
```

> **Verify byte-for-byte:** description strings and schema field descriptions must match `src/mymcp/tool_definitions.py` exactly, since `test_tools_list.py` deep-compares them. Diff against the Python literals before committing.

- [ ] **Step 2: Rewrite `go/internal/mcpserver/mcpserver.go`**

Replace the entire file with:

```go
// Package mcpserver assembles the MCP server: tool registration and the central
// callTool choke point (permission → dispatch → classify → audit → metrics),
// a line-for-line port of src/mymcp/mcp_server.py's call_tool.
package mcpserver

import (
	"context"
	"encoding/json"
	"fmt"
	"log"
	"strings"
	"time"
	"unicode/utf8"

	"github.com/modelcontextprotocol/go-sdk/mcp"

	"github.com/algony-tony/mymcp/go/internal/audit"
	"github.com/algony-tony/mymcp/go/internal/metrics"
	"github.com/algony-tony/mymcp/go/internal/tools"
	"github.com/algony-tony/mymcp/go/internal/version"
)

var readTools = map[string]bool{"read_file": true, "glob": true, "grep": true}
var writeTools = map[string]bool{"bash_execute": true, "write_file": true, "edit_file": true}

type ctxKey int

const authInfoKey ctxKey = 0

// AuthInfo carries the authenticated caller's identity through the context.
type AuthInfo struct {
	TokenName string
	Role      string
	IP        string
	RequestID string
}

// WithAuthInfo is called by the HTTP auth middleware to stash token info.
func WithAuthInfo(ctx context.Context, info AuthInfo) context.Context {
	return context.WithValue(ctx, authInfoKey, info)
}

func authInfoFrom(ctx context.Context) AuthInfo {
	if v, ok := ctx.Value(authInfoKey).(AuthInfo); ok {
		return v
	}
	// Least-privilege default so a propagation bug degrades to read-only.
	return AuthInfo{TokenName: "unknown", Role: "ro", IP: "unknown"}
}

// Server bundles the dependencies the callTool pipeline needs.
type Server struct {
	deps   tools.Deps
	audit  *audit.Writer
	metric *metrics.Metrics
}

// New constructs the server. audit may be a disabled writer; metric is required.
func New(d tools.Deps, a *audit.Writer, m *metrics.Metrics) *Server {
	return &Server{deps: d, audit: a, metric: m}
}

// ToolNames returns the registered tool names.
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

// Build wires the SDK server. Every registered tool shares the callTool handler;
// a receiving middleware routes unknown-tool calls through callTool too (so they
// are audited/counted as denied and return Python's PermissionDenied shape).
func (s *Server) Build() *mcp.Server {
	srv := mcp.NewServer(&mcp.Implementation{Name: "linux-server", Version: version.Version}, nil)
	for _, td := range toolDefs {
		td := td
		srv.AddTool(
			&mcp.Tool{Name: td.Name, Description: td.Description, InputSchema: mustSchema(td.SchemaJSON)},
			func(ctx context.Context, req *mcp.CallToolRequest) (*mcp.CallToolResult, error) {
				return textResult(s.callTool(ctx, td.Name, req.Params.Arguments)), nil
			},
		)
	}
	srv.AddReceivingMiddleware(func(next mcp.MethodHandler) mcp.MethodHandler {
		return func(ctx context.Context, method string, req mcp.Request) (mcp.Result, error) {
			if method == "tools/call" {
				if p, ok := req.GetParams().(*mcp.CallToolParamsRaw); ok {
					if !readTools[p.Name] && !writeTools[p.Name] {
						return textResult(s.callTool(ctx, p.Name, p.Arguments)), nil
					}
				}
			}
			return next(ctx, method, req)
		}
	})
	return srv
}

// callTool is the single choke point. Returns the JSON text the client receives.
func (s *Server) callTool(ctx context.Context, name string, rawArgs json.RawMessage) string {
	info := authInfoFrom(ctx)
	args := parseArgs(rawArgs)

	// Permission (also catches unknown tools).
	if msg := CheckToolPermission(name, info.Role); msg != "" {
		s.metric.ToolCalls.WithLabelValues(name, info.Role, "denied").Inc()
		if s.writeAudit(info, name, args, "denied", auditExtra{reason: msg}) != nil {
			return internalErrorJSON(name)
		}
		return permDeniedJSON(msg)
	}

	start := time.Now()
	resultJSON, panicked := s.dispatchRecover(name, args)
	durationMs := int(time.Since(start).Milliseconds())

	if panicked {
		s.metric.ToolCalls.WithLabelValues(name, info.Role, "error").Inc()
		s.metric.ToolDuration.WithLabelValues(name).Observe(float64(durationMs) / 1000)
		_ = s.writeAudit(info, name, args, "error", auditExtra{
			errorCode: "InternalError", errorMessage: "Unhandled exception in " + name, durationMs: &durationMs,
		})
		return internalErrorJSON(name)
	}

	status, errorCode, errorMessage, data := classifyResult(resultJSON)
	var output map[string]any
	if status == "ok" {
		output = s.buildOutput(name, args, data)
	}
	s.metric.ToolCalls.WithLabelValues(name, info.Role, status).Inc()
	s.metric.ToolDuration.WithLabelValues(name).Observe(float64(durationMs) / 1000)
	if s.writeAudit(info, name, args, status, auditExtra{
		errorCode: errorCode, errorMessage: errorMessage, durationMs: &durationMs, output: output,
	}) != nil {
		// SOC red line: an unauditable call must not be confirmed to the client.
		return internalErrorJSON(name)
	}
	return resultJSON
}

func (s *Server) dispatchRecover(name string, args map[string]any) (result string, panicked bool) {
	defer func() {
		if r := recover(); r != nil {
			log.Printf("panic in tool %s: %v", name, r)
			panicked = true
		}
	}()
	return Dispatch(s.deps, name, args), false
}

type auditExtra struct {
	reason       string
	errorCode    string
	errorMessage string
	durationMs   *int
	output       map[string]any
}

func (s *Server) writeAudit(info AuthInfo, tool string, args map[string]any, result string, ex auditExtra) error {
	err := s.audit.Log(audit.Entry{
		TS: time.Now().UTC().Format(time.RFC3339Nano), TokenName: info.TokenName,
		Role: info.Role, IP: info.IP, Tool: tool, Params: extractParams(args), Result: result,
		RequestID: info.RequestID, Reason: ex.reason, ErrorCode: ex.errorCode,
		ErrorMessage: ex.errorMessage, DurationMs: ex.durationMs, Output: ex.output,
	})
	if err != nil {
		s.metric.IncAuditFailure()
	}
	return err
}

// classifyResult ports the audit status extraction in call_tool.
func classifyResult(resultJSON string) (status, errorCode, errorMessage string, data map[string]any) {
	if err := json.Unmarshal([]byte(resultJSON), &data); err != nil || data == nil {
		return "ok", "", "", nil
	}
	if v, ok := data["success"]; ok {
		if b, isBool := v.(bool); isBool && !b {
			return "error", asString(data["error"]), asString(data["message"]), data
		}
	}
	if tv, ok := data["timed_out"].(bool); ok && tv {
		msg := asString(data["stderr"])
		if msg == "" {
			msg = "Command timed out"
		}
		return "error", "TimeoutError", msg, data
	}
	if ec, ok := data["exit_code"]; ok {
		if code, ok := toInt(ec); ok && code != 0 {
			stderr := asString(data["stderr"])
			msg := "Non-zero exit code"
			if stderr != "" {
				msg = truncateRunes(stderr, 200)
			}
			return "error", fmt.Sprintf("ExitCode:%d", code), msg, data
		}
	}
	return "ok", "", "", data
}

// buildOutput ports the per-tool audit `output` enrichment (ok status only).
func (s *Server) buildOutput(name string, args, data map[string]any) map[string]any {
	cfg := s.deps.Cfg
	switch name {
	case "bash_execute":
		out := audit.TruncateBashOutput([]byte(asString(data["stdout"])),
			cfg.AuditOutputBashHeadBytes, cfg.AuditOutputBashTailBytes)
		out["exit_code"] = data["exit_code"]
		tv, _ := data["timed_out"].(bool)
		out["timed_out"] = tv
		return out
	case "write_file":
		return audit.WriteFileOutput(asString(args["file_path"]), []byte(asString(args["content"])))
	case "edit_file":
		replacements := 0
		if r, ok := toInt(data["replacements"]); ok {
			replacements = r
		}
		oldS := asString(args["old_string"])
		newS := asString(args["new_string"])
		return audit.EditFileOutput(asString(args["file_path"]),
			strings.Count(newS, "\n")*replacements,
			strings.Count(oldS, "\n")*replacements,
			replacements)
	}
	return nil
}

// extractParams ports _extract_params: elide large content fields.
func extractParams(args map[string]any) map[string]any {
	omit := map[string]bool{"content": true, "old_string": true, "new_string": true}
	safe := make(map[string]any, len(args))
	for k, v := range args {
		if omit[k] {
			safe[k] = fmt.Sprintf("<%d chars>", pyLen(v))
		} else {
			safe[k] = v
		}
	}
	return safe
}

// Dispatch runs the tool and returns its JSON string. Argument defaulting
// mirrors the Python dispatch layer.
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
	case "bash_execute":
		timeout := 30
		if v, ok := argInt(args, "timeout"); ok {
			timeout = min(v, 600)
		}
		maxOut := d.Cfg.BashMaxOutputBytes
		if v, ok := argInt(args, "max_output_bytes"); ok {
			maxOut = min(v, d.Cfg.BashMaxOutputBytesHard)
		}
		result = tools.RunBash(d, argStr(args, "command", ""), timeout, argStr(args, "working_dir", "/"), maxOut)
	case "write_file":
		result = tools.WriteFile(d, argStr(args, "file_path", ""), argStr(args, "content", ""))
	case "edit_file":
		result = tools.EditFile(d, argStr(args, "file_path", ""),
			argStr(args, "old_string", ""), argStr(args, "new_string", ""), argBool(args, "replace_all"))
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

func textResult(s string) *mcp.CallToolResult {
	return &mcp.CallToolResult{Content: []mcp.Content{&mcp.TextContent{Text: s}}}
}

func permDeniedJSON(msg string) string {
	raw, _ := json.Marshal(map[string]any{"success": false, "error": "PermissionDenied", "message": msg})
	return string(raw)
}

func internalErrorJSON(name string) string {
	raw, _ := json.Marshal(map[string]any{
		"success": false, "error": "InternalError",
		"message": fmt.Sprintf("Tool '%s' failed with an unexpected error", name),
	})
	return string(raw)
}

func parseArgs(raw json.RawMessage) map[string]any {
	args := map[string]any{}
	if len(raw) > 0 {
		_ = json.Unmarshal(raw, &args)
	}
	return args
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

func asString(v any) string {
	s, _ := v.(string)
	return s
}

func toInt(v any) (int, bool) {
	switch n := v.(type) {
	case float64:
		return int(n), true
	case int:
		return n, true
	}
	return 0, false
}

func truncateRunes(s string, n int) string {
	if utf8.RuneCountInString(s) <= n {
		return s
	}
	r := []rune(s)
	return string(r[:n])
}

func pyLen(v any) int {
	if s, ok := v.(string); ok {
		return utf8.RuneCountInString(s)
	}
	return utf8.RuneCountInString(fmt.Sprint(v))
}
```

- [ ] **Step 3: Update `go/internal/mcpserver/mcpserver_test.go`**

The old tests used `BuildServer(d)` and expected 3 tools. Update: a `newServer(t)` helper that builds a `*Server` with a disabled audit writer and a real metrics instance; expect 6 tools; keep the permission/dispatch/in-process tests. Replace the file with:

```go
package mcpserver

import (
	"context"
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/modelcontextprotocol/go-sdk/mcp"

	"github.com/algony-tony/mymcp/go/internal/audit"
	"github.com/algony-tony/mymcp/go/internal/config"
	"github.com/algony-tony/mymcp/go/internal/metrics"
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

func newServer(t *testing.T) *Server {
	t.Helper()
	d := deps(t)
	a, err := audit.New(false, t.TempDir(), 1<<20, 5) // disabled: tests don't assert audit here
	if err != nil {
		t.Fatal(err)
	}
	m := metrics.New(func() float64 { return 0 })
	return New(d, a, m)
}

func TestCheckToolPermission(t *testing.T) {
	if got := CheckToolPermission("read_file", "ro"); got != "" {
		t.Fatalf("ro+read must pass: %q", got)
	}
	if got := CheckToolPermission("write_file", "rw"); got != "" {
		t.Fatalf("rw+write must pass: %q", got)
	}
	if got := CheckToolPermission("write_file", "ro"); got != "Permission denied: tool 'write_file' requires rw role" {
		t.Fatalf("ro+write: %q", got)
	}
	if got := CheckToolPermission("no_such_tool", "rw"); got != "Unknown tool: no_such_tool" {
		t.Fatalf("unknown: %q", got)
	}
}

func TestAuthInfoFromDefaultsToLeastPrivilege(t *testing.T) {
	if authInfoFrom(context.Background()).Role != "ro" {
		t.Fatal("missing auth info must default to ro")
	}
}

func TestToolNamesAreSix(t *testing.T) {
	names := ToolNames()
	if len(names) != 6 {
		t.Fatalf("expected 6 tools, got %d: %v", len(names), names)
	}
	want := map[string]bool{"read_file": true, "glob": true, "grep": true,
		"bash_execute": true, "write_file": true, "edit_file": true}
	for _, n := range names {
		delete(want, n)
	}
	if len(want) != 0 {
		t.Fatalf("missing tools: %v", want)
	}
	for _, td := range toolDefs {
		_ = mustSchema(td.SchemaJSON)
	}
}

func TestDispatchWriteThenEdit(t *testing.T) {
	d := deps(t)
	p := filepath.Join(t.TempDir(), "f.txt")
	out := Dispatch(d, "write_file", map[string]any{"file_path": p, "content": "a b a"})
	if !strings.Contains(out, `"success":true`) {
		t.Fatalf("write: %s", out)
	}
	out = Dispatch(d, "edit_file", map[string]any{"file_path": p, "old_string": "b", "new_string": "B"})
	if !strings.Contains(out, `"replacements":1`) {
		t.Fatalf("edit: %s", out)
	}
	got, _ := os.ReadFile(p)
	if string(got) != "a B a" {
		t.Fatalf("file = %q", got)
	}
}

func TestClassifyResult(t *testing.T) {
	cases := []struct {
		json, status, code string
	}{
		{`{"content":"x","total_lines":1}`, "ok", ""},
		{`{"success":true,"bytes_written":3}`, "ok", ""},
		{`{"success":false,"error":"ProtectedPath","message":"m"}`, "error", "ProtectedPath"},
		{`{"stdout":"","stderr":"Command timed out after 1s","exit_code":-1,"timed_out":true}`, "error", "TimeoutError"},
		{`{"stdout":"","stderr":"boom","exit_code":3,"timed_out":false}`, "error", "ExitCode:3"},
		{`{"stdout":"ok","stderr":"","exit_code":0,"timed_out":false}`, "ok", ""},
	}
	for _, c := range cases {
		status, code, _, _ := classifyResult(c.json)
		if status != c.status || code != c.code {
			t.Fatalf("classify(%s) = (%q,%q), want (%q,%q)", c.json, status, code, c.status, c.code)
		}
	}
}

func TestExtractParamsElidesContent(t *testing.T) {
	got := extractParams(map[string]any{"file_path": "/p", "content": "hello", "old_string": "ab"})
	if got["file_path"] != "/p" {
		t.Fatalf("file_path passed through wrong: %v", got["file_path"])
	}
	if got["content"] != "<5 chars>" || got["old_string"] != "<2 chars>" {
		t.Fatalf("elision wrong: %v", got)
	}
}

func textOf(t *testing.T, res *mcp.CallToolResult) string {
	t.Helper()
	tc, ok := res.Content[0].(*mcp.TextContent)
	if !ok {
		t.Fatalf("content is %T", res.Content[0])
	}
	return tc.Text
}

func TestBuildInProcessDeniedAndUnknown(t *testing.T) {
	s := newServer(t)
	srv := s.Build()
	ctx := context.Background()
	st, ct := mcp.NewInMemoryTransports()
	ss, err := srv.Connect(ctx, st, nil)
	if err != nil {
		t.Fatal(err)
	}
	defer ss.Close()
	client := mcp.NewClient(&mcp.Implementation{Name: "c", Version: "0"}, nil)
	cs, err := client.Connect(ctx, ct, nil)
	if err != nil {
		t.Fatal(err)
	}
	defer cs.Close()

	// Default (no auth info in context) is ro → write tool must be denied.
	res, err := cs.CallTool(ctx, &mcp.CallToolParams{Name: "write_file",
		Arguments: map[string]any{"file_path": "/tmp/x", "content": "y"}})
	if err != nil {
		t.Fatalf("call: %v", err)
	}
	var denied map[string]any
	_ = json.Unmarshal([]byte(textOf(t, res)), &denied)
	if denied["error"] != "PermissionDenied" ||
		denied["message"] != "Permission denied: tool 'write_file' requires rw role" {
		t.Fatalf("denied shape wrong: %v", denied)
	}

	// Unknown tool → PermissionDenied "Unknown tool" via the middleware.
	res, err = cs.CallTool(ctx, &mcp.CallToolParams{Name: "no_such", Arguments: map[string]any{}})
	if err != nil {
		t.Fatalf("unknown must not be a protocol error: %v", err)
	}
	var unk map[string]any
	_ = json.Unmarshal([]byte(textOf(t, res)), &unk)
	if unk["message"] != "Unknown tool: no_such" {
		t.Fatalf("unknown shape wrong: %v", unk)
	}
}
```

- [ ] **Step 4: Run to verify pass**

Run: `cd go && go test ./internal/mcpserver/ -v`
Expected: PASS (6 tools; denied + unknown flow through callTool).

- [ ] **Step 5: Commit**

```bash
gofmt -w go/internal/mcpserver/
git add go/internal/mcpserver/
git commit -m "feat(go/mcpserver): callTool choke point with audit + metrics; register write tools"
```

---

## Task 8: httpserver — /metrics, HTTP metrics, request-id, bash shutdown, audit lifecycle

**Files:**
- Modify: `go/internal/httpserver/httpserver.go`
- Test: `go/internal/httpserver/httpserver_test.go`

Port the `/metrics` endpoint (`src/mymcp/server.py:188-200`), the HTTP request counter (`MetricsMiddleware`), request-id generation (`RequestIdMiddleware`), and wire bash shutdown + audit close into `Serve`.

- [ ] **Step 1: Write the failing tests**

Replace `go/internal/httpserver/httpserver_test.go` with (keeping any M1 auth tests you find, adapted to the new `BuildMux` signature):

```go
package httpserver

import (
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"testing"

	"github.com/algony-tony/mymcp/go/internal/audit"
	"github.com/algony-tony/mymcp/go/internal/auth"
	"github.com/algony-tony/mymcp/go/internal/config"
	"github.com/algony-tony/mymcp/go/internal/metrics"
	"github.com/algony-tony/mymcp/go/internal/tools"
)

func testMux(t *testing.T, metricsToken string) http.Handler {
	t.Helper()
	t.Setenv("MYMCP_AUDIT_LOG_DIR", filepath.Join(t.TempDir(), "audit"))
	cfg, err := config.Load()
	if err != nil {
		t.Fatal(err)
	}
	tokFile := filepath.Join(t.TempDir(), "tokens.json")
	store, err := auth.NewTokenStore(tokFile, "admin-tok")
	if err != nil {
		t.Fatal(err)
	}
	a, _ := audit.New(false, t.TempDir(), 1<<20, 5)
	m := metrics.New(func() float64 { return 0 })
	d := tools.Deps{Cfg: cfg, Protected: tools.ProtectedFromConfig(cfg)}
	return BuildMux(d, store, a, m, metricsToken, "test")
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
	if rec.Code != 200 {
		t.Fatalf("health = %d", rec.Code)
	}
}

func TestMcpMissingBearer(t *testing.T) {
	mux := testMux(t, "")
	rec := httptest.NewRecorder()
	mux.ServeHTTP(rec, httptest.NewRequest("POST", "/mcp", nil))
	if rec.Code != 401 {
		t.Fatalf("status = %d, want 401", rec.Code)
	}
	body, _ := io.ReadAll(rec.Result().Body)
	if string(body) != `{"detail":"Missing Bearer token"}` {
		t.Fatalf("body = %q", body)
	}
}

func TestGenRequestID(t *testing.T) {
	a := genRequestID()
	b := genRequestID()
	if a == b || len(a) != 32 {
		t.Fatalf("bad request ids: %q %q", a, b)
	}
}

// keep the compiler honest about unused imports in trimmed test files
var _ = os.Getenv
```

- [ ] **Step 2: Run to verify failure**

Run: `cd go && go test ./internal/httpserver/ -v`
Expected: build failure (`BuildMux` old signature / `genRequestID` undefined).

- [ ] **Step 3: Rewrite `go/internal/httpserver/httpserver.go`**

```go
// Package httpserver assembles the HTTP surface: /mcp behind Bearer auth,
// /metrics behind the metrics token, /health, /version, an HTTP request counter,
// request-id propagation, and the serve loop with graceful shutdown that also
// tears down in-flight bash process groups and closes the audit writer.
package httpserver

import (
	"context"
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"net"
	"net/http"
	"os"
	"os/signal"
	"path/filepath"
	"strconv"
	"strings"
	"syscall"
	"time"

	"github.com/modelcontextprotocol/go-sdk/mcp"

	"github.com/algony-tony/mymcp/go/internal/audit"
	"github.com/algony-tony/mymcp/go/internal/auth"
	"github.com/algony-tony/mymcp/go/internal/config"
	"github.com/algony-tony/mymcp/go/internal/mcpserver"
	"github.com/algony-tony/mymcp/go/internal/metrics"
	"github.com/algony-tony/mymcp/go/internal/tools"
)

// BuildMux wires all routes and returns a handler wrapped with the HTTP request
// counter. version is passed in so tests don't depend on ldflags.
func BuildMux(d tools.Deps, store *auth.TokenStore, auditW *audit.Writer, m *metrics.Metrics, metricsToken, version string) http.Handler {
	mux := http.NewServeMux()

	srv := mcpserver.New(d, auditW, m).Build()
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
	mux.HandleFunc("GET /metrics", func(w http.ResponseWriter, r *http.Request) {
		if metricsToken == "" {
			writeJSON(w, 503, map[string]string{"detail": "Metrics disabled: MYMCP_METRICS_TOKEN not configured"})
			return
		}
		if r.Header.Get("Authorization") != "Bearer "+metricsToken {
			writeJSON(w, 401, map[string]string{"detail": "Unauthorized"})
			return
		}
		m.Handler().ServeHTTP(w, r)
	})

	return httpMetrics(m, mux)
}

type statusRecorder struct {
	http.ResponseWriter
	status int
}

func (s *statusRecorder) WriteHeader(code int) {
	s.status = code
	s.ResponseWriter.WriteHeader(code)
}

// httpMetrics records mymcp_http_requests_total{path,method,status}. The path
// label uses the matched route pattern (populated by ServeMux during
// next.ServeHTTP), or "<unmatched>" — bounded cardinality, matching
// src/mymcp/server.py:_path_label.
func httpMetrics(m *metrics.Metrics, next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		rec := &statusRecorder{ResponseWriter: w, status: 200}
		next.ServeHTTP(rec, r)
		path := r.Pattern
		if path == "" {
			path = "<unmatched>"
		}
		m.HTTPRequests.WithLabelValues(path, r.Method, strconv.Itoa(rec.status)).Inc()
	})
}

func authMiddleware(store *auth.TokenStore, next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		const prefix = "Bearer "
		authz := r.Header.Get("Authorization")
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
			TokenName: info.Name, Role: info.Role, IP: ip, RequestID: requestID(r),
		})
		next.ServeHTTP(w, r.WithContext(ctx))
	})
}

// requestID honours an inbound X-Request-ID (rejecting control chars) else
// generates one — parity with RequestIdMiddleware.
func requestID(r *http.Request) string {
	if rid := r.Header.Get("X-Request-ID"); rid != "" && !strings.ContainsAny(rid, "\r\n\x00") {
		return rid
	}
	return genRequestID()
}

func genRequestID() string {
	b := make([]byte, 16)
	_, _ = rand.Read(b)
	return hex.EncodeToString(b)
}

func writeJSON(w http.ResponseWriter, code int, body any) {
	raw, _ := json.Marshal(body)
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(code)
	w.Write(raw)
}

// NeedTempTokens ports the _maybe_set_temp_tokens decision.
func NeedTempTokens() bool {
	if config.DiscoveredEnvFile() != "" {
		return false
	}
	return os.Getenv("MYMCP_ADMIN_TOKEN") == ""
}

// Serve runs the server until SIGTERM/SIGINT, then kills in-flight bash process
// groups, shuts down gracefully, and flushes token store + audit writer.
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

	auditW, err := audit.New(cfg.AuditEnabled, cfg.AuditLogDir, cfg.AuditMaxBytes, cfg.AuditBackupCount)
	if err != nil {
		return err
	}
	m := metrics.New(func() float64 { return float64(tools.InflightCount()) })

	d := tools.Deps{Cfg: cfg, Protected: tools.ProtectedFromConfig(cfg)}
	host, port := cfg.Host, cfg.Port
	if hostFlag != "" {
		host = hostFlag
	}
	if portFlag != 0 {
		port = portFlag
	}
	server := &http.Server{
		Addr:              fmt.Sprintf("%s:%d", host, port),
		Handler:           BuildMux(d, store, auditW, m, cfg.MetricsToken, version),
		ReadHeaderTimeout: 10 * time.Second,
		IdleTimeout:       120 * time.Second,
	}

	errCh := make(chan error, 1)
	go func() { errCh <- server.ListenAndServe() }()
	fmt.Fprintf(os.Stderr, "[mymcp] serving on %s\n", server.Addr)

	sigCh := make(chan os.Signal, 1)
	signal.Notify(sigCh, syscall.SIGTERM, syscall.SIGINT)
	defer signal.Stop(sigCh)
	select {
	case err := <-errCh:
		return err
	case <-sigCh:
	}

	// TERM/grace/KILL in-flight bash process groups so their handlers unblock,
	// mirroring the Python CLI signal handler.
	tools.ShutdownInflight(cfg.ShutdownGraceSec)

	ctx, cancel := context.WithTimeout(context.Background(),
		time.Duration(cfg.ShutdownGraceSec)*time.Second)
	defer cancel()
	shutdownErr := server.Shutdown(ctx)
	if errors.Is(shutdownErr, context.DeadlineExceeded) {
		fmt.Fprintln(os.Stderr, "[mymcp] shutdown grace period exceeded; forcing exit")
		shutdownErr = nil
	}
	if err := store.Flush(); err != nil {
		fmt.Fprintf(os.Stderr, "[mymcp] token store flush failed: %v\n", err)
	}
	if err := auditW.Close(); err != nil {
		fmt.Fprintf(os.Stderr, "[mymcp] audit close failed: %v\n", err)
	}
	return shutdownErr
}
```

- [ ] **Step 4: Run to verify pass**

Run: `cd go && go test ./internal/httpserver/ -v && go vet ./... && test -z "$(gofmt -l .)"`
Expected: PASS, vet clean, gofmt clean.

- [ ] **Step 5: Full Go suite green**

Run: `cd go && go build ./... && go test ./...`
Expected: all packages PASS.

- [ ] **Step 6: Commit**

```bash
gofmt -w go/
git add go/internal/httpserver/
git commit -m "feat(go/httpserver): /metrics, HTTP counter, request-id, bash+audit shutdown"
```

---

## Task 9: Compatibility suite — write/edit/bash/metrics + audit acceptance

**Files:**
- Modify: `tests/compat/conftest.py`
- Modify: `tests/compat/test_tools_list.py`
- Create: `tests/compat/test_write_file.py`, `test_edit_file.py`, `test_bash.py`, `test_metrics.py`, `test_audit.py`

- [ ] **Step 1: Extend `conftest.py` with metrics token + audit dir**

Add near the other env reads (after `TMP = …`):

```python
METRICS_TOKEN = os.environ.get("MYMCP_COMPAT_METRICS_TOKEN", "")
AUDIT_DIR = os.environ.get("MYMCP_COMPAT_AUDIT_DIR", "")
```

and two fixtures after the `ro` fixture:

```python
@pytest.fixture
def metrics_token() -> str:
    if not METRICS_TOKEN:
        pytest.skip("MYMCP_COMPAT_METRICS_TOKEN not set")
    return METRICS_TOKEN


@pytest.fixture
def audit_dir() -> str:
    if not AUDIT_DIR:
        pytest.skip("MYMCP_COMPAT_AUDIT_DIR not set")
    return AUDIT_DIR
```

- [ ] **Step 2: Extend `test_tools_list.py` to cover the write tools**

Change the tuple and add a write-tool assertion:

```python
M1_TOOLS = ("read_file", "glob", "grep")
M2_WRITE_TOOLS = ("bash_execute", "write_file", "edit_file")


@pytest.mark.anyio
@pytest.mark.parametrize("name", M1_TOOLS + M2_WRITE_TOOLS)
async def test_tool_present_with_exact_schema(rw, name):
    tools = {t.name: t for t in await rw.list_tools()}
    assert name in tools, f"{name} missing from tools/list"
    golden = TOOL_DEFS[name]
    got = tools[name]
    assert got.description == golden.description
    assert got.inputSchema == golden.inputSchema


@pytest.mark.anyio
async def test_ro_token_cannot_see_write_tools(ro):
    names = {t.name for t in await ro.list_tools()}
    assert set(M1_TOOLS) <= names
    assert not (set(M2_WRITE_TOOLS) & names), "ro must not see write tools"
```

- [ ] **Step 3: Create `test_write_file.py`**

```python
import os

import pytest


@pytest.mark.anyio
async def test_write_creates_and_reports(rw, scratch):
    p = os.path.join(scratch, "w.txt")
    res = await rw.call("write_file", {"file_path": p, "content": "hello\nworld\n"})
    assert res["success"] is True
    assert res["bytes_written"] == 12
    with open(p) as f:
        assert f.read() == "hello\nworld\n"


@pytest.mark.anyio
async def test_write_protected_denied(rw):
    res = await rw.call("write_file", {"file_path": "/tmp/mymcp-compat-protected/x", "content": "no"})
    assert res["success"] is False
    assert res["error"] == "ProtectedPath"


@pytest.mark.anyio
async def test_ro_cannot_write(ro, scratch):
    res = await ro.call("write_file", {"file_path": os.path.join(scratch, "x"), "content": "y"})
    assert res == {
        "success": False,
        "error": "PermissionDenied",
        "message": "Permission denied: tool 'write_file' requires rw role",
    }
```

- [ ] **Step 4: Create `test_edit_file.py`**

```python
import os

import pytest


@pytest.mark.anyio
async def test_edit_single(rw, scratch):
    p = os.path.join(scratch, "e.txt")
    with open(p, "w") as f:
        f.write("alpha beta alpha")
    res = await rw.call("edit_file", {"file_path": p, "old_string": "beta", "new_string": "BETA"})
    assert res["success"] is True and res["replacements"] == 1
    with open(p) as f:
        assert f.read() == "alpha BETA alpha"


@pytest.mark.anyio
async def test_edit_ambiguous(rw, scratch):
    p = os.path.join(scratch, "e.txt")
    with open(p, "w") as f:
        f.write("x x x")
    res = await rw.call("edit_file", {"file_path": p, "old_string": "x", "new_string": "y"})
    assert res["success"] is False
    assert res["error"] == "AmbiguousMatch"
    assert res["message"].startswith("old_string appears 3 times")


@pytest.mark.anyio
async def test_edit_replace_all(rw, scratch):
    p = os.path.join(scratch, "e.txt")
    with open(p, "w") as f:
        f.write("x x x")
    res = await rw.call(
        "edit_file", {"file_path": p, "old_string": "x", "new_string": "y", "replace_all": True}
    )
    assert res["success"] is True and res["replacements"] == 3
```

- [ ] **Step 5: Create `test_bash.py`**

```python
import pytest


@pytest.mark.anyio
async def test_bash_basic(rw):
    res = await rw.call("bash_execute", {"command": "printf hi"})
    assert res["stdout"] == "hi"
    assert res["exit_code"] == 0
    assert res["timed_out"] is False


@pytest.mark.anyio
async def test_bash_nonzero_exit(rw):
    res = await rw.call("bash_execute", {"command": "exit 3"})
    assert res["exit_code"] == 3
    assert res["timed_out"] is False


@pytest.mark.anyio
async def test_bash_timeout(rw):
    res = await rw.call("bash_execute", {"command": "sleep 5", "timeout": 1})
    assert res["timed_out"] is True
    assert res["exit_code"] == -1
    assert res["stderr"] == "Command timed out after 1s"


@pytest.mark.anyio
async def test_bash_truncation(rw):
    res = await rw.call("bash_execute", {"command": "printf 'aaaaaaaaaa'", "max_output_bytes": 4})
    assert res["stdout"].startswith("aaaa\n[TRUNCATED: total 10 bytes, showing first 4 bytes]")


@pytest.mark.anyio
async def test_ro_cannot_bash(ro):
    res = await ro.call("bash_execute", {"command": "id"})
    assert res["error"] == "PermissionDenied"
```

- [ ] **Step 6: Create `test_metrics.py`**

```python
import os

import httpx
import pytest

BASE_URL = os.environ.get("MYMCP_COMPAT_URL", "http://127.0.0.1:8765")


def test_metrics_requires_token(metrics_token):
    r = httpx.get(f"{BASE_URL}/metrics")
    assert r.status_code == 401


def test_metrics_with_token_exposes_mymcp_names(metrics_token):
    r = httpx.get(f"{BASE_URL}/metrics", headers={"Authorization": f"Bearer {metrics_token}"})
    assert r.status_code == 200
    body = r.text
    assert "mymcp_tool_calls_total" in body
    assert "mymcp_http_requests_total" in body


@pytest.mark.anyio
async def test_tool_call_increments_counter(rw, metrics_token, scratch):
    await rw.call("write_file", {"file_path": os.path.join(scratch, "m.txt"), "content": "x"})
    r = httpx.get(f"{BASE_URL}/metrics", headers={"Authorization": f"Bearer {metrics_token}"})
    assert 'tool="write_file"' in r.text
```

- [ ] **Step 7: Create `test_audit.py` (M2 acceptance — real EventTailer)**

```python
"""M2 acceptance: the Python recorder EventTailer consumes the audit.log the
server writes (works against BOTH the Python and Go servers)."""

import os
import time
from pathlib import Path

import pytest

from mymcp.recorder.events import EventTailer

AUDIT_DIR = os.environ.get("MYMCP_COMPAT_AUDIT_DIR", "")
pytestmark = pytest.mark.skipif(not AUDIT_DIR, reason="MYMCP_COMPAT_AUDIT_DIR not set")


@pytest.mark.anyio
async def test_tailer_consumes_mutating_success_events(rw, scratch, tmp_path):
    u1 = os.path.join(scratch, "audit-target.txt")

    assert (await rw.call("write_file", {"file_path": u1, "content": "hello\nworld\n"}))["success"]
    assert (await rw.call("edit_file", {"file_path": u1, "old_string": "hello", "new_string": "HELLO"}))[
        "success"
    ]
    assert (await rw.call("bash_execute", {"command": "true"}))["exit_code"] == 0

    # Mutating but FAILED — must be filtered out by the tailer.
    assert (await rw.call("bash_execute", {"command": "exit 3"}))["exit_code"] == 3
    assert (await rw.call("write_file", {"file_path": "/tmp/mymcp-compat-protected/x", "content": "no"}))[
        "success"
    ] is False
    # Read-only — never mutating.
    await rw.call("read_file", {"file_path": u1})

    time.sleep(0.2)  # audit writes are synchronous, but be gentle on CI FS

    tailer = EventTailer(log_dir=Path(AUDIT_DIR), cursor_path=tmp_path / "cursor.json")
    events = list(tailer.read_new())

    def out(e):
        return e.output or {}

    assert [e for e in events if e.tool == "write_file" and out(e).get("path") == u1]
    assert [e for e in events if e.tool == "edit_file" and out(e).get("path") == u1]
    assert [e for e in events if e.tool == "bash_execute" and out(e).get("exit_code") == 0]

    # Failures + reads must never surface.
    assert not [e for e in events if e.tool == "bash_execute" and out(e).get("exit_code") == 3]
    assert not [
        e for e in events if e.tool == "write_file" and out(e).get("path") == "/tmp/mymcp-compat-protected/x"
    ]
    assert not [e for e in events if e.tool == "read_file"]
```

- [ ] **Step 8: Sanity-check against the Python server locally**

```bash
mkdir -p /tmp/mymcp-compat /tmp/mymcp-compat-protected /tmp/mymcp-compat-audit
cp tests/compat/ci-tokens.json /tmp/compat-tokens.json
MYMCP_ADMIN_TOKEN=compat-admin MYMCP_TOKEN_FILE=/tmp/compat-tokens.json \
MYMCP_PROTECTED_PATHS=/tmp/mymcp-compat-protected MYMCP_PORT=18770 \
MYMCP_METRICS_TOKEN=compat-metrics \
MYMCP_AUDIT_ENABLED=true MYMCP_AUDIT_LOG_DIR=/tmp/mymcp-compat-audit \
  mymcp serve &
sleep 1
MYMCP_COMPAT_URL=http://127.0.0.1:18770 \
MYMCP_COMPAT_RW_TOKEN=tok_compat_rw_0000000000000000 \
MYMCP_COMPAT_RO_TOKEN=tok_compat_ro_0000000000000000 \
MYMCP_COMPAT_METRICS_TOKEN=compat-metrics \
MYMCP_COMPAT_AUDIT_DIR=/tmp/mymcp-compat-audit \
  pytest tests/compat/ -v --benchmark-disable
kill %1
```

Expected: all compat tests PASS against the Python server (proves the suite itself is correct before pointing it at Go).

- [ ] **Step 9: Sanity-check against the Go server locally**

Same as Step 8 but build+boot the Go binary first (`cd go && go build -o /tmp/mymcp-go ./cmd/mymcp && cd ..`, then `/tmp/mymcp-go serve` with the same env, fresh `/tmp/mymcp-compat-audit`). Expected: all PASS.

- [ ] **Step 10: Commit**

```bash
git add tests/compat/
git commit -m "test(compat): write/edit/bash/metrics + EventTailer audit acceptance"
```

---

## Task 10: CI + CHANGELOG

**Files:**
- Modify: `.github/workflows/ci.yml`
- Modify: `CHANGELOG.md`

- [ ] **Step 1: Enable audit + metrics in both compat jobs**

In `.github/workflows/ci.yml`, `compat-python` job — replace the "seed tokens and boot server" and "run compat suite" steps with:

```yaml
      - name: seed tokens and boot server
        run: |
          mkdir -p /tmp/mymcp-compat /tmp/mymcp-compat-protected /tmp/mymcp-compat-audit
          cp tests/compat/ci-tokens.json /tmp/compat-tokens.json
          MYMCP_ADMIN_TOKEN=compat-admin MYMCP_TOKEN_FILE=/tmp/compat-tokens.json \
          MYMCP_PROTECTED_PATHS=/tmp/mymcp-compat-protected MYMCP_PORT=18770 \
          MYMCP_METRICS_TOKEN=compat-metrics \
          MYMCP_AUDIT_ENABLED=true MYMCP_AUDIT_LOG_DIR=/tmp/mymcp-compat-audit \
            mymcp serve &
          for i in $(seq 20); do curl -sf http://127.0.0.1:18770/health && break; sleep 0.5; done
      - name: run compat suite
        run: |
          MYMCP_COMPAT_URL=http://127.0.0.1:18770 \
          MYMCP_COMPAT_RW_TOKEN=tok_compat_rw_0000000000000000 \
          MYMCP_COMPAT_RO_TOKEN=tok_compat_ro_0000000000000000 \
          MYMCP_COMPAT_METRICS_TOKEN=compat-metrics \
          MYMCP_COMPAT_AUDIT_DIR=/tmp/mymcp-compat-audit \
            pytest tests/compat/ -v --benchmark-disable
```

In `compat-go` job — replace the "build and boot go server" and "run compat suite" steps with:

```yaml
      - name: build and boot go server
        run: |
          cd go && go build -o /tmp/mymcp-go ./cmd/mymcp && cd ..
          mkdir -p /tmp/mymcp-compat /tmp/mymcp-compat-protected /tmp/mymcp-compat-audit
          cp tests/compat/ci-tokens.json /tmp/compat-tokens.json
          MYMCP_ADMIN_TOKEN=compat-admin MYMCP_TOKEN_FILE=/tmp/compat-tokens.json \
          MYMCP_PROTECTED_PATHS=/tmp/mymcp-compat-protected MYMCP_PORT=18770 \
          MYMCP_METRICS_TOKEN=compat-metrics \
          MYMCP_AUDIT_ENABLED=true MYMCP_AUDIT_LOG_DIR=/tmp/mymcp-compat-audit \
            /tmp/mymcp-go serve &
          for i in $(seq 20); do curl -sf http://127.0.0.1:18770/health && break; sleep 0.5; done
      - name: run compat suite
        run: |
          MYMCP_COMPAT_URL=http://127.0.0.1:18770 \
          MYMCP_COMPAT_RW_TOKEN=tok_compat_rw_0000000000000000 \
          MYMCP_COMPAT_RO_TOKEN=tok_compat_ro_0000000000000000 \
          MYMCP_COMPAT_METRICS_TOKEN=compat-metrics \
          MYMCP_COMPAT_AUDIT_DIR=/tmp/mymcp-compat-audit \
            pytest tests/compat/ -v --benchmark-disable
```

> The existing `go` job already runs `go vet` + `go test ./...` + gofmt check; the new packages are covered automatically.

- [ ] **Step 2: CHANGELOG entry**

Under `## [Unreleased]` in `CHANGELOG.md`, add:

```markdown
### Added
- Go core M2: `bash_execute` (process-group cleanup, timeout, output truncation),
  `write_file`, and `edit_file` with protected-path enforcement.
- Go core audit writer: JSON-lines with `RotatingFileHandler`-compatible size
  rotation (`audit.log.N`), consumed unchanged by the Python recorder
  `EventTailer` (verified in CI).
- Go core native Prometheus `/metrics` (`mymcp_*` names identical to the Python
  core) behind `MYMCP_METRICS_TOKEN`.
- Compat suite: write/edit/bash/metrics coverage plus an EventTailer audit
  acceptance test, run against both the Python and Go servers in CI.
```

- [ ] **Step 3: Push and open the PR**

```bash
git add .github/workflows/ci.yml CHANGELOG.md
git commit -m "ci: enable audit + metrics in compat jobs; changelog for M2"
git push -u origin feat/go-core-m2
gh pr create --title "Go core M2 — full tool surface + audit + metrics" \
  --body "$(cat <<'EOF'
Implements milestone **M2** of the Go core rewrite (spec: docs/superpowers/specs/2026-07-04-go-core-rewrite-design.md).

- bash_execute (process groups), write_file, edit_file, protected paths
- audit writer (RotatingFileHandler-compatible, audit.log.N)
- native Prometheus /metrics with mymcp_* names
- full compat suite green against Python **and** Go (except transfer/admin, which are M3)
- M2 acceptance: the Python EventTailer consumes Go-written audit.log

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

- [ ] **Step 4: Confirm all CI checks green**

Wait for `go`, `compat-python`, `compat-go`, `test`, `lint`, `security-audit`, `build`, `mutation-smoke` to pass. The two compat jobs green = drop-in for the M2 surface is proven.

---

## Self-Review (run before requesting review)

**1. Spec coverage (M2 row: "bash_execute (process groups), write_file/edit_file, protected paths, audit writer, /metrics; acceptance: full compat green except transfer/admin + EventTailer consumes Go audit.log"):**

| M2 requirement | Task |
|---|---|
| bash_execute + process groups | Task 4 |
| write_file / edit_file | Tasks 5, 6 |
| protected paths (write mode) | Tasks 5, 6 (via `fsutil.CheckProtectedPath` ModeWrite) |
| audit writer | Task 2 (+ wiring Task 7) |
| /metrics | Tasks 3, 8 |
| full compat green except transfer/admin | Task 9 |
| EventTailer consumes Go audit.log | Task 9 (`test_audit.py`) |
| audit write failure → InternalError (SOC) | Task 7 (`writeAudit` → `internalErrorJSON`) |

**2. Placeholder scan:** every code step contains full source; no "TBD"/"add error handling"/"similar to". The one prose instruction (edit_file `AmbiguousMatch` correction, Task 6 Step 4) is explicit with the exact literal.

**3. Type consistency:** `audit.Writer.Log(Entry) error`, `audit.New(bool,string,int64,int)`, `metrics.New(func() float64) *Metrics`, `mcpserver.New(tools.Deps,*audit.Writer,*metrics.Metrics) *Server`, `Server.Build()`, `BuildMux(tools.Deps,*auth.TokenStore,*audit.Writer,*metrics.Metrics,string,string) http.Handler`, `tools.RunBash(Deps,string,int,string,int)`, `tools.WriteFile(Deps,string,string)`, `tools.EditFile(Deps,string,string,string,bool)`, `tools.InflightCount() int`, `tools.ShutdownInflight(int)` — all referenced consistently across tasks. `AuthInfo` gains `RequestID` (Task 7) set by `authMiddleware` (Task 8).

**4. Parity edge cases** are enumerated in "Known, Documented Divergences" and cross-referenced from the code comments; none affect a compat assertion.

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-07-04-go-core-m2-tools-safety.md`. Two execution options:

**1. Subagent-Driven (recommended)** — fresh subagent per task, two-stage review (spec compliance then code quality) between tasks. Matches how M1 shipped.

**2. Inline Execution** — execute tasks in this session with checkpoints for review.

Which approach?
