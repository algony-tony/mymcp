package main

import (
	"bytes"
	"os"
	"testing"
)

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
