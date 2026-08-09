# Issue #92 Recorder Visibility Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make a stopped overview recorder impossible to miss — fix the v2 → v3 upgrade path that drops it, and surface staleness in `server_overview` instead of serving month-old state as current fact.

**Architecture:** Two PRs off `master`. PR-A is documentation plus two constant-level changes with no new logic. PR-B adds a disk-derived freshness check to the Go core (`overview.md`'s `_Last updated:_` header + `cursor.json` offset scanned against `audit.log`), adaptive batch shrinking in the Python merge cycle, and a `--install-unit` helper for the sidecar systemd unit.

**Tech Stack:** Go 1.x (`go/`, module `github.com/algony-tony/mymcp/go`, stdlib only for this work), Python 3.11+ (`src/mymcp`, recorder sidecar), pytest + anyio, `go test`.

**Spec:** `docs/superpowers/specs/2026-08-09-issue-92-recorder-visibility-design.md`

## Global Constraints

- `master` is protected. Every change lands via a branch + PR; never commit to `master` directly.
- The `error` code `RecorderDisabled` returned by `server_overview` MUST NOT change — `tests/compat/test_server_overview.py:10` asserts it. Only the `message` may change.
- The Go core is stdlib-only for this work. Do not add Go dependencies.
- The Python package's base install has **zero dependencies**. Recorder code may import `httpx` etc., but nothing on the base import path may.
- Go gates: `cd go && go test ./... && go vet ./... && gofmt -l .` (`gofmt -l .` must print nothing).
- Python gates: `pytest tests/ -v --benchmark-disable`, `ruff check . && ruff format --check . && mypy src/mymcp`.
- `MUTATING_TOOLS` in Go must stay a faithful port of `src/mymcp/recorder/events.py:34-43` — six entries, **not** `mcpserver.writeTools`' four.
- Timestamps in prose/docs are absolute dates, not relative.

---

# PR-A — Stop the bleeding

Branch: `fix/issue-92-recorder-visibility` (already exists, already carries the spec commit). Check it out before Task 1.

### Task 1: Differentiate the `server_overview` failure message

Today every read failure — sidecar never installed, sidecar crashed before first write, wrong `recorder_data_dir`, permission denied — returns *"server_overview requires `MYMCP_RECORDER_ENABLED=true`"*. On the host in issue #92 that flag was correct the entire time, so the message sent the operator to a dead end.

**Files:**
- Modify: `go/internal/tools/overview.go:14-24`
- Test: `go/internal/tools/overview_test.go`

**Interfaces:**
- Consumes: `tools.Deps` (`go/internal/tools/readfile.go:23`), `testDeps(t)` helper (already in the `tools` package tests).
- Produces: `ServerOverview(d Deps) map[string]any` — unchanged signature. Result keys on failure: `success` (false), `error` (always the string `RecorderDisabled`), `message`.

- [ ] **Step 1: Write the failing tests**

Replace `TestServerOverviewAbsentReturnsRecorderDisabled` in `go/internal/tools/overview_test.go` with these two, keeping `TestServerOverviewPresentReturnsContent` as-is:

```go
func TestServerOverviewAbsentPointsAtSidecar(t *testing.T) {
	d := testDeps(t)
	d.Cfg.RecorderDataDir = t.TempDir() // no overview/overview.md
	res := ServerOverview(d)
	if res["success"] != false || res["error"] != "RecorderDisabled" {
		t.Fatalf("res = %v", res)
	}
	msg, _ := res["message"].(string)
	if !strings.Contains(msg, "systemctl status mymcp-recorder") {
		t.Fatalf("message should name the sidecar, got %q", msg)
	}
	if strings.Contains(msg, "MYMCP_RECORDER_ENABLED") {
		t.Fatalf("absent-file message must not blame the config flag, got %q", msg)
	}
}

func TestServerOverviewUnreadableReportsReadFailure(t *testing.T) {
	if os.Geteuid() == 0 {
		t.Skip("root bypasses file permissions")
	}
	dir := t.TempDir()
	ov := filepath.Join(dir, "overview")
	if err := os.MkdirAll(ov, 0o755); err != nil {
		t.Fatal(err)
	}
	path := filepath.Join(ov, "overview.md")
	if err := os.WriteFile(path, []byte("# Server\n"), 0o000); err != nil {
		t.Fatal(err)
	}
	d := testDeps(t)
	d.Cfg.RecorderDataDir = dir
	res := ServerOverview(d)
	if res["success"] != false || res["error"] != "RecorderDisabled" {
		t.Fatalf("res = %v", res)
	}
	msg, _ := res["message"].(string)
	if !strings.Contains(msg, path) {
		t.Fatalf("unreadable message should name the path, got %q", msg)
	}
	if strings.Contains(msg, "systemctl") {
		t.Fatalf("unreadable message must not claim the sidecar never ran, got %q", msg)
	}
}
```

Add `"strings"` to the test file's imports (`os` and `path/filepath` are already there).

- [ ] **Step 2: Run the tests to verify they fail**

```bash
cd go && go test ./internal/tools/ -run 'TestServerOverview' -v
```

Expected: `TestServerOverviewAbsentPointsAtSidecar` FAILs with `message should name the sidecar` (it currently says `server_overview requires MYMCP_RECORDER_ENABLED=true`), and `TestServerOverviewUnreadableReportsReadFailure` FAILs on the path assertion.

- [ ] **Step 3: Write the implementation**

Replace the body of `ServerOverview` in `go/internal/tools/overview.go`:

```go
func ServerOverview(d Deps) map[string]any {
	path := filepath.Join(d.Cfg.RecorderDataDir, "overview", "overview.md")
	raw, err := os.ReadFile(path)
	if err != nil {
		// The error code stays RecorderDisabled for compat (the Python core's
		// shape); only the message distinguishes the causes. "File absent" is
		// overwhelmingly "the sidecar was never started" — issue #92 — so the
		// message names the sidecar rather than a config flag the Go core does
		// not gate on.
		msg := fmt.Sprintf("overview not generated yet at %s — is the recorder "+
			"sidecar running? Check: systemctl status mymcp-recorder", path)
		if !os.IsNotExist(err) {
			msg = fmt.Sprintf("could not read overview at %s: %v", path, err)
		}
		return map[string]any{"success": false, "error": "RecorderDisabled", "message": msg}
	}
	return map[string]any{"success": true, "overview": fsutil.DecodeReplace(raw)}
}
```

Add `"fmt"` to the imports in `overview.go`.

- [ ] **Step 4: Run the tests to verify they pass**

```bash
cd go && go test ./internal/tools/ -run 'TestServerOverview' -v && go vet ./... && gofmt -l .
```

Expected: both tests PASS, `go vet` silent, `gofmt -l .` prints nothing.

- [ ] **Step 5: Commit**

```bash
git add go/internal/tools/overview.go go/internal/tools/overview_test.go
git commit -m "fix(overview): name the sidecar, not the config flag, on read failure

Every read failure returned 'server_overview requires
MYMCP_RECORDER_ENABLED=true'. On the host in #92 that flag was correct
the whole time, so the message dead-ended the operator. Split absent
(point at systemctl status mymcp-recorder) from unreadable (report the
path and the underlying error). The RecorderDisabled error code is
unchanged — tests/compat/test_server_overview.py pins it.

Refs #92"
```

---

### Task 2: Raise the default recorder `max_tokens`

On reasoning models, thinking tokens count toward `completion_tokens`. Probing DeepSeek `deepseek-v4-flash` with a trivial prompt returned `{"completion_tokens": 54, "completion_tokens_details": {"reasoning_tokens": 40}}` — 74% of the budget spent reasoning before answering. The 16384 default truncates real merges on such models.

**Files:**
- Modify: `src/mymcp/config.py:94-99`
- Modify: `README.md` (recorder rows of the configuration table)
- Test: `tests/test_config_settings.py`

**Interfaces:**
- Consumes: `mymcp.config.get_settings()`, `mymcp.config.reset_settings_cache()`.
- Produces: `Settings.recorder_llm_max_tokens` default `32768` (was `16384`). `MergeCycle` and `BootstrapAgent` keep their own `max_tokens: int = 16384` parameter defaults — those are only used when constructed without wiring, and Task 8 does not change them.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_config_settings.py`:

```python
def test_recorder_llm_max_tokens_default_accommodates_reasoning_models():
    """Reasoning models bill thinking tokens against the output budget.

    16384 truncated real merges on deepseek-v4-flash (issue #92 item 5).
    """
    from mymcp.config import get_settings, reset_settings_cache

    reset_settings_cache()
    assert get_settings().recorder_llm_max_tokens == 32768
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
pytest tests/test_config_settings.py::test_recorder_llm_max_tokens_default_accommodates_reasoning_models -v
```

Expected: FAIL — `assert 16384 == 32768`.

- [ ] **Step 3: Change the default and its comment**

In `src/mymcp/config.py`, replace the comment block and field at lines 94-99:

```python
    # Per-call output ceiling for the recorder's LLM. Must stay ≤ the chosen
    # model's max output (Claude Haiku/Sonnet 4.6: 64k, Opus 4.8: 128k,
    # GPT-5.x: 128k, DeepSeek v4: 384k); the API rejects values above the
    # model's limit.
    #
    # On reasoning models the thinking tokens are billed against this same
    # output budget — a trivial deepseek-v4-flash call spent 40 of 54
    # completion tokens on reasoning — so a merge folding a large backlog
    # truncates well before it finishes answering. 32768 clears that on every
    # provider listed above; deployments on small-output models must lower it.
    recorder_llm_max_tokens: int = Field(default=32768)
```

- [ ] **Step 4: Run the test to verify it passes**

```bash
pytest tests/test_config_settings.py -v
```

Expected: PASS, and no other config test regresses.

- [ ] **Step 5: Document the interaction in the README**

In `README.md`'s configuration table, change the `MYMCP_RECORDER_LLM_MAX_TOKENS` row's default to `32768` and its description to:

```
Per-call output ceiling for the recorder's LLM. Must stay under your model's output limit. On reasoning models, thinking tokens count against this budget too — raise it rather than lowering `MYMCP_RECORDER_MAX_EVENTS_PER_CYCLE` if merges report `max_tokens`.
```

If the table has no such row, add it directly after `MYMCP_RECORDER_LLM_BASE_URL`.

- [ ] **Step 6: Run the Python gates and commit**

```bash
pytest tests/ --benchmark-disable -q && ruff check . && ruff format --check . && mypy src/mymcp
git add src/mymcp/config.py tests/test_config_settings.py README.md
git commit -m "fix(recorder): default max_tokens 16384 -> 32768 for reasoning models

Reasoning models bill thinking tokens against completion_tokens; a
trivial deepseek-v4-flash call spent 40 of 54 output tokens reasoning.
Merges folding a backlog truncated before answering.

Refs #92"
```

---

### Task 3: Document the v2 → v3 upgrade (and fix two doc errors it exposed)

`README.md`'s Upgrade section is two lines, and the paragraph above it — *"already have this unit"*, *"keeps it"*, *"unchanged"* — actively communicates that nothing else is required. This is exactly what was run on the affected host.

**Files:**
- Modify: `README.md:77-86` (the "Existing v2 deployments…" paragraph and the `### Upgrade` section)
- Modify: `README.md:224` (the `MYMCP_RECORDER_ENABLED` table row)
- Modify: `docs/superpowers/plans/2026-07-12-go-core-m3b-p3b-v3-cutover.md:528`

**Interfaces:**
- Consumes: nothing. Documentation only.
- Produces: nothing code-level. Task 9 later replaces the inlined unit text with a pointer to `mymcp-recorder --install-unit`.

- [ ] **Step 1: Amend the "Existing v2 deployments" paragraph**

It currently ends with "…Install `ripgrep` separately for fast file search." Add a sentence before that:

```
That covers the main service only. A v2 deployment that ran the overview
recorder needs a **second** unit as well — see [From v2.x](#from-v2x) below;
without it the recorder silently stops and `server_overview` keeps serving a
frozen overview.
```

- [ ] **Step 2: Add the `From v2.x` subsection**

Directly under `### Upgrade`, after the existing two-line block, add:

````markdown
#### From v2.x

v3 split the overview recorder into a separate `mymcp-recorder` process. A
`pipx upgrade` does **not** create its unit, so a v2 deployment that had the
recorder enabled loses it silently. If `MYMCP_RECORDER_ENABLED=true` in your
`.env`, do this as well:

```bash
# 1. Recorder dependencies (v2 had them as base deps; v3 does not)
pipx inject algony-mymcp "algony-mymcp[recorder]"

# 2. Sidecar unit
sudo tee /etc/systemd/system/mymcp-recorder.service >/dev/null <<'UNIT'
[Unit]
Description=MyMCP Recorder (overview sidecar)
After=network.target mymcp.service
Wants=mymcp.service

[Service]
Type=simple
User=mymcp
WorkingDirectory=/etc/mymcp
EnvironmentFile=/etc/mymcp/.env
ExecStart=/usr/local/bin/mymcp-recorder
Restart=on-failure
RestartSec=10
NoNewPrivileges=true

[Install]
WantedBy=multi-user.target
UNIT

# 3. Start it
sudo systemctl daemon-reload
sudo systemctl enable --now mymcp-recorder
```

Adjust `User`, `EnvironmentFile`, and `ExecStart` to your install
(`which mymcp-recorder` gives the last one).

**Verify it is actually consuming events** — do not skip this; the failure mode
this guards against went unnoticed for four weeks on a production host:

```bash
systemctl is-active mymcp-recorder                      # -> active
# offset must advance within one merge interval (default 300s):
cat /var/lib/mymcp/recorder/cursor.json; sleep 310; cat /var/lib/mymcp/recorder/cursor.json
stat -c '%y %n' /var/lib/mymcp/recorder/overview/overview.md
```

If `offset` does not move while `/var/log/mymcp/audit.log` is growing, check
`journalctl -u mymcp-recorder -n 50`.
````

- [ ] **Step 3: Fix the `MYMCP_RECORDER_ENABLED` row**

`README.md:224` claims the Go `server_overview` "works regardless, returning a
stub until an overview exists". It does not — `go/internal/tools/overview.go`
returns `success: false`. The stub is v2 Python behaviour that the port did not
carry over. Replace the description with:

```
Let the `mymcp-recorder` sidecar start. The Go `server_overview` tool does not read this flag — it reports `RecorderDisabled` whenever `overview.md` is absent, whatever the flag says.
```

- [ ] **Step 4: Fix the stale template path**

In `docs/superpowers/plans/2026-07-12-go-core-m3b-p3b-v3-cutover.md:528`, change
`mymcp/deploy/templates/` to `src/mymcp/recorder/templates/`.

- [ ] **Step 5: Verify the instructions are literally correct**

The README's unit block must not drift from the shipped template. Render the
template with the README's values and diff the two directive lists:

```bash
python - <<'PY'
import re, pathlib
tpl = pathlib.Path("src/mymcp/recorder/templates/mymcp-recorder.service.in").read_text()
rendered = tpl.format(
    service_user="mymcp",
    working_directory="/etc/mymcp",
    env_file="/etc/mymcp/.env",
    exec_start="/usr/local/bin/mymcp-recorder",
)
readme = pathlib.Path("README.md").read_text()
block = re.search(r"\[Unit\].*?WantedBy=multi-user\.target", readme, re.S).group(0)
directives = lambda t: [l.strip() for l in t.splitlines()
                        if l.strip() and not l.strip().startswith("#")]
missing = [d for d in directives(rendered) if d not in directives(block)]
print("MISSING FROM README:", missing or "none")
PY
```

Expected: `MISSING FROM README: none`. Then confirm the in-page link resolves —
the `#from-v2x` anchor must match GitHub's slug for the `#### From v2.x`
heading (lowercase, spaces to hyphens, dots dropped).

- [ ] **Step 6: Commit and open the PR**

```bash
git add README.md docs/superpowers/plans/2026-07-12-go-core-m3b-p3b-v3-cutover.md
git commit -m "docs: cover the recorder sidecar in the v2 -> v3 upgrade path

The Upgrade section was two lines and the paragraph above it said the
existing unit 'keeps' working 'unchanged', so a v2 user with the
recorder enabled silently loses it. Adds a From v2.x subsection with the
[recorder] extra, the sidecar unit, and a verification step; fixes the
config table's claim that server_overview returns a stub (it returns
RecorderDisabled) and a stale template path in the cutover plan.

Refs #92"
git push
gh pr create --title "fix(#92): stop the bleeding — recorder upgrade docs, honest failure message, reasoning-model max_tokens" --body "$(cat <<'BODY'
PR-A of two for #92. Documentation plus two constant-level changes; no new logic.

- README gains a `From v2.x` upgrade subsection (the `[recorder]` extra + the
  sidecar unit + a verification step). Omitting this is what silently killed the
  recorder for four weeks on a production host.
- `server_overview` now distinguishes "overview absent" (points at
  `systemctl status mymcp-recorder`) from "unreadable" (reports path + error).
  The `RecorderDisabled` error code is unchanged — compat pins it.
- `recorder_llm_max_tokens` defaults to 32768: reasoning models bill thinking
  tokens against the output budget.
- Fixes the config table's claim that `server_overview` returns a stub, and a
  stale template path in the cutover plan.

PR-B follows with the staleness signal, adaptive batch shrinking, and
`mymcp-recorder --install-unit`.

Spec: `docs/superpowers/specs/2026-08-09-issue-92-recorder-visibility-design.md`

Refs #92
BODY
)"
```

---

# PR-B — Hardening

Branch off `master` (not off PR-A) so the two review independently:

```bash
git checkout master && git pull && git checkout -b feat/issue-92-recorder-staleness
```

If PR-A has not merged yet, branch off `fix/issue-92-recorder-visibility` instead and note the dependency in the PR body.

### Task 4: Expose the merge interval to the Go config

The staleness threshold is `2 ×` the recorder's merge interval. Both processes read the same `.env`, so Go reads the same variable rather than inventing a second knob.

**Files:**
- Modify: `go/internal/config/config.go` (struct field near `RecorderDataDir` at line 51; parse near line 155)
- Test: `go/internal/config/config_test.go`

**Interfaces:**
- Consumes: the `getStr`/`get` helpers already in `config.go`.
- Produces: `Config.RecorderMergeIntervalSec int` — default `300`, from `MYMCP_RECORDER_MERGE_INTERVAL_SEC`. Values `<= 0` fall back to `300`. Tasks 6 and 7 consume it.

- [ ] **Step 1: Write the failing test**

Append to `go/internal/config/config_test.go`:

```go
func TestRecorderMergeIntervalSec(t *testing.T) {
	cases := []struct {
		name string
		env  string
		want int
	}{
		{"default", "", 300},
		{"override", "60", 60},
		{"zero falls back", "0", 300},
		{"negative falls back", "-5", 300},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			t.Setenv("MYMCP_RECORDER_MERGE_INTERVAL_SEC", tc.env)
			cfg, err := Load()
			if err != nil {
				t.Fatal(err)
			}
			if cfg.RecorderMergeIntervalSec != tc.want {
				t.Fatalf("got %d, want %d", cfg.RecorderMergeIntervalSec, tc.want)
			}
		})
	}
}
```

`Load()` takes no arguments and returns `(*Config, error)` — match the existing
tests in that file (`config_test.go:11`). A non-numeric value is deliberately not
in the table: `getInt` returns an error for it, and the surrounding code's
convention is to surface that from `Load()` rather than silently defaulting.

- [ ] **Step 2: Run the test to verify it fails**

```bash
cd go && go test ./internal/config/ -run TestRecorderMergeIntervalSec -v
```

Expected: compile error — `cfg.RecorderMergeIntervalSec undefined`.

- [ ] **Step 3: Add the field and parse it**

Next to `RecorderDataDir` in the `Config` struct:

```go
	// RecorderMergeIntervalSec mirrors the sidecar's
	// MYMCP_RECORDER_MERGE_INTERVAL_SEC (both processes read the same .env).
	// The core uses it only to derive the overview staleness threshold.
	RecorderMergeIntervalSec int
```

Next to the `RecorderDataDir` parse line. `getInt` (`config.go:232`) has
signature `getInt(get getter, key string, def int) (int, error)`; follow the
surrounding code's convention for propagating that error out of `Load()`:

```go
	if cfg.RecorderMergeIntervalSec, err = getInt(get, "MYMCP_RECORDER_MERGE_INTERVAL_SEC", 300); err != nil {
		return nil, err
	}
	if cfg.RecorderMergeIntervalSec <= 0 {
		// A non-positive interval would make the staleness threshold 0 and flag
		// every overview; fall back rather than fail the whole server start.
		cfg.RecorderMergeIntervalSec = 300
	}
```

Read the neighbouring `getInt` call sites first and copy their exact
error-handling shape — if they use `var err error` differently, match that.

- [ ] **Step 4: Run the tests to verify they pass**

```bash
cd go && go test ./internal/config/ -v && go vet ./... && gofmt -l .
```

Expected: PASS, vet silent, gofmt prints nothing.

- [ ] **Step 5: Commit**

```bash
git add go/internal/config/
git commit -m "feat(config): read MYMCP_RECORDER_MERGE_INTERVAL_SEC in the Go core

The overview staleness threshold is 2x the merge interval. Both
processes read the same .env, so the core reads the same variable rather
than adding a second knob that could disagree.

Refs #92"
```

---

### Task 5: Count the unconsumed backlog from `cursor.json`

The core derives the backlog the same way the sidecar's `pending_count` does: read the persisted offset, scan `audit.log` from there, count events that are both mutating and successful.

**Files:**
- Create: `go/internal/tools/recorderstatus.go`
- Create: `go/internal/tools/recorderstatus_test.go`

**Interfaces:**
- Consumes: `Deps.Cfg.RecorderDataDir`, `Deps.Cfg.AuditLogDir` (`go/internal/config/config.go:36`).
- Produces:
  - `func pendingEvents(cfg *config.Config) int` — unconsumed mutating+successful events; `0` on any error or missing file.
  - `var mutatingTools map[string]bool` — the six-entry port of Python's `MUTATING_TOOLS`.
  - Task 6 adds `lastUpdated` and `RecorderStatus` to this same file.

- [ ] **Step 1: Write the failing tests**

Create `go/internal/tools/recorderstatus_test.go`:

```go
package tools

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"syscall"
	"testing"
)

// writeAudit writes JSON-lines to <dir>/audit.log and returns its size.
func writeAudit(t *testing.T, dir string, lines ...string) int64 {
	t.Helper()
	path := filepath.Join(dir, "audit.log")
	f, err := os.OpenFile(path, os.O_CREATE|os.O_WRONLY|os.O_APPEND, 0o644)
	if err != nil {
		t.Fatal(err)
	}
	defer f.Close()
	for _, l := range lines {
		if _, err := fmt.Fprintln(f, l); err != nil {
			t.Fatal(err)
		}
	}
	st, err := os.Stat(path)
	if err != nil {
		t.Fatal(err)
	}
	return st.Size()
}

func auditLine(tool, result string) string {
	b, _ := json.Marshal(map[string]any{
		"ts": "2026-08-09T00:00:00Z", "token_name": "t", "role": "rw",
		"ip": "127.0.0.1", "tool": tool, "params": map[string]any{}, "result": result,
	})
	return string(b)
}

// writeCursor writes cursor.json pointing at audit.log's current inode.
func writeCursor(t *testing.T, dataDir, logDir string, offset int64) {
	t.Helper()
	var ino uint64
	if st, err := os.Stat(filepath.Join(logDir, "audit.log")); err == nil {
		if sys, ok := st.Sys().(*syscall.Stat_t); ok {
			ino = sys.Ino
		}
	}
	b, _ := json.Marshal(map[string]any{"file": "audit.log", "inode": ino, "offset": offset})
	if err := os.WriteFile(filepath.Join(dataDir, "cursor.json"), b, 0o644); err != nil {
		t.Fatal(err)
	}
}

func TestPendingEventsCountsMutatingSuccessfulOnly(t *testing.T) {
	d := testDeps(t)
	dataDir, logDir := t.TempDir(), t.TempDir()
	d.Cfg.RecorderDataDir, d.Cfg.AuditLogDir = dataDir, logDir

	consumed := writeAudit(t, logDir, auditLine("write_file", "ok"))
	writeCursor(t, dataDir, logDir, consumed)
	writeAudit(t, logDir,
		auditLine("bash_execute", "ok"),      // counts
		auditLine("read_file", "ok"),         // read-only: ignored
		auditLine("write_file", "denied"),    // not successful: ignored
		auditLine("prepare_download", "ok"),  // in MUTATING_TOOLS, not in writeTools
		auditLine("transfer_upload", "ok"),   // endpoint audit name, not an MCP tool
		"not json at all",                    // corrupt: skipped
		"[1,2,3]",                            // valid JSON, not an object: skipped
		"",                                   // blank: skipped
	)

	if got := pendingEvents(d.Cfg); got != 3 {
		t.Fatalf("pendingEvents = %d, want 3", got)
	}
}

func TestPendingEventsNoCursorCountsFromHead(t *testing.T) {
	d := testDeps(t)
	dataDir, logDir := t.TempDir(), t.TempDir()
	d.Cfg.RecorderDataDir, d.Cfg.AuditLogDir = dataDir, logDir
	writeAudit(t, logDir, auditLine("write_file", "ok"), auditLine("edit_file", "ok"))

	// No cursor.json: the sidecar has never committed, so everything is pending.
	if got := pendingEvents(d.Cfg); got != 2 {
		t.Fatalf("pendingEvents = %d, want 2", got)
	}
}

func TestPendingEventsCaughtUpIsZero(t *testing.T) {
	d := testDeps(t)
	dataDir, logDir := t.TempDir(), t.TempDir()
	d.Cfg.RecorderDataDir, d.Cfg.AuditLogDir = dataDir, logDir
	size := writeAudit(t, logDir, auditLine("write_file", "ok"))
	writeCursor(t, dataDir, logDir, size)

	if got := pendingEvents(d.Cfg); got != 0 {
		t.Fatalf("pendingEvents = %d, want 0", got)
	}
}

func TestPendingEventsRotatedLogCountsBothFiles(t *testing.T) {
	d := testDeps(t)
	dataDir, logDir := t.TempDir(), t.TempDir()
	d.Cfg.RecorderDataDir, d.Cfg.AuditLogDir = dataDir, logDir

	// Old file: one consumed event, then one the cursor never reached.
	consumed := writeAudit(t, logDir, auditLine("write_file", "ok"))
	writeCursor(t, dataDir, logDir, consumed)
	writeAudit(t, logDir, auditLine("edit_file", "ok"))

	// Rotate: audit.log -> audit.log.1, fresh audit.log with a new inode.
	if err := os.Rename(filepath.Join(logDir, "audit.log"), filepath.Join(logDir, "audit.log.1")); err != nil {
		t.Fatal(err)
	}
	writeAudit(t, logDir, auditLine("bash_execute", "ok"))

	// 1 from the tail of audit.log.1 + 1 from the head of the new audit.log.
	if got := pendingEvents(d.Cfg); got != 2 {
		t.Fatalf("pendingEvents = %d, want 2", got)
	}
}

func TestPendingEventsMissingLogIsZero(t *testing.T) {
	d := testDeps(t)
	d.Cfg.RecorderDataDir, d.Cfg.AuditLogDir = t.TempDir(), t.TempDir()
	if got := pendingEvents(d.Cfg); got != 0 {
		t.Fatalf("pendingEvents = %d, want 0", got)
	}
}
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
cd go && go test ./internal/tools/ -run TestPendingEvents -v
```

Expected: compile error — `undefined: pendingEvents`.

- [ ] **Step 3: Write the implementation**

Create `go/internal/tools/recorderstatus.go`:

```go
package tools

import (
	"bufio"
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"syscall"

	"github.com/algony-tony/mymcp/go/internal/config"
)

// mutatingTools is a port of MUTATING_TOOLS in src/mymcp/recorder/events.py.
//
// It is deliberately NOT mcpserver.writeTools. The recorder's set has two
// entries the permission model does not: prepare_download (classified
// read-only for auth, but it still hands out host bytes) and transfer_upload
// (the *endpoint* audit name for a redeemed upload ticket, which is not an MCP
// tool and so can never appear in writeTools). Counting with writeTools would
// report a backlog different from the one the sidecar actually drains.
//
// Keep in sync with events.py; a tool added there but not here silently
// under-counts the backlog.
var mutatingTools = map[string]bool{
	"bash_execute":     true,
	"write_file":       true,
	"edit_file":        true,
	"prepare_upload":   true,
	"prepare_download": true,
	"transfer_upload":  true,
}

// successResults mirrors _SUCCESS_RESULTS in events.py: the core writes "ok",
// and "success" is tolerated for forward-compat.
var successResults = map[string]bool{"ok": true, "success": true}

// recorderCursor is the on-disk shape of <recorder_data_dir>/cursor.json,
// written by src/mymcp/recorder/cursor.py.
type recorderCursor struct {
	File   string `json:"file"`
	Inode  uint64 `json:"inode"`
	Offset int64  `json:"offset"`
}

// loadCursor reads cursor.json. A missing or corrupt cursor yields the zero
// value, which means "nothing consumed yet" — the same fallback as
// Cursor.load() in cursor.py.
func loadCursor(dataDir string) recorderCursor {
	var c recorderCursor
	raw, err := os.ReadFile(filepath.Join(dataDir, "cursor.json"))
	if err != nil {
		return recorderCursor{}
	}
	if err := json.Unmarshal(raw, &c); err != nil {
		return recorderCursor{}
	}
	if c.Offset < 0 {
		c.Offset = 0
	}
	return c
}

func inodeOf(path string) (uint64, bool) {
	st, err := os.Stat(path)
	if err != nil {
		return 0, false
	}
	sys, ok := st.Sys().(*syscall.Stat_t)
	if !ok {
		return 0, false
	}
	return sys.Ino, true
}

// countFrom counts mutating+successful audit events in path from byteOffset to
// EOF. Unreadable files and malformed lines contribute zero rather than
// failing: this feeds an advisory freshness signal, never a tool's success.
func countFrom(path string, byteOffset int64) int {
	f, err := os.Open(path)
	if err != nil {
		return 0
	}
	defer f.Close()
	if byteOffset > 0 {
		if _, err := f.Seek(byteOffset, 0); err != nil {
			return 0
		}
	}
	count := 0
	sc := bufio.NewScanner(f)
	// Audit lines carry truncated tool output and can exceed the 64KB default.
	sc.Buffer(make([]byte, 0, 64*1024), 8*1024*1024)
	for sc.Scan() {
		line := strings.TrimSpace(sc.Text())
		if line == "" {
			continue
		}
		var entry map[string]any
		// Non-object JSON ("42", "[1,2]") fails to unmarshal into a map, which
		// is the behaviour we want — events.py skips those explicitly.
		if err := json.Unmarshal([]byte(line), &entry); err != nil {
			continue
		}
		result, _ := entry["result"].(string)
		if !successResults[result] {
			continue
		}
		tool, _ := entry["tool"].(string)
		if !mutatingTools[tool] {
			continue
		}
		count++
	}
	return count
}

// pendingEvents reports how many mutating+successful audit events sit past the
// recorder's committed cursor. It is the Go port of EventTailer.pending_count
// (src/mymcp/recorder/events.py:162), including the rotation branch: when the
// live audit.log's inode no longer matches the cursor's, the unread tail of
// audit.log.1 is counted first and the new file is counted from its head.
//
// Every failure path returns 0 — "no known backlog" — so a freshness probe can
// never make a tool call fail.
func pendingEvents(cfg *config.Config) int {
	logPath := filepath.Join(cfg.AuditLogDir, "audit.log")
	liveInode, ok := inodeOf(logPath)
	if !ok {
		return 0
	}
	cur := loadCursor(cfg.RecorderDataDir)
	if cur.Inode != 0 && cur.Inode != liveInode {
		count := 0
		rotated := filepath.Join(cfg.AuditLogDir, "audit.log.1")
		if rotInode, ok := inodeOf(rotated); ok && rotInode == cur.Inode {
			count += countFrom(rotated, cur.Offset)
		}
		return count + countFrom(logPath, 0)
	}
	return countFrom(logPath, cur.Offset)
}
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
cd go && go test ./internal/tools/ -run TestPendingEvents -v && go vet ./... && gofmt -l .
```

Expected: all five PASS, vet silent, gofmt prints nothing.

- [ ] **Step 5: Commit**

```bash
git add go/internal/tools/recorderstatus.go go/internal/tools/recorderstatus_test.go
git commit -m "feat(overview): derive the recorder backlog from cursor.json

Port of EventTailer.pending_count: read the committed offset, scan
audit.log from there, count mutating+successful events, handling
rotation via inode mismatch. The mutating set is the recorder's
MUTATING_TOOLS (6 entries), not mcpserver.writeTools (4) — the latter
lacks prepare_download and transfer_upload and would under-count.

Refs #92"
```

---

### Task 6: Derive `last_updated` and `stale`

`stale` is the conjunction of "there is work" and "it is not being done" — the local evaluation of the composite PromQL this project already recommends. An idle server has no backlog and is therefore never stale, which is the documented false positive to avoid.

**Files:**
- Modify: `go/internal/tools/recorderstatus.go`
- Modify: `go/internal/tools/recorderstatus_test.go`

**Interfaces:**
- Consumes: `pendingEvents(cfg)` and `loadCursor` from Task 5; `Config.RecorderMergeIntervalSec` from Task 4.
- Produces:
  - `type RecorderStatus struct { LastUpdated time.Time; LastUpdatedRaw string; PendingEvents int; Stale bool; StaleMinutes int }`
  - `func recorderStatusFor(cfg *config.Config, overviewPath string, now time.Time) RecorderStatus`
  - Task 7 consumes `recorderStatusFor` only.

- [ ] **Step 1: Write the failing tests**

Append to `go/internal/tools/recorderstatus_test.go` (add `"time"` to its imports):

```go
// seedOverview writes overview/overview.md with the given body and returns its path.
func seedOverview(t *testing.T, dataDir, body string) string {
	t.Helper()
	dir := filepath.Join(dataDir, "overview")
	if err := os.MkdirAll(dir, 0o755); err != nil {
		t.Fatal(err)
	}
	path := filepath.Join(dir, "overview.md")
	if err := os.WriteFile(path, []byte(body), 0o644); err != nil {
		t.Fatal(err)
	}
	return path
}

const overviewHeader = "# Server Overview\n_Last updated: 2026-07-13 02:08 UTC_\n_Hostname: h | OS: linux_\n\nbody\n"

func TestRecorderStatusParsesLastUpdatedHeader(t *testing.T) {
	d := testDeps(t)
	dataDir, logDir := t.TempDir(), t.TempDir()
	d.Cfg.RecorderDataDir, d.Cfg.AuditLogDir = dataDir, logDir
	path := seedOverview(t, dataDir, overviewHeader)

	now := time.Date(2026, 7, 13, 3, 8, 0, 0, time.UTC)
	st := recorderStatusFor(d.Cfg, path, now)
	want := time.Date(2026, 7, 13, 2, 8, 0, 0, time.UTC)
	if !st.LastUpdated.Equal(want) {
		t.Fatalf("LastUpdated = %v, want %v", st.LastUpdated, want)
	}
	if st.LastUpdatedRaw != "2026-07-13 02:08 UTC" {
		t.Fatalf("LastUpdatedRaw = %q", st.LastUpdatedRaw)
	}
}

func TestRecorderStatusFallsBackToMtime(t *testing.T) {
	d := testDeps(t)
	dataDir, logDir := t.TempDir(), t.TempDir()
	d.Cfg.RecorderDataDir, d.Cfg.AuditLogDir = dataDir, logDir
	path := seedOverview(t, dataDir, "# Server Overview\nno header here\n")
	mtime := time.Date(2026, 7, 1, 12, 0, 0, 0, time.UTC)
	if err := os.Chtimes(path, mtime, mtime); err != nil {
		t.Fatal(err)
	}

	st := recorderStatusFor(d.Cfg, path, mtime.Add(time.Hour))
	if !st.LastUpdated.Equal(mtime.UTC()) {
		t.Fatalf("LastUpdated = %v, want mtime %v", st.LastUpdated, mtime)
	}
	if st.LastUpdatedRaw != "" {
		t.Fatalf("LastUpdatedRaw should be empty when the header is absent, got %q", st.LastUpdatedRaw)
	}
}

func TestRecorderStatusIdleServerIsNeverStale(t *testing.T) {
	d := testDeps(t)
	dataDir, logDir := t.TempDir(), t.TempDir()
	d.Cfg.RecorderDataDir, d.Cfg.AuditLogDir = dataDir, logDir
	d.Cfg.RecorderMergeIntervalSec = 300
	path := seedOverview(t, dataDir, overviewHeader)
	// No audit.log at all => nothing pending. Overview is a month old.
	now := time.Date(2026, 8, 9, 0, 0, 0, 0, time.UTC)

	st := recorderStatusFor(d.Cfg, path, now)
	if st.PendingEvents != 0 {
		t.Fatalf("PendingEvents = %d, want 0", st.PendingEvents)
	}
	if st.Stale {
		t.Fatal("an idle server with nothing to fold must not be reported stale")
	}
}

func TestRecorderStatusBacklogPlusOldOverviewIsStale(t *testing.T) {
	d := testDeps(t)
	dataDir, logDir := t.TempDir(), t.TempDir()
	d.Cfg.RecorderDataDir, d.Cfg.AuditLogDir = dataDir, logDir
	d.Cfg.RecorderMergeIntervalSec = 300
	path := seedOverview(t, dataDir, overviewHeader)
	writeAudit(t, logDir, auditLine("write_file", "ok"))
	now := time.Date(2026, 8, 9, 0, 0, 0, 0, time.UTC) // ~27 days later

	st := recorderStatusFor(d.Cfg, path, now)
	if st.PendingEvents != 1 {
		t.Fatalf("PendingEvents = %d, want 1", st.PendingEvents)
	}
	if !st.Stale {
		t.Fatal("backlog + month-old overview must be stale")
	}
	if st.StaleMinutes < 38000 {
		t.Fatalf("StaleMinutes = %d, want roughly 27 days", st.StaleMinutes)
	}
}

func TestRecorderStatusOneSlowCycleIsNotStale(t *testing.T) {
	d := testDeps(t)
	dataDir, logDir := t.TempDir(), t.TempDir()
	d.Cfg.RecorderDataDir, d.Cfg.AuditLogDir = dataDir, logDir
	d.Cfg.RecorderMergeIntervalSec = 300
	path := seedOverview(t, dataDir, overviewHeader)
	writeAudit(t, logDir, auditLine("write_file", "ok"))
	// 400s after the last merge: there IS a backlog, but the threshold is 600s.
	now := time.Date(2026, 7, 13, 2, 8, 0, 0, time.UTC).Add(400 * time.Second)

	st := recorderStatusFor(d.Cfg, path, now)
	if st.Stale {
		t.Fatal("one slow cycle (under 2x the merge interval) must not be stale")
	}
}
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
cd go && go test ./internal/tools/ -run TestRecorderStatus -v
```

Expected: compile error — `undefined: recorderStatusFor`.

- [ ] **Step 3: Write the implementation**

Append to `go/internal/tools/recorderstatus.go` (add `"regexp"` and `"time"` to its imports):

```go
// lastUpdatedRe matches the header line the sidecar stamps on every write —
// see _build_header in src/mymcp/recorder/merge_cycle.py and stamp_last_updated
// in src/mymcp/recorder/overview.py.
var lastUpdatedRe = regexp.MustCompile(`(?m)^_Last updated: ([^_]+)_\s*$`)

// lastUpdatedLayouts covers both stamps the sidecar can produce: merge_cycle
// writes "2006-01-02 15:04 UTC", bootstrap and overview.stamp write ISO8601.
var lastUpdatedLayouts = []string{
	"2006-01-02 15:04 MST",
	time.RFC3339,
	"2006-01-02T15:04:05.999999-07:00",
	"2006-01-02T15:04:05",
}

// RecorderStatus is the freshness of the on-disk overview, derived entirely
// from files: the core cannot see the sidecar's in-memory state (the sidecar
// serves no HTTP and pushes metrics via OTLP).
type RecorderStatus struct {
	// LastUpdated is when the overview was last written. Zero if unknown.
	LastUpdated time.Time
	// LastUpdatedRaw is the header's verbatim text, empty if it was absent or
	// unparseable and LastUpdated came from the file's mtime instead.
	LastUpdatedRaw string
	// PendingEvents is the unconsumed mutating-event backlog.
	PendingEvents int
	// Stale is PendingEvents > 0 AND LastUpdated older than 2x the merge
	// interval. Both conjuncts matter: an idle server has no backlog and is
	// never stale, which is the false positive the metrics-based version of
	// this check originally had.
	Stale bool
	// StaleMinutes is the age of LastUpdated in whole minutes; 0 unless Stale.
	StaleMinutes int
}

func parseLastUpdated(body []byte) (time.Time, string) {
	m := lastUpdatedRe.FindSubmatch(body)
	if m == nil {
		return time.Time{}, ""
	}
	raw := strings.TrimSpace(string(m[1]))
	for _, layout := range lastUpdatedLayouts {
		if ts, err := time.Parse(layout, raw); err == nil {
			return ts.UTC(), raw
		}
	}
	return time.Time{}, ""
}

// recorderStatusFor derives freshness for the overview at overviewPath. A
// missing or unreadable overview yields the zero RecorderStatus (never stale) —
// that case is already reported by ServerOverview's RecorderDisabled branch.
func recorderStatusFor(cfg *config.Config, overviewPath string, now time.Time) RecorderStatus {
	var st RecorderStatus
	body, err := os.ReadFile(overviewPath)
	if err != nil {
		return st
	}
	st.LastUpdated, st.LastUpdatedRaw = parseLastUpdated(body)
	if st.LastUpdated.IsZero() {
		if fi, err := os.Stat(overviewPath); err == nil {
			st.LastUpdated = fi.ModTime().UTC()
		}
	}
	st.PendingEvents = pendingEvents(cfg)

	interval := cfg.RecorderMergeIntervalSec
	if interval <= 0 {
		interval = 300
	}
	// 2x the interval so a single slow cycle is not flagged — the threshold
	// v2's banner used (src/mymcp/recorder/tool.py:85).
	threshold := time.Duration(2*interval) * time.Second
	age := now.Sub(st.LastUpdated)
	if st.PendingEvents > 0 && !st.LastUpdated.IsZero() && age > threshold {
		st.Stale = true
		st.StaleMinutes = int(age.Minutes())
	}
	return st
}
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
cd go && go test ./internal/tools/ -v && go vet ./... && gofmt -l .
```

Expected: all `TestRecorderStatus*` and `TestPendingEvents*` PASS, vet silent, gofmt prints nothing.

- [ ] **Step 5: Commit**

```bash
git add go/internal/tools/recorderstatus.go go/internal/tools/recorderstatus_test.go
git commit -m "feat(overview): derive last_updated and staleness from disk

stale = backlog exists AND the overview is older than 2x the merge
interval. The conjunction is deliberate: it is the local form of the
composite PromQL this project recommends, and it keeps an idle server
with nothing to fold from being reported as broken.

Refs #92"
```

---

### Task 7: Surface staleness in the `server_overview` result

Fields for programmatic consumers, banner for the model that reads only the prose. Both, because a model handed a `stale: true` field it does not look at is exactly the failure this issue is about.

**Files:**
- Modify: `go/internal/tools/overview.go`
- Modify: `go/internal/tools/overview_test.go`

**Interfaces:**
- Consumes: `recorderStatusFor(cfg, overviewPath, now)` from Task 6.
- Produces: `ServerOverview(d Deps) map[string]any` — unchanged signature. Success result now carries `overview`, `last_updated` (string, empty if unknown), `pending_events` (int), `stale` (bool). When `stale` is true, `overview` is prefixed with a banner line.

- [ ] **Step 1: Write the failing tests**

Append to `go/internal/tools/overview_test.go` (it will need `"strings"` from Task 1, plus `"time"`):

```go
func TestServerOverviewReportsFreshnessFields(t *testing.T) {
	d := testDeps(t)
	dataDir, logDir := t.TempDir(), t.TempDir()
	d.Cfg.RecorderDataDir, d.Cfg.AuditLogDir = dataDir, logDir
	seedOverview(t, dataDir, overviewHeader)

	res := ServerOverview(d)
	if res["success"] != true {
		t.Fatalf("res = %v", res)
	}
	if res["last_updated"] != "2026-07-13 02:08 UTC" {
		t.Fatalf("last_updated = %v", res["last_updated"])
	}
	if res["pending_events"] != 0 {
		t.Fatalf("pending_events = %v, want 0", res["pending_events"])
	}
	if res["stale"] != false {
		t.Fatalf("stale = %v, want false", res["stale"])
	}
	if body, _ := res["overview"].(string); !strings.HasPrefix(body, "# Server Overview") {
		t.Fatalf("fresh overview must not be prefixed with a banner: %q", body)
	}
}

func TestServerOverviewPrefixesBannerWhenStale(t *testing.T) {
	d := testDeps(t)
	dataDir, logDir := t.TempDir(), t.TempDir()
	d.Cfg.RecorderDataDir, d.Cfg.AuditLogDir = dataDir, logDir
	d.Cfg.RecorderMergeIntervalSec = 300
	// Header is dated 2026-07-13; the test clock is now, so this is months old.
	seedOverview(t, dataDir, overviewHeader)
	writeAudit(t, logDir, auditLine("write_file", "ok"), auditLine("bash_execute", "ok"))

	res := ServerOverview(d)
	if res["stale"] != true {
		t.Fatalf("stale = %v, want true", res["stale"])
	}
	if res["pending_events"] != 2 {
		t.Fatalf("pending_events = %v, want 2", res["pending_events"])
	}
	body, _ := res["overview"].(string)
	if !strings.HasPrefix(body, "_⚠️") {
		t.Fatalf("stale overview must lead with a banner, got %q", body)
	}
	if !strings.Contains(body, "2 events pending") {
		t.Fatalf("banner should state the backlog, got %q", body)
	}
	if !strings.Contains(body, "systemctl status mymcp-recorder") {
		t.Fatalf("banner should state the remedy, got %q", body)
	}
	if !strings.Contains(body, "# Server Overview") {
		t.Fatalf("banner must prefix, not replace, the overview: %q", body)
	}
}
```

Note: `TestServerOverviewPresentReturnsContent` from Task 1 asserts
`res["overview"] == "# Server\nstuff\n"`. That body has no `_Last updated:_`
header and no audit log, so it is not stale and the assertion still holds.

- [ ] **Step 2: Run the tests to verify they fail**

```bash
cd go && go test ./internal/tools/ -run TestServerOverview -v
```

Expected: FAIL — `last_updated = <nil>` (the key does not exist yet).

- [ ] **Step 3: Write the implementation**

Replace the success branch of `ServerOverview` in `go/internal/tools/overview.go`:

```go
	st := recorderStatusFor(d.Cfg, path, time.Now())
	body := fsutil.DecodeReplace(raw)
	if st.Stale {
		// Prefix rather than replace: a model that reads only the prose still
		// sees this, which is the whole point — issue #92 was a frozen overview
		// being consumed as current fact for four weeks.
		body = fmt.Sprintf("_⚠️ %d events pending; recorder overview stale for %d"+
			" minutes — check: systemctl status mymcp-recorder_\n\n%s",
			st.PendingEvents, st.StaleMinutes, body)
	}
	lastUpdated := st.LastUpdatedRaw
	if lastUpdated == "" && !st.LastUpdated.IsZero() {
		lastUpdated = st.LastUpdated.Format(time.RFC3339)
	}
	return map[string]any{
		"success":        true,
		"overview":       body,
		"last_updated":   lastUpdated,
		"pending_events": st.PendingEvents,
		"stale":          st.Stale,
	}
```

Add `"time"` to the imports in `overview.go`.

- [ ] **Step 4: Run the full Go suite to verify**

```bash
cd go && go test ./... && go vet ./... && gofmt -l .
```

Expected: everything PASSes, vet silent, gofmt prints nothing.

- [ ] **Step 5: Verify compat is unaffected**

```bash
cd go && go build -o /tmp/mymcp ./cmd/mymcp
```

Then boot `/tmp/mymcp serve` with `MYMCP_*` env per CLAUDE.md and run:

```bash
MYMCP_COMPAT_URL=http://127.0.0.1:PORT ... pytest tests/compat/test_server_overview.py -v
```

Expected: PASS. Compat CI runs without an `overview.md`, so it takes the
`RecorderDisabled` branch, whose shape Task 1 left unchanged.

- [ ] **Step 6: Commit**

```bash
git add go/internal/tools/overview.go go/internal/tools/overview_test.go
git commit -m "feat(overview): report last_updated, pending_events and stale

Fields for programmatic consumers; a banner prefixed onto the body for
the model that reads only the prose. The frozen overview in #92 was
consumed as current fact for four weeks precisely because nothing in the
output said otherwise.

Refs #92"
```

---

### Task 8: Shrink the merge batch after a `max_tokens` failure

The longer the recorder has been down, the larger the first merge's backlog, the more reasoning it needs, and the more likely it truncates. With a threshold of 5 and a 300s interval, a recovering recorder trips its breaker ~25 minutes after being started and goes dormant again — so the fix for Task 3 can quietly fail to stick.

**Files:**
- Modify: `src/mymcp/recorder/merge_cycle.py:80-94` (constructor) and `:110-114` (batch read) and `:156-181` (failure branch) and the success path at `:207-208`
- Test: `tests/recorder/test_merge_cycle.py`

**Interfaces:**
- Consumes: `MergeCycle(client=…, tailer=…, store=…, max_events_per_cycle=…, max_tokens=…)` — constructor signature unchanged.
- Produces: `MergeCycle._adaptive_max: int` (initialised to `max_events_per_cycle`, floor `_MIN_BATCH = 5`). No metric, span, or public-API changes; `merge_cycles{reason="max_tokens"}` already names this outcome.

- [ ] **Step 1: Write the failing test**

Append to `tests/recorder/test_merge_cycle.py`. The helpers below are built from
that file's existing `_audit_line`, `_write_log`, and `_text_response` (lines
18-36) — do not introduce a parallel set of fakes:

