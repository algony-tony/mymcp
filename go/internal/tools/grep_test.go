package tools

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

// forceFallback disables rg for the test.
func forceFallback(d *Deps) { d.RgOverride = "disabled" }

func grepDir(t *testing.T) string {
	t.Helper()
	dir := t.TempDir()
	os.WriteFile(filepath.Join(dir, "a.log"), []byte("error one\nok line\nerror two\n"), 0o644)
	os.WriteFile(filepath.Join(dir, "b.txt"), []byte("nothing here\n"), 0o644)
	return dir
}

func TestGrepFallbackContentMode(t *testing.T) {
	d := testDeps(t)
	forceFallback(&d)
	dir := grepDir(t)
	res := Grep(d, "error \\w+", dir, "", "content", 0, 100, false)
	if res["match_count"] != 2 {
		t.Fatalf("match_count = %v; res=%v", res["match_count"], res)
	}
	out := res["results"].(string)
	if !strings.Contains(out, "a.log:1:error one") || !strings.Contains(out, "a.log:3:error two") {
		t.Fatalf("content lines wrong: %q", out)
	}
}

func TestGrepFallbackFilesAndCountModes(t *testing.T) {
	d := testDeps(t)
	forceFallback(&d)
	dir := grepDir(t)
	res := Grep(d, "error", dir, "", "files", 0, 100, false)
	out := res["results"].(string)
	if !strings.HasSuffix(strings.TrimSpace(out), "a.log") || res["match_count"] != 1 {
		t.Fatalf("files mode: %v", res)
	}
	res = Grep(d, "error", dir, "", "count", 0, 100, false)
	if !strings.Contains(res["results"].(string), "a.log: 2") {
		t.Fatalf("count mode: %v", res)
	}
}

func TestGrepFallbackGlobFilterAndCaseInsensitive(t *testing.T) {
	d := testDeps(t)
	forceFallback(&d)
	dir := grepDir(t)
	res := Grep(d, "ERROR", dir, "*.log", "content", 0, 100, true)
	if res["match_count"] != 2 {
		t.Fatalf("case-insensitive+glob: %v", res)
	}
	res = Grep(d, "error", dir, "*.txt", "content", 0, 100, false)
	if res["match_count"] != 0 {
		t.Fatalf("glob filter must exclude a.log: %v", res)
	}
}

func TestGrepFallbackTruncationMarker(t *testing.T) {
	d := testDeps(t)
	forceFallback(&d)
	dir := t.TempDir()
	var sb strings.Builder
	for range 10 {
		sb.WriteString("match\n")
	}
	os.WriteFile(filepath.Join(dir, "m.txt"), []byte(sb.String()), 0o644)
	res := Grep(d, "match", dir, "", "content", 0, 3, false)
	out := res["results"].(string)
	if !strings.Contains(out, "[TRUNCATED: ") || !strings.Contains(out, " more matches not shown]") {
		t.Fatalf("truncation marker missing: %q", out)
	}
}

func TestGrepInvalidRegex(t *testing.T) {
	d := testDeps(t)
	forceFallback(&d)
	res := Grep(d, "([unclosed", t.TempDir(), "", "content", 0, 100, false)
	if res["success"] != false || res["error"] != "InvalidRegex" {
		t.Fatalf("invalid regex: %v", res)
	}
}

func TestGrepFallbackSkipsProtected(t *testing.T) {
	d := testDeps(t)
	forceFallback(&d)
	dir := grepDir(t)
	d.Protected = append(d.Protected, protectedAll(dir))
	res := Grep(d, "error", dir, "", "content", 0, 100, false)
	if res["match_count"] != 0 {
		t.Fatalf("protected dir must yield 0: %v", res)
	}
}

func TestGrepRgPathIfInstalled(t *testing.T) {
	d := testDeps(t)
	if d.RgPath() == "" {
		t.Skip("ripgrep not installed")
	}
	dir := grepDir(t)
	res := Grep(d, "error", dir, "", "content", 0, 100, false)
	if res["match_count"] != 2 {
		t.Fatalf("rg path: %v", res)
	}
}
