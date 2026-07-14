package audit

import (
	"os"
	"path/filepath"
	"testing"
)

func TestCloseEnabledThenIdempotent(t *testing.T) {
	dir := t.TempDir()
	w, err := New(true, dir, 1<<20, 5)
	if err != nil {
		t.Fatal(err)
	}
	if err := w.Log(Entry{TS: "t", Tool: "x", Result: "ok", Params: map[string]any{}}); err != nil {
		t.Fatal(err)
	}
	if err := w.Close(); err != nil {
		t.Fatalf("first close: %v", err)
	}
	// Second close is a no-op (file already released).
	if err := w.Close(); err != nil {
		t.Fatalf("second close must be a no-op: %v", err)
	}
	if _, err := os.Stat(filepath.Join(dir, "audit.log")); err != nil {
		t.Fatalf("audit.log must persist after close: %v", err)
	}
}

func TestCloseDisabledIsNoop(t *testing.T) {
	w, err := New(false, t.TempDir(), 1<<20, 5)
	if err != nil {
		t.Fatal(err)
	}
	if err := w.Close(); err != nil {
		t.Fatalf("close on disabled writer must be nil: %v", err)
	}
}

func TestEditFileOutput(t *testing.T) {
	got := EditFileOutput("/f", 4, 2, 3)
	if got["path"] != "/f" || got["lines_added"] != 4 || got["lines_removed"] != 2 || got["hunk_count"] != 3 {
		t.Fatalf("EditFileOutput = %v", got)
	}
}

func TestWriteFileOutputEdgeCases(t *testing.T) {
	// No newline: whole content is the first line.
	got := WriteFileOutput("/f", []byte("single line"))
	if got["first_line"] != "single line" || got["size_bytes"] != 11 {
		t.Fatalf("no-newline = %v", got)
	}
	// Empty content: empty first line, zero size, sha of empty input.
	empty := WriteFileOutput("/f", nil)
	if empty["first_line"] != "" || empty["size_bytes"] != 0 {
		t.Fatalf("empty = %v", empty)
	}
	if s, _ := empty["sha256"].(string); len(s) != 64 {
		t.Fatalf("sha256 len = %d", len(s))
	}
}