```python
def _max_tokens_response() -> LLMResponse:
    """A truncated response: the model spent its budget before finishing."""
    return LLMResponse(
        text='{"new_changelog_lines": ["partial',
        tool_uses=[],
        stop_reason="max_tokens",
        usage=Usage(input_tokens=5000, output_tokens=16384),
    )


def _backlog_cycle(tmp_path: Path, *, count: int, max_events: int):
    """A MergeCycle over `count` unconsumed write_file events."""
    _write_log(
        tmp_path,
        *(
            _audit_line(tool="write_file", params={"file_path": f"/tmp/f{i}"})
            for i in range(count)
        ),
    )
    store = OverviewStore(tmp_path / "overview")
    store.write_overview("# Server Overview\n\n## TL;DR\nSeed.\n")
    tailer = EventTailer(log_dir=tmp_path, cursor_path=tmp_path / "cursor.json")
    fake = AsyncMock()
    cycle = MergeCycle(
        client=fake, tailer=tailer, store=store, max_events_per_cycle=max_events
    )
    return cycle, fake


@pytest.mark.anyio
async def test_max_tokens_failure_halves_the_next_batch(tmp_path):
    """A recovering recorder must drain a backlog, not trip its breaker.

    Issue #92: the bigger the backlog the more a reasoning model thinks, so the
    first merge after an outage is the most likely to truncate. Re-reading the
    same oversized batch every cycle guarantees the breaker opens first.
    """
    cycle, fake = _backlog_cycle(tmp_path, count=50, max_events=50)
    fake.call = AsyncMock(return_value=_max_tokens_response())

    with pytest.raises(ValueError, match="max_tokens"):
        await cycle.run_once()
    assert cycle._adaptive_max == 25

    with pytest.raises(ValueError, match="max_tokens"):
        await cycle.run_once()
    assert cycle._adaptive_max == 12

    # A success restores the full batch size.
    fake.call = AsyncMock(
        return_value=_text_response({"new_changelog_lines": [], "section_updates": {}})
    )
    result = await cycle.run_once()
    assert result.events_consumed == 12
    assert cycle._adaptive_max == 50


@pytest.mark.anyio
async def test_adaptive_batch_has_a_floor(tmp_path):
    cycle, fake = _backlog_cycle(tmp_path, count=8, max_events=8)
    fake.call = AsyncMock(return_value=_max_tokens_response())
    for _ in range(6):
        with pytest.raises(ValueError, match="max_tokens"):
            await cycle.run_once()
    assert cycle._adaptive_max == 5, "must not shrink below the floor"
```

