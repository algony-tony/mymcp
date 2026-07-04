package config

import (
	"os"
	"path/filepath"
	"testing"
)

// setEnv sets an env var for the test duration.
func setEnv(t *testing.T, k, v string) {
	t.Helper()
	t.Setenv(k, v)
}

func TestDefaults(t *testing.T) {
	cfg, err := Load()
	if err != nil {
		t.Fatal(err)
	}
	if cfg.Host != "0.0.0.0" || cfg.Port != 8765 {
		t.Fatalf("host/port = %q/%d", cfg.Host, cfg.Port)
	}
	if cfg.ReadFileDefaultLimit != 2000 || cfg.ReadFileMaxLimit != 50000 ||
		cfg.ReadFileMaxLineBytes != 32768 {
		t.Fatalf("read_file limits wrong: %+v", cfg)
	}
	if cfg.GlobMaxResults != 1000 || cfg.GrepDefaultMaxResults != 500 || cfg.GrepMaxResults != 5000 {
		t.Fatalf("glob/grep limits wrong: %+v", cfg)
	}
	if cfg.TokenFile != "/etc/mymcp/tokens.json" || cfg.AuditLogDir != "/var/log/mymcp" {
		t.Fatalf("paths wrong: %+v", cfg)
	}
	if cfg.ShutdownGraceSec != 5 {
		t.Fatalf("shutdown grace = %d", cfg.ShutdownGraceSec)
	}
}

func TestEnvOverridesDefault(t *testing.T) {
	setEnv(t, "MYMCP_PORT", "9999")
	cfg, err := Load()
	if err != nil {
		t.Fatal(err)
	}
	if cfg.Port != 9999 {
		t.Fatalf("port = %d, want 9999", cfg.Port)
	}
}

func TestEnvFileDiscoveryAndPrecedence(t *testing.T) {
	dir := t.TempDir()
	envPath := filepath.Join(dir, "custom.env")
	content := "MYMCP_PORT=7000\nMYMCP_HOST=127.0.0.1\n# comment\n\nMYMCP_ADMIN_TOKEN=fromfile\n"
	if err := os.WriteFile(envPath, []byte(content), 0o600); err != nil {
		t.Fatal(err)
	}
	setEnv(t, "MYMCP_ENV_FILE", envPath)
	// Process env must beat the file.
	setEnv(t, "MYMCP_PORT", "7001")
	cfg, err := Load()
	if err != nil {
		t.Fatal(err)
	}
	if cfg.Port != 7001 {
		t.Fatalf("process env should win: port = %d, want 7001", cfg.Port)
	}
	if cfg.Host != "127.0.0.1" || cfg.AdminToken != "fromfile" {
		t.Fatalf("file values not applied: %+v", cfg)
	}
}

func TestEnvFileQuotedValues(t *testing.T) {
	dir := t.TempDir()
	envPath := filepath.Join(dir, ".env")
	content := "MYMCP_ADMIN_TOKEN=\"quoted\"\nMYMCP_HOST='single'\n"
	if err := os.WriteFile(envPath, []byte(content), 0o600); err != nil {
		t.Fatal(err)
	}
	setEnv(t, "MYMCP_ENV_FILE", envPath)
	cfg, err := Load()
	if err != nil {
		t.Fatal(err)
	}
	if cfg.AdminToken != "quoted" || cfg.Host != "single" {
		t.Fatalf("quote stripping failed: %+v", cfg)
	}
}

func TestBadIntNamesVariable(t *testing.T) {
	setEnv(t, "MYMCP_PORT", "not-a-number")
	_, err := Load()
	if err == nil {
		t.Fatal("expected error")
	}
	if got := err.Error(); !contains(got, "MYMCP_PORT") {
		t.Fatalf("error should name the variable, got %q", got)
	}
}

func TestProtectedPathsComposition(t *testing.T) {
	setEnv(t, "MYMCP_AUDIT_LOG_DIR", "/var/log/x")
	setEnv(t, "MYMCP_PROTECTED_PATHS", " /etc/secret , ,/opt/keys ")
	cfg, err := Load()
	if err != nil {
		t.Fatal(err)
	}
	got := cfg.ProtectedPaths()
	want := []string{"/var/log/x", "/etc/secret", "/opt/keys"}
	if len(got) != len(want) {
		t.Fatalf("got %v, want %v", got, want)
	}
	for i := range want {
		if got[i] != want[i] {
			t.Fatalf("got %v, want %v", got, want)
		}
	}
}

func contains(s, sub string) bool {
	return len(s) >= len(sub) && (s == sub || len(sub) == 0 ||
		func() bool {
			for i := 0; i+len(sub) <= len(s); i++ {
				if s[i:i+len(sub)] == sub {
					return true
				}
			}
			return false
		}())
}

func TestParseBoolSpellings(t *testing.T) {
	for _, s := range []string{"true", "TRUE", "1", "yes", "On"} {
		v, err := ParseBool("K", s)
		if err != nil || !v {
			t.Fatalf("ParseBool(%q) = %v, %v", s, v, err)
		}
	}
	for _, s := range []string{"false", "0", "No", "OFF"} {
		v, err := ParseBool("K", s)
		if err != nil || v {
			t.Fatalf("ParseBool(%q) = %v, %v", s, v, err)
		}
	}
	if _, err := ParseBool("K", "maybe"); err == nil {
		t.Fatal("expected error for 'maybe'")
	}
}
