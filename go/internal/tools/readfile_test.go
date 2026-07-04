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
