package main

import (
	"bytes"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/algony-tony/mymcp/go/internal/setup"
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
	// Task 10: bare invocation is a status check, not a usage error — see
	// TestBareInvocationExitsZeroNotUsageError and TestBareInvocationTellsUserWhatToDoNext.
	if code := run(nil); code != 0 {
		t.Fatalf("exit %d, want 0", code)
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

func TestBareInvocationTellsUserWhatToDoNext(t *testing.T) {
	var buf bytes.Buffer
	statusHint(t.TempDir(), setup.RealSystem(), &buf)
	s := buf.String()
	if !strings.Contains(s, "not initialised") {
		t.Errorf("an uninitialised host must be told so:\n%s", s)
	}
	if !strings.Contains(s, "mymcp init") {
		t.Errorf("the next step must be named:\n%s", s)
	}
	if !strings.Contains(s, "mymcp serve") {
		t.Errorf("the trial-run option must be offered:\n%s", s)
	}
}

func TestBareInvocationExitsZeroNotUsageError(t *testing.T) {
	// Running `mymcp` with no args is a question, not a mistake.
	if code := run(nil); code != 0 {
		t.Fatalf("exit = %d, want 0", code)
	}
}

func TestHelpFlagIsRecognised(t *testing.T) {
	for _, arg := range []string{"-h", "--help", "help"} {
		if code := run([]string{arg}); code != 0 {
			t.Errorf("run(%q) = %d, want 0", arg, code)
		}
	}
}

func TestConfigExamplePrintsTemplate(t *testing.T) {
	if code := run([]string{"config", "example"}); code != 0 {
		t.Fatalf("exit = %d, want 0", code)
	}
}
