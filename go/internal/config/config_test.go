package config

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

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
	t.Setenv("MYMCP_PORT", "9999")
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
	t.Setenv("MYMCP_ENV_FILE", envPath)
	// Process env must beat the file.
	t.Setenv("MYMCP_PORT", "7001")
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
	t.Setenv("MYMCP_ENV_FILE", envPath)
	cfg, err := Load()
	if err != nil {
		t.Fatal(err)
	}
	if cfg.AdminToken != "quoted" || cfg.Host != "single" {
		t.Fatalf("quote stripping failed: %+v", cfg)
	}
}

func TestBadIntNamesVariable(t *testing.T) {
	t.Setenv("MYMCP_PORT", "not-a-number")
	_, err := Load()
	if err == nil {
		t.Fatal("expected error")
	}
	if got := err.Error(); !strings.Contains(got, "MYMCP_PORT") {
		t.Fatalf("error should name the variable, got %q", got)
	}
}

func TestProtectedPathsComposition(t *testing.T) {
	t.Setenv("MYMCP_AUDIT_LOG_DIR", "/var/log/x")
	t.Setenv("MYMCP_PROTECTED_PATHS", " /etc/secret , ,/opt/keys ")
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

func TestLoadM2Defaults(t *testing.T) {
	// Ensure a clean env: none of the M2 vars set.
	for _, k := range []string{
		"MYMCP_METRICS_TOKEN", "MYMCP_AUDIT_ENABLED", "MYMCP_AUDIT_MAX_BYTES",
		"MYMCP_AUDIT_BACKUP_COUNT", "MYMCP_BASH_MAX_OUTPUT_BYTES",
		"MYMCP_BASH_MAX_OUTPUT_BYTES_HARD", "MYMCP_WRITE_FILE_MAX_BYTES",
		"MYMCP_EDIT_STRING_MAX_BYTES", "MYMCP_AUDIT_OUTPUT_BASH_HEAD_BYTES",
		"MYMCP_AUDIT_OUTPUT_BASH_TAIL_BYTES",
	} {
		t.Setenv(k, "") // t.Setenv restores afterwards; set-then-unset below
		os.Unsetenv(k)
	}
	cfg, err := Load()
	if err != nil {
		t.Fatal(err)
	}
	if cfg.MetricsToken != "" {
		t.Fatalf("MetricsToken default = %q", cfg.MetricsToken)
	}
	if cfg.AuditEnabled {
		t.Fatal("AuditEnabled default must be false")
	}
	if cfg.AuditMaxBytes != 10*1024*1024 {
		t.Fatalf("AuditMaxBytes = %d", cfg.AuditMaxBytes)
	}
	if cfg.AuditBackupCount != 5 {
		t.Fatalf("AuditBackupCount = %d", cfg.AuditBackupCount)
	}
	if cfg.BashMaxOutputBytes != 102400 || cfg.BashMaxOutputBytesHard != 1048576 {
		t.Fatalf("bash byte defaults wrong: %d %d", cfg.BashMaxOutputBytes, cfg.BashMaxOutputBytesHard)
	}
	if cfg.WriteFileMaxBytes != 10*1024*1024 || cfg.EditStringMaxBytes != 1024*1024 {
		t.Fatalf("write/edit defaults wrong: %d %d", cfg.WriteFileMaxBytes, cfg.EditStringMaxBytes)
	}
	if cfg.AuditOutputBashHeadBytes != 4096 || cfg.AuditOutputBashTailBytes != 4096 {
		t.Fatalf("audit output defaults wrong: %d %d", cfg.AuditOutputBashHeadBytes, cfg.AuditOutputBashTailBytes)
	}
}

func TestLoadAuditEnabledBoolSpellings(t *testing.T) {
	for _, v := range []string{"true", "1", "yes", "on", "TRUE", "On"} {
		t.Setenv("MYMCP_AUDIT_ENABLED", v)
		cfg, err := Load()
		if err != nil || !cfg.AuditEnabled {
			t.Fatalf("%q should parse true (err=%v)", v, err)
		}
	}
	for _, v := range []string{"false", "0", "no", "off"} {
		t.Setenv("MYMCP_AUDIT_ENABLED", v)
		cfg, err := Load()
		if err != nil || cfg.AuditEnabled {
			t.Fatalf("%q should parse false (err=%v)", v, err)
		}
	}
	t.Setenv("MYMCP_AUDIT_ENABLED", "maybe")
	if _, err := Load(); err == nil {
		t.Fatal("invalid bool must error naming the variable")
	}
}
