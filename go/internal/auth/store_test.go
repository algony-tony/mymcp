package auth

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestLoadCreatesMissingFile(t *testing.T) {
	path := filepath.Join(t.TempDir(), "sub", "tokens.json")
	st, err := NewTokenStore(path, "admin-secret")
	if err != nil {
		t.Fatal(err)
	}
	if _, err := os.Stat(path); err != nil {
		t.Fatalf("file not created: %v", err)
	}
	if st.AdminToken() != "admin-secret" {
		t.Fatal("admin token not set")
	}
}

func TestLoadOverridesAdminAndDefaultsRole(t *testing.T) {
	path := filepath.Join(t.TempDir(), "tokens.json")
	seed := `{"tokens": {"tok_abc": {"name": "old", "created_at": "x", "last_used": null, "enabled": true}}, "admin_token": "stale"}`
	if err := os.WriteFile(path, []byte(seed), 0o600); err != nil {
		t.Fatal(err)
	}
	st, err := NewTokenStore(path, "fresh-admin")
	if err != nil {
		t.Fatal(err)
	}
	if st.AdminToken() != "fresh-admin" {
		t.Fatal("admin_token must come from config, not file")
	}
	info := st.Validate("tok_abc")
	if info == nil || info.Role != "rw" {
		t.Fatalf("missing role must default to rw, got %+v", info)
	}
}

func TestValidateRejectsDisabledAndUnknown(t *testing.T) {
	path := filepath.Join(t.TempDir(), "tokens.json")
	seed := `{"tokens": {"tok_off": {"name": "n", "created_at": "x", "last_used": null, "enabled": false, "role": "rw"}}, "admin_token": ""}`
	if err := os.WriteFile(path, []byte(seed), 0o600); err != nil {
		t.Fatal(err)
	}
	st, err := NewTokenStore(path, "a")
	if err != nil {
		t.Fatal(err)
	}
	if st.Validate("tok_off") != nil {
		t.Fatal("disabled token must be rejected")
	}
	if st.Validate("tok_nope") != nil {
		t.Fatal("unknown token must be rejected")
	}
}

func TestValidateUpdatesLastUsedInMemoryFlushPersists(t *testing.T) {
	path := filepath.Join(t.TempDir(), "tokens.json")
	seed := `{"tokens": {"tok_a": {"name": "n", "created_at": "x", "last_used": null, "enabled": true, "role": "ro"}}, "admin_token": ""}`
	if err := os.WriteFile(path, []byte(seed), 0o600); err != nil {
		t.Fatal(err)
	}
	st, err := NewTokenStore(path, "a")
	if err != nil {
		t.Fatal(err)
	}
	if info := st.Validate("tok_a"); info == nil || info.LastUsed == nil {
		t.Fatal("last_used must be set after Validate")
	}
	// Disk copy untouched until Flush.
	raw, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	var preDisk struct {
		Tokens map[string]TokenInfo `json:"tokens"`
	}
	if err := json.Unmarshal(raw, &preDisk); err != nil {
		t.Fatal(err)
	}
	if preDisk.Tokens["tok_a"].LastUsed != nil {
		t.Fatal("disk must still have last_used null before Flush")
	}
	if err := st.Flush(); err != nil {
		t.Fatal(err)
	}
	raw, err = os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	var disk struct {
		Tokens map[string]TokenInfo `json:"tokens"`
	}
	if err := json.Unmarshal(raw, &disk); err != nil {
		t.Fatal(err)
	}
	if disk.Tokens["tok_a"].LastUsed == nil {
		t.Fatal("Flush must persist last_used")
	}
	st2, err := NewTokenStore(path, "a")
	if err != nil {
		t.Fatal(err)
	}
	if st2.Validate("tok_a") == nil {
		t.Fatal("round-trip load must keep token valid")
	}
}

func TestFlushSetsRestrictivePerms(t *testing.T) {
	path := filepath.Join(t.TempDir(), "tokens.json")
	st, err := NewTokenStore(path, "a")
	if err != nil {
		t.Fatal(err)
	}
	if err := st.Flush(); err != nil {
		t.Fatal(err)
	}
	fi, err := os.Stat(path)
	if err != nil {
		t.Fatal(err)
	}
	if fi.Mode().Perm() != 0o600 {
		t.Fatalf("perm = %o, want 600", fi.Mode().Perm())
	}
}

func TestAddEphemeralToken(t *testing.T) {
	path := filepath.Join(t.TempDir(), "tokens.json")
	st, err := NewTokenStore(path, "a")
	if err != nil {
		t.Fatal(err)
	}
	st.AddEphemeral("tok_temp123", "temp-rw", "rw")
	info := st.Validate("tok_temp123")
	if info == nil || info.Role != "rw" || info.Name != "temp-rw" {
		t.Fatalf("ephemeral token broken: %+v", info)
	}
	if info.CreatedAt != "ephemeral" {
		t.Fatalf("created_at = %q, want ephemeral", info.CreatedAt)
	}
}

func TestGenerateTokenShape(t *testing.T) {
	tok, err := GenerateToken()
	if err != nil {
		t.Fatal(err)
	}
	if !strings.HasPrefix(tok, "tok_") || len(tok) != 4+32 {
		t.Fatalf("token shape wrong: %q", tok)
	}
}

func TestCreateRevokeList(t *testing.T) {
	path := filepath.Join(t.TempDir(), "tokens.json")
	s, err := NewTokenStore(path, "admin")
	if err != nil {
		t.Fatal(err)
	}
	tok, err := s.CreateToken("ci", "rw")
	if err != nil || !strings.HasPrefix(tok, "tok_") {
		t.Fatalf("create: %q %v", tok, err)
	}
	if s.Validate(tok) == nil {
		t.Fatal("created token must validate")
	}
	list := s.ListTokens()
	if info, ok := list[tok]; !ok || info.Role != "rw" || info.Name != "ci" {
		t.Fatalf("list wrong: %+v", list)
	}
	if !s.RevokeToken(tok) {
		t.Fatal("revoke must succeed")
	}
	if s.RevokeToken(tok) {
		t.Fatal("second revoke must fail")
	}
	if s.Validate(tok) != nil {
		t.Fatal("revoked token must not validate")
	}
}

func TestCreateTokenBadRole(t *testing.T) {
	s, _ := NewTokenStore(filepath.Join(t.TempDir(), "t.json"), "admin")
	if _, err := s.CreateToken("x", "superuser"); err == nil {
		t.Fatal("bad role must error")
	}
}
