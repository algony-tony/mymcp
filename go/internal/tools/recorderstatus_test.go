package tools

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"syscall"
	"testing"
	"time"
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
		auditLine("bash_execute", "ok"),     // counts
		auditLine("read_file", "ok"),        // read-only: ignored
		auditLine("write_file", "denied"),   // not successful: ignored
		auditLine("prepare_download", "ok"), // in MUTATING_TOOLS, not in writeTools
		auditLine("transfer_upload", "ok"),  // endpoint audit name, not an MCP tool
		"not json at all",                   // corrupt: skipped
		"[1,2,3]",                           // valid JSON, not an object: skipped
		"",                                  // blank: skipped
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

func TestPendingEventsOversizedLineDoesNotSwallowFollowingEvents(t *testing.T) {
	d := testDeps(t)
	dataDir, logDir := t.TempDir(), t.TempDir()
	d.Cfg.RecorderDataDir, d.Cfg.AuditLogDir = dataDir, logDir

	// bufio.Scanner permanently aborts the whole scan — silently dropping
	// every line after, not just the offending one — the moment a single
	// line exceeds its buffer, no matter how large that buffer is set. This
	// line (9MB) is deliberately bigger than any fixed cap we'd plausibly
	// configure and is not valid JSON, so it is skipped on its own merits;
	// the point of the test is that the *next* line must still be counted,
	// which only holds if the reader degrades one line at a time instead of
	// enforcing any max line size at all.
	huge := strings.Repeat("x", 9_000_000)
	writeAudit(t, logDir, huge, auditLine("bash_execute", "ok"))

	if got := pendingEvents(d.Cfg); got != 1 {
		t.Fatalf("pendingEvents = %d, want 1", got)
	}
}

func TestPendingEventsNegativeOffsetIsZero(t *testing.T) {
	d := testDeps(t)
	dataDir, logDir := t.TempDir(), t.TempDir()
	d.Cfg.RecorderDataDir, d.Cfg.AuditLogDir = dataDir, logDir
	writeAudit(t, logDir, auditLine("write_file", "ok"), auditLine("edit_file", "ok"))

	// Hand-corrupt the cursor with a negative offset (inode still matches,
	// so this hits the non-rotation path). Python's Cursor.load() does not
	// clamp this either; the eventual f.seek(start_offset) raises OSError,
	// caught to return 0. The Go port must fail the same way rather than
	// treating negative-as-zero and recounting the whole file as pending.
	writeCursor(t, dataDir, logDir, -1)

	if got := pendingEvents(d.Cfg); got != 0 {
		t.Fatalf("pendingEvents = %d, want 0", got)
	}
}

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

// TestRecorderStatusParsesRealOnDiskFormat covers the only format that ever
// actually lands in overview.md: OverviewStore.write_overview always routes
// through _stamp_last_updated (src/mymcp/recorder/overview.py:84-104), which
// unconditionally strips any existing "_Last updated: ..._" line — including
// the "%Y-%m-%d %H:%M UTC" one _build_header just wrote — and replaces it
// with datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00",
// "Z"). Bootstrap's LLM-authored placeholder line meets the same fate via the
// same write_overview call. TestRecorderStatusParsesLastUpdatedHeader above
// exercises a format that is tolerated but never actually written by the
// sidecar; this test exercises the one that is.
func TestRecorderStatusParsesRealOnDiskFormat(t *testing.T) {
	d := testDeps(t)
	dataDir, logDir := t.TempDir(), t.TempDir()
	d.Cfg.RecorderDataDir, d.Cfg.AuditLogDir = dataDir, logDir
	body := "# Server Overview\n_Last updated: 2026-07-13T02:08:00Z_\n_Hostname: h | OS: linux_\n\nbody\n"
	path := seedOverview(t, dataDir, body)

	now := time.Date(2026, 7, 13, 3, 8, 0, 0, time.UTC)
	st := recorderStatusFor(d.Cfg, path, now)
	want := time.Date(2026, 7, 13, 2, 8, 0, 0, time.UTC)
	if !st.LastUpdated.Equal(want) {
		t.Fatalf("LastUpdated = %v, want %v", st.LastUpdated, want)
	}
	if st.LastUpdatedRaw != "2026-07-13T02:08:00Z" {
		t.Fatalf("LastUpdatedRaw = %q", st.LastUpdatedRaw)
	}
}

// TestRecorderStatusFallsBackToMtimeWhenHeaderUnparseable covers the branch
// distinct from an absent header: the marker line is present and matches
// lastUpdatedRe, but its content matches none of lastUpdatedLayouts. This
// must fall back to mtime exactly like the absent-header case.
func TestRecorderStatusFallsBackToMtimeWhenHeaderUnparseable(t *testing.T) {
	d := testDeps(t)
	dataDir, logDir := t.TempDir(), t.TempDir()
	d.Cfg.RecorderDataDir, d.Cfg.AuditLogDir = dataDir, logDir
	path := seedOverview(t, dataDir, "# Server Overview\n_Last updated: not-a-date_\n\nbody\n")
	mtime := time.Date(2026, 7, 1, 12, 0, 0, 0, time.UTC)
	if err := os.Chtimes(path, mtime, mtime); err != nil {
		t.Fatal(err)
	}

	st := recorderStatusFor(d.Cfg, path, mtime.Add(time.Hour))
	if !st.LastUpdated.Equal(mtime.UTC()) {
		t.Fatalf("LastUpdated = %v, want mtime %v", st.LastUpdated, mtime)
	}
	if st.LastUpdatedRaw != "" {
		t.Fatalf("LastUpdatedRaw should be empty when the header content is unparseable, got %q", st.LastUpdatedRaw)
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