`8 // 2 = 4` clamps to the floor of 5, and `5 // 2 = 2` clamps again — so the
second test pins the floor rather than the halving.

- [ ] **Step 2: Run the tests to verify they fail**

```bash
pytest tests/recorder/test_merge_cycle.py -k adaptive -v
pytest tests/recorder/test_merge_cycle.py -k max_tokens -v
```

Expected: FAIL — `AttributeError: 'MergeCycle' object has no attribute '_adaptive_max'`.

- [ ] **Step 3: Write the implementation**

In `src/mymcp/recorder/merge_cycle.py`, add a module-level constant near the top:

```python
# Floor for adaptive batch shrinking. Below this the per-call overhead
# dominates and a backlog would take too many cycles to drain.
_MIN_BATCH = 5
```

In `__init__`, after `self._max = max_events_per_cycle`:

```python
        # Effective batch size for the next cycle. Halved on each max_tokens
        # failure and restored on success — see run_once. Reasoning models bill
        # thinking against the output budget, so a large backlog can truncate
        # every cycle; re-reading the same batch would trip the circuit breaker
        # (threshold 5) before the backlog ever drained.
        self._adaptive_max = max_events_per_cycle
```

In `run_once`, change the batch-read bound from `self._max` to
`self._adaptive_max`:

