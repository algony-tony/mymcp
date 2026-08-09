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
