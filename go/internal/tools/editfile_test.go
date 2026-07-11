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
	if res["message"] != "old_string appears 3 times. Set replace_all=true to replace all occurrences." {
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
