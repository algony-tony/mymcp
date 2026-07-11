package main

import (
	"bytes"
	"os"
	"path/filepath"
	"testing"
)

func TestTokenAddThenList(t *testing.T) {
	tok := filepath.Join(t.TempDir(), "tokens.json")
	t.Setenv("MYMCP_TOKEN_FILE", tok)
	t.Setenv("MYMCP_ADMIN_TOKEN", "admin")
	if code := run([]string{"token", "add", "--role", "rw", "ci"}); code != 0 {
		t.Fatalf("add exit=%d", code)
	}
	if code := run([]string{"token", "list"}); code != 0 {
		t.Fatalf("list exit=%d", code)
	}
}

func TestTokenRevokeMissing(t *testing.T) {
	tok := filepath.Join(t.TempDir(), "tokens.json")
	t.Setenv("MYMCP_TOKEN_FILE", tok)
	t.Setenv("MYMCP_ADMIN_TOKEN", "admin")
	if code := run([]string{"token", "revoke", "tok_absent"}); code != 1 {
		t.Fatalf("revoke-missing exit=%d (want 1)", code)
	}
}

func TestRunVersion(t *testing.T) {
	r, w, _ := os.Pipe()
	old := os.Stdout
	os.Stdout = w
	code := run([]string{"version"})
	w.Close()
	os.Stdout = old
	var buf bytes.Buffer
	buf.ReadFrom(r)
	if code != 0 {
		t.Fatalf("exit %d, want 0", code)
	}
	if got := buf.String(); got != "mymcp dev\n" {
		t.Fatalf("output = %q, want %q", got, "mymcp dev\n")
	}
}

func TestRunNoArgs(t *testing.T) {
	if code := run(nil); code != 2 {
		t.Fatalf("exit %d, want 2", code)
	}
}

// TestRunServeStub removed: serve now actually starts the server (Task 9);
// the stub test would block. End-to-end serve behaviour is tested via smoke
// test and httpserver package tests.

func TestRunUnknown(t *testing.T) {
	if code := run([]string{"bogus"}); code != 2 {
		t.Fatalf("exit %d, want 2", code)
	}
}