```python
                if len(events) >= self._adaptive_max:
                    break
```

In the `except _MergeFailure` handler, shrink before re-raising:

```python
            except _MergeFailure as f:
                if f.reason == "max_tokens":
                    self._adaptive_max = max(_MIN_BATCH, self._adaptive_max // 2)
                    span.set_attribute("events.adaptive_max", self._adaptive_max)
                self._record_outcome(f.reason, start=start)
                self._tailer.rollback()
                raise ValueError(f.message) from f.__cause__
```

On the success path, immediately before `self._tailer.commit()`:

```python
            self._adaptive_max = self._max
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
pytest tests/recorder/test_merge_cycle.py -v
```

Expected: the two new tests PASS and every existing merge-cycle test still passes.

- [ ] **Step 5: Commit**

```bash
pytest tests/ --benchmark-disable -q && ruff check . && ruff format --check . && mypy src/mymcp
git add src/mymcp/recorder/merge_cycle.py tests/recorder/test_merge_cycle.py
git commit -m "fix(recorder): halve the batch after max_tokens instead of retrying it

50 -> 25 -> 12 -> 6 -> 5 converges inside the 5-failure breaker budget, so
a recorder recovering from a long outage drains its backlog instead of
going dormant again ~25 minutes after being started.

Refs #92"
```

