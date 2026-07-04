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

func TestGlobExcludesDotfilesByDefault(t *testing.T) {
	d := testDeps(t)
	root := t.TempDir()
	os.WriteFile(filepath.Join(root, ".hidden.txt"), []byte("x"), 0o644)
	os.WriteFile(filepath.Join(root, "visible.txt"), []byte("x"), 0o644)
	res := Glob(d, "*.txt", root)
	files := res["files"].([]string)
	if len(files) != 1 || files[0] != filepath.Join(root, "visible.txt") {
		t.Fatalf("dotfiles must not match wildcards: %v", files)
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
