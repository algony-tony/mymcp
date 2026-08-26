package setup

import (
	"bytes"
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func writeEnv(t *testing.T, dir, content string, mode os.FileMode) string {
	t.Helper()
	p := filepath.Join(dir, ".env")
	if err := os.WriteFile(p, []byte(content), mode); err != nil {
		t.Fatal(err)
	}
	return p
}

func findCheck(checks []Check, name string) *Check {
	for i := range checks {
		if checks[i].Name == name {
			return &checks[i]
		}
	}
	return nil
}

func TestDoctorFlagsLooseEnvPermissions(t *testing.T) {
	dir := t.TempDir()
	writeEnv(t, dir, "MYMCP_ADMIN_TOKEN=tok_a\n", 0o644)
	c := findCheck(Doctor(dir, newFakeSystem()), "env permissions")
	if c == nil {
		t.Fatal("missing 'env permissions' check")
	}
	if c.Severity != SevFail {
		t.Fatalf("0644 .env holds the admin token; severity = %v, want SevFail", c.Severity)
	}
	if c.Remedy == "" {
		t.Error("a failing check must carry a pasteable remedy")
	}
}

func TestDoctorWarnsWhenAuditDisabled(t *testing.T) {
	dir := t.TempDir()
	writeEnv(t, dir, "MYMCP_ADMIN_TOKEN=tok_a\nMYMCP_AUDIT_ENABLED=false\n", 0o600)
	c := findCheck(Doctor(dir, newFakeSystem()), "audit enabled")
	if c == nil || c.Severity != SevWarn {
		t.Fatalf("audit-disabled must warn, got %+v", c)
	}
}

func TestDoctorFailsOnMissingAdminToken(t *testing.T) {
	dir := t.TempDir()
	writeEnv(t, dir, "MYMCP_PORT=8765\n", 0o600)
	c := findCheck(Doctor(dir, newFakeSystem()), "admin token")
	if c == nil || c.Severity != SevFail {
		t.Fatalf("missing admin token must fail, got %+v", c)
	}
}

func TestDoctorDetectsDuplicateBinariesOnPath(t *testing.T) {
	dir := t.TempDir()
	writeEnv(t, dir, "MYMCP_ADMIN_TOKEN=tok_a\n", 0o600)
	sys := newFakeSystem()
	sys.Paths["mymcp"] = "/usr/local/bin/mymcp"
	sys.Outputs["which -a mymcp"] = "/usr/local/bin/mymcp\n/root/.local/bin/mymcp\n"
	c := findCheck(Doctor(dir, sys), "duplicate binaries")
	if c == nil || c.Severity != SevFail {
		t.Fatalf("two mymcp on PATH must fail (upgrades hit the wrong copy), got %+v", c)
	}
	if !strings.Contains(c.Detail, "/root/.local/bin/mymcp") {
		t.Errorf("detail must name both copies: %q", c.Detail)
	}
}

func TestDoctorExitCodeAndStrict(t *testing.T) {
	ok := []Check{{Name: "a", Severity: SevOK}}
	warn := []Check{{Name: "a", Severity: SevWarn}}
	fail := []Check{{Name: "a", Severity: SevFail}}
	if DoctorExitCode(ok, false) != 0 || DoctorExitCode(warn, false) != 0 {
		t.Error("warnings alone must exit 0 without -strict")
	}
	if DoctorExitCode(warn, true) != 1 {
		t.Error("-strict must promote warnings to failure")
	}
	if DoctorExitCode(fail, false) != 1 {
		t.Error("a failure must exit 1")
	}
}

func TestRenderChecksJSONIsMachineReadable(t *testing.T) {
	var buf bytes.Buffer
	if err := RenderChecksJSON([]Check{{Group: "CONFIG", Name: "a", Severity: SevWarn, Detail: "d"}}, &buf); err != nil {
		t.Fatal(err)
	}
	var got []map[string]any
	if err := json.Unmarshal(buf.Bytes(), &got); err != nil {
		t.Fatalf("not valid JSON: %v\n%s", err, buf.String())
	}
	if got[0]["severity"] != "warn" {
		t.Fatalf("severity must serialise as a string, got %v", got[0]["severity"])
	}
}

func TestRenderChecksGroupsAndCounts(t *testing.T) {
	var buf bytes.Buffer
	RenderChecks([]Check{
		{Group: "INSTALL", Name: "binary", Severity: SevOK, Detail: "/usr/local/bin/mymcp"},
		{Group: "CONFIG", Name: "audit enabled", Severity: SevWarn, Remedy: "set MYMCP_AUDIT_ENABLED=true"},
	}, &buf)
	s := buf.String()
	if !strings.Contains(s, "INSTALL") || !strings.Contains(s, "CONFIG") {
		t.Errorf("groups must be headed:\n%s", s)
	}
	if !strings.Contains(s, "0 problems, 1 warning") {
		t.Errorf("missing the tally line:\n%s", s)
	}
}

func TestDoctorNeverCreatesFiles(t *testing.T) {
	// A diagnostic must not mutate what it diagnoses. auth.NewTokenStore
	// creates the store when absent, so the existence check must come first.
	dir := t.TempDir()
	writeEnv(t, dir, "MYMCP_ADMIN_TOKEN=tok_a\n", 0o600)
	_ = Doctor(dir, newFakeSystem())
	entries, err := os.ReadDir(dir)
	if err != nil {
		t.Fatal(err)
	}
	if len(entries) != 1 || entries[0].Name() != ".env" {
		var names []string
		for _, e := range entries {
			names = append(names, e.Name())
		}
		t.Fatalf("doctor created files: %v", names)
	}
}