---

### Task 9: `mymcp-recorder --install-unit` and a friendly missing-deps error

The systemd template ships with placeholders, but v3 removed `install-service`, the CLI that rendered `.in` templates. The only thing in the product that renders it is a test, using values hardcoded inside the test — so recovery required hand-transcription. Separately, `[project.scripts]` declares `mymcp-recorder` unconditionally while the real deps live in the `[recorder]` extra, so a base `pipx install` puts a command on `PATH` that dies with `ModuleNotFoundError: httpx`.

**Files:**
- Modify: `src/mymcp/recorder/__main__.py`
- Modify: `tests/recorder/test_sidecar_packaging.py`
- Modify: `README.md` (the `From v2.x` block from Task 3)

**Interfaces:**
- Consumes: `mymcp.config.get_settings()`, `src/mymcp/recorder/templates/mymcp-recorder.service.in`.
- Produces:
  - `render_unit(settings) -> str` in `mymcp.recorder.__main__` — the rendered systemd unit.
  - `main(argv: list[str] | None = None) -> int` — now argument-aware; `--install-unit` prints the unit and returns 0, `--output PATH` writes it instead. Note `[project.scripts]` calls `main()` with no arguments, so `argv` must default to `sys.argv[1:]`.

- [ ] **Step 1: Write the failing tests**

