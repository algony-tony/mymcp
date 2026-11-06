package config

import (
	"bufio"
	"os"
	"path/filepath"
	"strconv"
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

func TestLoadM3aDefaults(t *testing.T) {
	for _, k := range []string{
		"MYMCP_TRANSFER_ENABLED", "MYMCP_TRANSFER_MAX_BYTES", "MYMCP_TRANSFER_DEFAULT_TTL_SEC",
		"MYMCP_TRANSFER_MAX_TTL_SEC", "MYMCP_PUBLIC_BASE_URL", "MYMCP_RECORDER_DATA_DIR",
	} {
		t.Setenv(k, "")
		os.Unsetenv(k)
	}
	cfg, err := Load()
	if err != nil {
		t.Fatal(err)
	}
	if !cfg.TransferEnabled {
		t.Fatal("TransferEnabled default must be true")
	}
	if cfg.TransferMaxBytes != 2*1024*1024*1024 {
		t.Fatalf("TransferMaxBytes = %d", cfg.TransferMaxBytes)
	}
	if cfg.TransferDefaultTTLSec != 300 || cfg.TransferMaxTTLSec != 900 {
		t.Fatalf("ttl defaults wrong: %d %d", cfg.TransferDefaultTTLSec, cfg.TransferMaxTTLSec)
	}
	if cfg.PublicBaseURL != "" {
		t.Fatalf("PublicBaseURL default = %q", cfg.PublicBaseURL)
	}
	if cfg.RecorderDataDir != "/var/lib/mymcp/recorder" {
		t.Fatalf("RecorderDataDir = %q", cfg.RecorderDataDir)
	}
}

func TestRecorderMergeIntervalSec(t *testing.T) {
	cases := []struct {
		name string
		env  string
		want int
	}{
		{"default", "", 300},
		{"override", "60", 60},
		{"zero falls back", "0", 300},
		{"negative falls back", "-5", 300},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			if tc.env == "" {
				t.Setenv("MYMCP_RECORDER_MERGE_INTERVAL_SEC", "")
				os.Unsetenv("MYMCP_RECORDER_MERGE_INTERVAL_SEC")
			} else {
				t.Setenv("MYMCP_RECORDER_MERGE_INTERVAL_SEC", tc.env)
			}
			cfg, err := Load()
			if err != nil {
				t.Fatal(err)
			}
			if cfg.RecorderMergeIntervalSec != tc.want {
				t.Fatalf("got %d, want %d", cfg.RecorderMergeIntervalSec, tc.want)
			}
		})
	}
}

func TestEnvExampleMatchesGoDefaults(t *testing.T) {
	dir, err := os.Getwd()
	if err != nil {
		t.Fatal(err)
	}
	var envPath string
	for i := 0; i < 5; i++ {
		candidate := filepath.Join(dir, ".env.example")
		if _, err := os.Stat(candidate); err == nil {
			envPath = candidate
			break
		}
		parent := filepath.Dir(dir)
		if parent == dir {
			break
		}
		dir = parent
	}
	if envPath == "" {
		t.Fatal(".env.example not found")
	}

	raw, err := os.ReadFile(envPath)
	if err != nil {
		t.Fatal(err)
	}

	envVars := make(map[string]string)
	scanner := bufio.NewScanner(strings.NewReader(string(raw)))
	for scanner.Scan() {
		line := strings.TrimSpace(scanner.Text())
		if line == "" {
			continue
		}
		if strings.HasPrefix(line, "#") {
			line = strings.TrimSpace(strings.TrimPrefix(line, "#"))
		}
		if idx := strings.Index(line, "="); idx != -1 {
			k := strings.TrimSpace(line[:idx])
			v := strings.TrimSpace(line[idx+1:])
			if strings.HasPrefix(k, "MYMCP_") {
				if commentIdx := strings.Index(v, "#"); commentIdx != -1 {
					v = strings.TrimSpace(v[:commentIdx])
				}
				envVars[k] = v
			}
		}
	}

	// Clean environment to ensure we inspect true defaults
	for k := range envVars {
		t.Setenv(k, "")
		os.Unsetenv(k)
	}

	cfg, err := Load()
	if err != nil {
		t.Fatal(err)
	}

	checks := []struct {
		key  string
		want string
	}{
		{"MYMCP_HOST", cfg.Host},
		{"MYMCP_PORT", strconv.Itoa(cfg.Port)},
		{"MYMCP_TLS_CERT", cfg.TLSCert},
		{"MYMCP_TLS_KEY", cfg.TLSKey},
		{"MYMCP_TRUSTED_PROXIES", cfg.TrustedProxies},
		{"MYMCP_TOKEN_FILE", cfg.TokenFile},
		{"MYMCP_AUDIT_LOG_DIR", cfg.AuditLogDir},
		{"MYMCP_AUDIT_ENABLED", strconv.FormatBool(cfg.AuditEnabled)},
		{"MYMCP_AUDIT_MAX_BYTES", strconv.FormatInt(cfg.AuditMaxBytes, 10)},
		{"MYMCP_AUDIT_BACKUP_COUNT", strconv.Itoa(cfg.AuditBackupCount)},
		{"MYMCP_AUDIT_OUTPUT_BASH_HEAD_BYTES", strconv.Itoa(cfg.AuditOutputBashHeadBytes)},
		{"MYMCP_AUDIT_OUTPUT_BASH_TAIL_BYTES", strconv.Itoa(cfg.AuditOutputBashTailBytes)},
		{"MYMCP_BASH_MAX_OUTPUT_BYTES", strconv.Itoa(cfg.BashMaxOutputBytes)},
		{"MYMCP_BASH_MAX_OUTPUT_BYTES_HARD", strconv.Itoa(cfg.BashMaxOutputBytesHard)},
		{"MYMCP_READ_FILE_DEFAULT_LIMIT", strconv.Itoa(cfg.ReadFileDefaultLimit)},
		{"MYMCP_READ_FILE_MAX_LIMIT", strconv.Itoa(cfg.ReadFileMaxLimit)},
		{"MYMCP_READ_FILE_MAX_LINE_BYTES", strconv.Itoa(cfg.ReadFileMaxLineBytes)},
		{"MYMCP_WRITE_FILE_MAX_BYTES", strconv.Itoa(cfg.WriteFileMaxBytes)},
		{"MYMCP_EDIT_STRING_MAX_BYTES", strconv.Itoa(cfg.EditStringMaxBytes)},
		{"MYMCP_GLOB_MAX_RESULTS", strconv.Itoa(cfg.GlobMaxResults)},
		{"MYMCP_GREP_DEFAULT_MAX_RESULTS", strconv.Itoa(cfg.GrepDefaultMaxResults)},
		{"MYMCP_GREP_MAX_RESULTS", strconv.Itoa(cfg.GrepMaxResults)},
		{"MYMCP_SHUTDOWN_GRACE_SEC", strconv.Itoa(cfg.ShutdownGraceSec)},
		{"MYMCP_TRANSFER_ENABLED", strconv.FormatBool(cfg.TransferEnabled)},
		{"MYMCP_TRANSFER_DEFAULT_TTL_SEC", strconv.Itoa(cfg.TransferDefaultTTLSec)},
		{"MYMCP_TRANSFER_MAX_TTL_SEC", strconv.Itoa(cfg.TransferMaxTTLSec)},
		{"MYMCP_TRANSFER_MAX_BYTES", strconv.FormatInt(cfg.TransferMaxBytes, 10)},
		{"MYMCP_RECORDER_DATA_DIR", cfg.RecorderDataDir},
		{"MYMCP_RECORDER_MERGE_INTERVAL_SEC", strconv.Itoa(cfg.RecorderMergeIntervalSec)},
	}

	for _, tc := range checks {
		got, ok := envVars[tc.key]
		if !ok {
			t.Errorf(".env.example missing key %s", tc.key)
		} else if got != tc.want {
			t.Errorf(".env.example default for %s = %q, want %q", tc.key, got, tc.want)
		}
	}
}

