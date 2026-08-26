package main

import (
	"bytes"
	"encoding/json"
	"os"
	"strings"
	"testing"
)

// captureStdout redirects os.Stdout for the duration of fn and returns
// everything written to it. Every other CLI test in this package repeats
// the same os.Pipe dance inline; runDoctor needs it twice so it is factored
// out here.
func captureStdout(t *testing.T, fn func() int) (string, int) {
	t.Helper()
	old := os.Stdout
	r, w, err := os.Pipe()
	if err != nil {
		t.Fatal(err)
	}
	os.Stdout = w
	code := fn()
	_ = w.Close()
	os.Stdout = old
	var buf bytes.Buffer
	_, _ = buf.ReadFrom(r)
	return buf.String(), code
}

func TestRunDoctorJSONOutputParsesAndReflectsFailure(t *testing.T) {
	dir := t.TempDir() // no .env here: Doctor must report exactly one SevFail check
	out, code := captureStdout(t, func() int {
		return run([]string{"doctor", "-config-dir", dir, "-json"})
	})
	if code != 1 {
		t.Fatalf("exit = %d, want 1 (missing config is a failure)", code)
	}
	var checks []map[string]any
	if err := json.Unmarshal([]byte(out), &checks); err != nil {
		t.Fatalf("doctor -json did not emit valid JSON: %v\n%s", err, out)
	}
	if len(checks) == 0 {
		t.Fatal("expected at least one check")
	}
	if checks[0]["severity"] != "fail" {
		t.Errorf("checks[0] severity = %v, want fail", checks[0]["severity"])
	}
}

func TestRunDoctorStrictRendersHumanReadableOutput(t *testing.T) {
	dir := t.TempDir()
	out, code := captureStdout(t, func() int {
		return run([]string{"doctor", "-config-dir", dir, "-strict"})
	})
	if code != 1 {
		t.Fatalf("exit = %d, want 1", code)
	}
	if !strings.Contains(out, "problem") {
		t.Errorf("expected the human-readable tally line in output:\n%s", out)
	}
}