Rewrite `tests/recorder/test_sidecar_packaging.py`'s rendering test to exercise
the real code path, and add coverage for the CLI (read the existing file first;
keep any packaging assertions it already makes about the template being
included in the wheel):

```python
def test_render_unit_uses_real_settings(monkeypatch):
    """The template must be rendered by shipped code, not by this test.

    Issue #92: the only renderer was a test with values hardcoded inside it, so
    no code path and no document told an operator what to substitute.
    """
    monkeypatch.setenv("MYMCP_RECORDER_ENABLED", "true")
    from mymcp.config import get_settings, reset_settings_cache
    from mymcp.recorder.__main__ import render_unit

    reset_settings_cache()
    unit = render_unit(get_settings())

    assert "{" not in unit, f"unsubstituted placeholder left in unit:\n{unit}"
    assert "[Unit]" in unit and "[Service]" in unit and "[Install]" in unit
    assert "ExecStart=" in unit
    assert "NoNewPrivileges=true" in unit


def test_install_unit_writes_to_output(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("MYMCP_RECORDER_ENABLED", "true")
    from mymcp.config import reset_settings_cache
    from mymcp.recorder.__main__ import main

    reset_settings_cache()
    dest = tmp_path / "mymcp-recorder.service"
    assert main(["--install-unit", "--output", str(dest)]) == 0
    assert "ExecStart=" in dest.read_text()


def test_install_unit_prints_to_stdout(monkeypatch, capsys):
    monkeypatch.setenv("MYMCP_RECORDER_ENABLED", "true")
    from mymcp.config import reset_settings_cache
    from mymcp.recorder.__main__ import main

    reset_settings_cache()
    assert main(["--install-unit"]) == 0
    assert "[Service]" in capsys.readouterr().out
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
pytest tests/recorder/test_sidecar_packaging.py -v
```