func TestConfigTLSAndTrustedProxies(t *testing.T) {
	t.Setenv("MYMCP_TLS_CERT", "/etc/ssl/cert.pem")
	t.Setenv("MYMCP_TLS_KEY", "/etc/ssl/key.pem")
	t.Setenv("MYMCP_TRUSTED_PROXIES", "127.0.0.1/32, 10.0.0.0/8, ::1/128")

	cfg, err := Load()
	if err != nil {
		t.Fatalf("Load: %v", err)
	}
	if cfg.TLSCert != "/etc/ssl/cert.pem" || cfg.TLSKey != "/etc/ssl/key.pem" {
		t.Errorf("got cert=%q key=%q", cfg.TLSCert, cfg.TLSKey)
	}
	if !cfg.IsTLS() {
		t.Errorf("expected IsTLS() == true")
	}

	nets, err := cfg.ParsedTrustedProxies()
	if err != nil {
		t.Fatalf("ParsedTrustedProxies: %v", err)
	}
	if len(nets) != 3 {
		t.Fatalf("expected 3 nets, got %d", len(nets))
	}

	// Incomplete TLS configuration must fail
	t.Setenv("MYMCP_TLS_CERT", "/etc/ssl/cert.pem")
	t.Setenv("MYMCP_TLS_KEY", "")
	if _, err := Load(); err == nil {
		t.Errorf("expected error when only MYMCP_TLS_CERT is provided")
	}
	t.Setenv("MYMCP_TLS_CERT", "")
	t.Setenv("MYMCP_TLS_KEY", "/etc/ssl/key.pem")
	if _, err := Load(); err == nil {
		t.Errorf("expected error when only MYMCP_TLS_KEY is provided")
	}

	// Bare IPs and empty string handling
	cfgBare := &Config{TrustedProxies: "127.0.0.1, ::1, "}
	bareNets, err := cfgBare.ParsedTrustedProxies()
	if err != nil {
		t.Fatalf("ParsedTrustedProxies bare IPs: %v", err)
	}
	if len(bareNets) != 2 {
		t.Fatalf("expected 2 nets, got %d", len(bareNets))
	}

	// Empty trusted proxies
	cfgEmpty := &Config{TrustedProxies: "  "}
	emptyNets, err := cfgEmpty.ParsedTrustedProxies()
	if err != nil {
		t.Fatalf("ParsedTrustedProxies empty: %v", err)
	}
	if len(emptyNets) != 0 {
		t.Fatalf("expected 0 nets, got %d", len(emptyNets))
	}

	// Invalid trusted proxy IP
	cfgInvalid := &Config{TrustedProxies: "not-an-ip"}
	if _, err := cfgInvalid.ParsedTrustedProxies(); err == nil {
		t.Errorf("expected error for invalid IP")
	}

	// Invalid trusted proxy CIDR
	cfgInvalidCIDR := &Config{TrustedProxies: "127.0.0.1/99"}
	if _, err := cfgInvalidCIDR.ParsedTrustedProxies(); err == nil {
		t.Errorf("expected error for invalid CIDR")
	}

	// IsTLS false when empty
	cfgNoTLS := &Config{}
	if cfgNoTLS.IsTLS() {
		t.Errorf("expected IsTLS() == false")
	}
}
