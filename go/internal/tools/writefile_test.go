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