Expected: FAIL — `ImportError: cannot import name 'render_unit'`.

- [ ] **Step 3: Write the implementation**

In `src/mymcp/recorder/__main__.py`, add imports (`argparse`, `shutil`,
`importlib.resources as resources`) and:

```python
def render_unit(settings) -> str:
    """Render the packaged systemd unit template with this install's values.

    v3 dropped `install-service` (Python-CLI machinery), which left the
    template shipped but unrenderable — issue #92. This is the renderer.
    """
    template = (
        resources.files("mymcp.recorder.templates")
        .joinpath("mymcp-recorder.service.in")
        .read_text(encoding="utf-8")
    )
    exec_start = shutil.which("mymcp-recorder") or "/usr/local/bin/mymcp-recorder"
    return template.format(
        service_user="mymcp",
        working_directory="/etc/mymcp",
        env_file="/etc/mymcp/.env",
        exec_start=exec_start,
    )
```

Then restructure `main`:

```python
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="mymcp-recorder")
    parser.add_argument(
        "--install-unit",
        action="store_true",
        help="print a systemd unit for this install and exit",
    )
    parser.add_argument(
        "--output",
        metavar="PATH",
        help="with --install-unit, write the unit to PATH instead of stdout",
    )
    args = parser.parse_args(argv)

    settings = get_settings()

    if args.install_unit:
        unit = render_unit(settings)
        if args.output:
            Path(args.output).write_text(unit, encoding="utf-8")
        else:
            print(unit)
        return 0

    logging.basicConfig(level=logging.INFO)
    if not settings.recorder_enabled:
        print(
            "mymcp-recorder: MYMCP_RECORDER_ENABLED is not true; refusing to start.",
            file=sys.stderr,
        )
        return 1
    try:
        from mymcp.recorder.wiring import build_supervisor
    except ImportError as e:
        # `mymcp-recorder` is an unconditional [project.scripts] entry while
        # the recorder's real deps live in the [recorder] extra, so a base
        # install puts this command on PATH with nothing behind it.
        print(
            f"mymcp-recorder: recorder dependencies are missing ({e}).\n"
            '  Install them with: pipx inject algony-mymcp "algony-mymcp[recorder]"',
            file=sys.stderr,
        )
        return 1
    supervisor = build_supervisor(settings)
    log.info("mymcp-recorder: starting (data_dir=%s)", settings.recorder_data_dir)
    asyncio.run(_amain(supervisor))
    log.info("mymcp-recorder: stopped")
    return 0
```

