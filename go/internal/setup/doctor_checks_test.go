package setup

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestTruncate(t *testing.T) {
	if got := truncate("short", 10); got != "short" {
		t.Errorf("truncate(short) = %q, want unchanged", got)
	}
	if got := truncate("abcdefghij", 3); got != "abc…" {
		t.Errorf("truncate(long) = %q, want %q", got, "abc…")
	}
}

func TestTokenStoreChecksMissingFile(t *testing.T) {
	path := filepath.Join(t.TempDir(), "tokens.json")
	checks := tokenStoreChecks(path)
	c := findCheck(checks, "token store")
	if c == nil || c.Severity != SevFail {
		t.Fatalf("checks = %+v, want a SevFail 'token store' check", checks)
	}
	if c.Remedy == "" {
		t.Error("a missing token store must carry a remedy")
	}
}

func TestTokenStoreChecksWrongMode(t *testing.T) {
	path, _ := newTestTokenStore(t, "rw")
	if err := os.Chmod(path, 0o644); err != nil {
		t.Fatal(err)
	}
	c := findCheck(tokenStoreChecks(path), "token store permissions")
	if c == nil || c.Severity != SevFail {
		t.Fatalf("checks = %+v, want SevFail for a 0644 token store", c)
	}
}

func TestTokenStoreChecksOnlyDisabledTokensFails(t *testing.T) {
	path := filepath.Join(t.TempDir(), "tokens.json")
	raw := `{"tokens":{"tok_old":{"name":"old","created_at":"2020-01-01T00:00:00Z","last_used":null,"enabled":false,"role":"rw"}},"admin_token":"unused"}`
	if err := os.WriteFile(path, []byte(raw), 0o600); err != nil {
		t.Fatal(err)
	}
	c := findCheck(tokenStoreChecks(path), "client tokens")
	if c == nil || c.Severity != SevFail {
		t.Fatalf("checks = %+v, want SevFail when every token is disabled", c)
	}
	if !strings.Contains(c.Detail, "no enabled tokens") {
		t.Errorf("detail = %q, want it to say no enabled tokens", c.Detail)
	}
}

func TestTokenStoreChecksHealthyStoreIsOK(t *testing.T) {
	path, _ := newTestTokenStore(t, "rw")
	checks := tokenStoreChecks(path)
	perm := findCheck(checks, "token store permissions")
	tokens := findCheck(checks, "client tokens")
	if perm == nil || perm.Severity != SevOK {
		t.Fatalf("permissions check = %+v, want SevOK", perm)
	}
	if tokens == nil || tokens.Severity != SevOK {
		t.Fatalf("client tokens check = %+v, want SevOK", tokens)
	}
	if !strings.Contains(tokens.Detail, "1 enabled") {
		t.Errorf("detail = %q, want it to count the enabled token", tokens.Detail)
	}
}

func writeUnit(t *testing.T, content string) string {
	t.Helper()
	path := filepath.Join(t.TempDir(), "mymcp.service")
	if err := os.WriteFile(path, []byte(content), 0o644); err != nil {
		t.Fatal(err)
	}
	return path
}

func TestExecStartCheckMissingUnitFileWarns(t *testing.T) {
	sys := newFakeSystem()
	c := execStartCheck(sys, filepath.Join(t.TempDir(), "does-not-exist.service"))
	if c.Severity != SevWarn {
		t.Fatalf("severity = %v, want SevWarn for a missing unit file", c.Severity)
	}
	if c.Remedy == "" {
		t.Error("a missing unit file must carry a remedy")
	}
}

func TestExecStartCheckNoExecStartLineWarns(t *testing.T) {
	sys := newFakeSystem()
	unit := writeUnit(t, "[Service]\nUser=mymcp\n")
	c := execStartCheck(sys, unit)
	if c.Severity != SevWarn {
		t.Fatalf("severity = %v, want SevWarn when the unit has no ExecStart=", c.Severity)
	}
}

func TestExecStartCheckMismatchFails(t *testing.T) {
	sys := newFakeSystem()
	sys.Paths["mymcp"] = "/usr/local/bin/mymcp"
	unit := writeUnit(t, "[Service]\nExecStart=/opt/old/mymcp serve\n")
	c := execStartCheck(sys, unit)
	if c.Severity != SevFail {
		t.Fatalf("severity = %v, want SevFail on a stale ExecStart", c.Severity)
	}
	if !strings.Contains(c.Detail, "/opt/old/mymcp") || !strings.Contains(c.Detail, "/usr/local/bin/mymcp") {
		t.Errorf("detail = %q, want it to name both binaries", c.Detail)
	}
}

func TestExecStartCheckMatchIsOK(t *testing.T) {
	sys := newFakeSystem()
	sys.Paths["mymcp"] = "/usr/local/bin/mymcp"
	unit := writeUnit(t, "[Service]\nExecStart=/usr/local/bin/mymcp serve\n")
	c := execStartCheck(sys, unit)
	if c.Severity != SevOK {
		t.Fatalf("severity = %v, want SevOK when the unit runs the resolved binary", c.Severity)
	}
}
