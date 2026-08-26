package main

import (
	"path/filepath"
	"testing"
)

// tokenEnv points config at a fresh temp token file for a subtest.
func tokenEnv(t *testing.T) {
	t.Helper()
	t.Setenv("MYMCP_TOKEN_FILE", filepath.Join(t.TempDir(), "tokens.json"))
	t.Setenv("MYMCP_ADMIN_TOKEN", "admin")
}

func TestTokenListEmpty(t *testing.T) {
	tokenEnv(t)
	if code := run([]string{"token", "list"}); code != 0 {
		t.Fatalf("empty list exit=%d, want 0", code)
	}
}

func TestTokenNoSubcommand(t *testing.T) {
	if code := run([]string{"token"}); code != 2 {
		t.Fatalf("token no-subcommand exit=%d, want 2", code)
	}
}

func TestTokenUnknownSubcommand(t *testing.T) {
	if code := run([]string{"token", "frobnicate"}); code != 2 {
		t.Fatalf("unknown subcommand exit=%d, want 2", code)
	}
}

func TestTokenAddMissingName(t *testing.T) {
	tokenEnv(t)
	if code := run([]string{"token", "add", "--role", "rw"}); code != 2 {
		t.Fatalf("add missing name exit=%d, want 2", code)
	}
}

func TestTokenAddBadRole(t *testing.T) {
	tokenEnv(t)
	// CreateToken rejects roles outside {ro,rw} → runtime error → exit 1.
	if code := run([]string{"token", "add", "--role", "superuser", "ci"}); code != 1 {
		t.Fatalf("add bad-role exit=%d, want 1", code)
	}
}

func TestTokenAddBadFlag(t *testing.T) {
	if code := run([]string{"token", "add", "--nope"}); code != 2 {
		t.Fatalf("add bad-flag exit=%d, want 2", code)
	}
}

func TestTokenRevokeNoArg(t *testing.T) {
	if code := run([]string{"token", "revoke"}); code != 2 {
		t.Fatalf("revoke no-arg exit=%d, want 2", code)
	}
}

func TestTokenRevokeExisting(t *testing.T) {
	tokenEnv(t)
	// Add a token via the store, then revoke it through the CLI.
	store, err := loadTokenStore()
	if err != nil {
		t.Fatal(err)
	}
	tok, err := store.CreateToken("ci", "ro")
	if err != nil {
		t.Fatal(err)
	}
	if code := run([]string{"token", "revoke", tok}); code != 0 {
		t.Fatalf("revoke existing exit=%d, want 0", code)
	}
}

func TestServeBadFlag(t *testing.T) {
	// flag.ContinueOnError → Parse error → exit 2, without starting a server.
	if code := run([]string{"serve", "--nonexistent-flag"}); code != 2 {
		t.Fatalf("serve bad-flag exit=%d, want 2", code)
	}
}

func TestUnknownCommandStillExitsTwo(t *testing.T) {
	if code := run([]string{"frobnicate"}); code != 2 {
		t.Fatalf("exit = %d, want 2", code)
	}
}
