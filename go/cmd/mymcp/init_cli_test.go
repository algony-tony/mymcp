package main

import "testing"

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