Move the module-level `from mymcp.recorder.wiring import build_supervisor`
import into `main` as shown (that is what makes the `ImportError` catchable),
and add `from pathlib import Path`.

- [ ] **Step 4: Run the tests to verify they pass**

```bash
pytest tests/recorder/ -v
```

Expected: the three new tests PASS and the rest of the recorder suite still passes.

- [ ] **Step 5: Verify the rendered unit is valid systemd**

```bash
python -m mymcp.recorder --install-unit --output /tmp/mymcp-recorder.service
systemd-analyze verify /tmp/mymcp-recorder.service || true
```

Expected: the unit renders with no `{placeholder}` left. `systemd-analyze` may
warn about the missing `mymcp` user or a non-existent `ExecStart` on a dev box —
that is fine; there must be no *syntax* errors.

- [ ] **Step 6: Point the README at the helper**

In the `From v2.x` block added in Task 3, replace the `sudo tee … <<'UNIT' … UNIT` heredoc with:

````markdown
```bash
# 2. Sidecar unit (rendered for this install)
mymcp-recorder --install-unit | sudo tee /etc/systemd/system/mymcp-recorder.service
```

Review the rendered `User`, `EnvironmentFile`, and `ExecStart` before starting it.
````

- [ ] **Step 7: Commit**

```bash
pytest tests/ --benchmark-disable -q && ruff check . && ruff format --check . && mypy src/mymcp
git add src/mymcp/recorder/__main__.py tests/recorder/test_sidecar_packaging.py README.md
git commit -m "feat(recorder): add --install-unit and a real missing-deps error

v3 removed install-service, leaving the systemd template shipped but
unrenderable — the only renderer was a test with the substitutions
hardcoded inside it, so recovery meant hand-transcribing. Also replaces
the bare ModuleNotFoundError: httpx from a base install with the pipx
inject command that fixes it.

Refs #92"
```

---

### Task 10: Delete the dead v2 banner and record the widened contract

With the banner in Go, `src/mymcp/recorder/tool.py` is dead on every path — only its own test imports it. Its six banner cases encode real priority rules and are worth keeping; the Python implementing them is not.

**Files:**
- Delete: `src/mymcp/recorder/tool.py`
- Delete: `tests/recorder/test_server_overview_tool.py`
- Modify: `go/internal/tools/recorderstatus_test.go` (port the surviving cases)
- Modify: `CLAUDE.md` (the "Python ↔ Go contract" section)

**Interfaces:**
- Consumes: `recorderStatusFor` from Task 6.
- Produces: nothing new. This task only removes code and adds documentation.

- [ ] **Step 1: Confirm the module is genuinely dead**

```bash
grep -rn "server_overview_handler\|recorder.tool\|recorder import tool\|_STUB_TEMPLATE" src/ tests/ go/ docs/ --include='*.py' --include='*.go' --include='*.md'
```

Expected: hits only in `src/mymcp/recorder/tool.py` itself,
`tests/recorder/test_server_overview_tool.py`, and the spec/plan docs. **If
anything else references it, stop and report** — the module is not dead and this
task's premise is wrong.

- [ ] **Step 2: Port the one banner rule not yet covered in Go**

Tasks 6 and 7 already cover "idle is not a failure", "backlog + old overview is
stale", and "one slow cycle is not stale". The remaining v2 rule worth keeping is
that the banner *prefixes* rather than replaces — covered by
`TestServerOverviewPrefixesBannerWhenStale` in Task 7. Read
`tests/recorder/test_server_overview_tool.py` and, for any rule it asserts that
none of the Go tests cover, add a Go test for it in `recorderstatus_test.go`
before deleting. The Go core has no `circuit_open` or `last_error` input, so
priority-1 and priority-3 cases have no Go counterpart — note that in the commit
message rather than porting them.

- [ ] **Step 3: Delete the dead module and its test**

```bash
git rm src/mymcp/recorder/tool.py tests/recorder/test_server_overview_tool.py
```

- [ ] **Step 4: Record the widened Python ↔ Go contract**

In `CLAUDE.md`, in the "Python ↔ Go contract" section, replace the first sentence:

```
The Python recorder and the Go server share the same `audit.log` format,
`MYMCP_*` env vars, `tokens.json`, and — since the overview staleness signal —
`cursor.json` and the recorder's `MUTATING_TOOLS` set. The Go core reads
`cursor.json` to compute the unconsumed backlog for `server_overview`'s `stale`
flag, so its `{file, inode, offset}` shape and the six-entry mutating-tool set
in `src/mymcp/recorder/events.py` cannot be changed unilaterally — see
`go/internal/tools/recorderstatus.go`.
```

Also add to the recorder section, near the `server_overview` description:

```
`server_overview` returns `last_updated`, `pending_events`, and `stale` alongside
`overview`, and prefixes the body with a warning banner when stale. `stale` is
the conjunction `pending_events > 0 AND last_updated older than 2 ×
MYMCP_RECORDER_MERGE_INTERVAL_SEC` — an idle server with no backlog is never
reported stale.
```

- [ ] **Step 5: Run every gate**

```bash
cd go && go test ./... && go vet ./... && gofmt -l . && cd ..
pytest tests/ --benchmark-disable -q
ruff check . && ruff format --check . && mypy src/mymcp
```

Expected: all green; `gofmt -l .` and `ruff` print nothing.

- [ ] **Step 6: Commit and open the PR**

```bash
git add -A
git commit -m "refactor(recorder): drop the dead v2 banner, document the contract

server_overview moved to the Go core in v3 but its status banner did not
follow, which is why #92's frozen overview carried no warning. Now that
the banner exists in Go, tool.py is dead on every path — only its own
test imported it. Its idle/stalled rules are covered by Go tests;
circuit_open and last_error have no Go counterpart (the core cannot see
sidecar memory) and are not ported.

CLAUDE.md now records cursor.json and MUTATING_TOOLS as part of the
Python <-> Go contract.

Refs #92"
git push -u origin feat/issue-92-recorder-staleness
gh pr create --title "feat(#92): surface a stalled recorder instead of serving stale state" --body "$(cat <<'BODY'
PR-B of two for #92. Depends on PR-A only for docs continuity; the code is independent.

- `server_overview` now returns `last_updated`, `pending_events`, and `stale`,
  and prefixes a warning banner when stale. Freshness is derived entirely from
  disk (`overview.md`'s `_Last updated:_` header, `cursor.json`'s offset scanned
  against `audit.log`) because the core cannot see the sidecar's memory.
- `stale` is a conjunction — backlog **and** an old overview — so an idle server
  is never reported broken. That was the documented false positive in the
  metrics-based version of this check.
- The Go mutating-tool set is a port of the recorder's `MUTATING_TOOLS`, not
  `mcpserver.writeTools`: the latter lacks `prepare_download` and
  `transfer_upload` and would under-count the backlog.
- `merge_cycle` halves its batch after a `max_tokens` failure, so a recorder
  recovering from a long outage drains instead of tripping its breaker.
- `mymcp-recorder --install-unit` renders the shipped systemd template; a base
  install now explains the missing `[recorder]` extra instead of raising
  `ModuleNotFoundError: httpx`.
- Deletes `src/mymcp/recorder/tool.py`, dead since the v3 port.

Spec: `docs/superpowers/specs/2026-08-09-issue-92-recorder-visibility-design.md`

Closes #92
BODY
)"
```

---

## Post-merge verification on the affected host

Not a code task — do this after both PRs are deployed, since the whole point is that CI could not have caught the original failure.

- [ ] Upgrade the host by following the new `From v2.x` README section verbatim, without consulting this plan or the spec. If any step is ambiguous, the documentation is still wrong — fix it rather than working around it.
- [ ] Confirm `systemctl is-active mymcp-recorder` reports `active`.
- [ ] Confirm `cursor.json`'s `offset` advances within one merge interval.
- [ ] Call `server_overview` and confirm `stale` is `false` and `last_updated` is recent.
- [ ] Stop the sidecar (`systemctl stop mymcp-recorder`), make a mutating tool call, wait past `2 × MYMCP_RECORDER_MERGE_INTERVAL_SEC`, and confirm `server_overview` reports `stale: true` with the banner. Restart it afterwards.
