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
