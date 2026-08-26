package main

import (
	"os"
	"testing"
)

func TestInitRejectsNonInteractiveWithoutYes(t *testing.T) {
	// No TTY in `go test`, so init must refuse rather than hang or take
	// silent defaults.
	if code := run([]string{"init", "-config-dir", t.TempDir(), "-dry-run"}); code == 0 {
		t.Fatal("init without -yes and without a TTY must fail")
	}
}

func TestInitDryRunWithYesSucceedsAndWritesNothing(t *testing.T) {
	dir := t.TempDir()
	code := run([]string{
		"init", "-yes", "-dry-run",
		"-config-dir", dir,
		"-log-dir", dir + "/log",
		"-recorder-data-dir", dir + "/rec",
		"-start=false",
	})
	if code != 0 {
		t.Fatalf("init -yes -dry-run exit = %d, want 0", code)
	}
}

func TestUnknownInitFlagIsUsageError(t *testing.T) {
	if code := run([]string{"init", "-nope"}); code != 2 {
		t.Fatalf("exit = %d, want 2 for a usage error", code)
	}
}

func TestInitMarksOnlyTypedFlagsExplicit(t *testing.T) {
	// -port is typed, -bind is not; only -port may appear in Explicit.
	o, err := parseInitFlags([]string{
		"-yes", "-dry-run",
		"-config-dir", t.TempDir(),
		"-port", "9100",
		"-start=false",
	})
	if err != nil {
		t.Fatalf("parseInitFlags: %v", err)
	}
	if !o.Explicit["port"] {
		t.Error("Explicit[\"port\"] must be true: -port was typed")
	}
	if o.Explicit["bind"] {
		t.Error("Explicit[\"bind\"] must be false: -bind was not typed")
	}
}

func TestInitTreatsAnEnvSourcedRecorderKeyAsExplicit(t *testing.T) {
	dir := t.TempDir()
	t.Setenv("MYMCP_RECORDER_LLM_API_KEY", "sk-rotated")
	code := run([]string{
		"init", "-yes", "-dry-run",
		"-config-dir", dir,
		"-log-dir", dir + "/log",
		"-recorder-data-dir", dir + "/rec",
		"-start=false",
	})
	if code != 0 {
		t.Fatalf("exit = %d, want 0", code)
	}
}

func TestParseInitFlagsMarksEnvSourcedRecorderKeyExplicit(t *testing.T) {
	// flag.Visit only reports flags typed on the command line; a rotated
	// MYMCP_RECORDER_LLM_API_KEY must still win over a stale .env value, so
	// parseInitFlags must mark it Explicit even though -recorder-api-key was
	// never typed.
	t.Setenv("MYMCP_RECORDER_LLM_API_KEY", "sk-rotated")
	o, err := parseInitFlags([]string{"-yes", "-dry-run", "-config-dir", t.TempDir()})
	if err != nil {
		t.Fatalf("parseInitFlags: %v", err)
	}
	if !o.Explicit["recorder-api-key"] {
		t.Error("Explicit[\"recorder-api-key\"] must be true when MYMCP_RECORDER_LLM_API_KEY is set")
	}
	if o.RecorderAPIKey != "sk-rotated" {
		t.Errorf("RecorderAPIKey = %q, want the env-sourced value", o.RecorderAPIKey)
	}
}

func TestInitRefusesSilentDegradeWhenNotRoot(t *testing.T) {
	if os.Geteuid() == 0 {
		t.Skip("this test is about the non-root path")
	}
	if _, err := os.Stat("/run/systemd/system"); err != nil {
		t.Skip("no systemd on this host; the degrade is legitimate here")
	}
	dir := t.TempDir()
	code := run([]string{
		"init", "-yes",
		"-config-dir", dir,
		"-log-dir", dir + "/log",
		"-recorder-data-dir", dir + "/rec",
		"-start=false",
	})
	if code == 0 {
		t.Fatal("a forgotten sudo must fail loudly, not degrade to a files-only success")
	}
}

func TestInitFilesOnlyRunsUnprivileged(t *testing.T) {
	dir := t.TempDir()
	code := run([]string{
		"init", "-yes", "-files-only",
		"-config-dir", dir,
		"-log-dir", dir + "/log",
		"-recorder-data-dir", dir + "/rec",
		"-start=false",
	})
	if code != 0 {
		t.Fatalf("exit = %d, want 0 — -files-only is the supported unprivileged path", code)
	}
	if _, err := os.Stat(dir + "/.env"); err != nil {
		t.Errorf("-files-only must still write the config: %v", err)
	}
}
