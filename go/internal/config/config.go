// Package config loads MYMCP_* settings from the process environment and an
// optional .env file, with semantics matching the Python core's
// pydantic-settings usage (src/mymcp/config.py): process env beats .env;
// discovery order MYMCP_ENV_FILE, /etc/mymcp/.env, ./.env.
package config

import (
	"bufio"
	"fmt"
	"os"
	"strconv"
	"strings"
)

type Config struct {
	Host       string
	Port       int
	AdminToken string
	TokenFile  string

	MetricsToken string

	BashMaxOutputBytes     int
	BashMaxOutputBytesHard int
	WriteFileMaxBytes      int
	EditStringMaxBytes     int

	ReadFileDefaultLimit int
	ReadFileMaxLimit     int
	ReadFileMaxLineBytes int

	GlobMaxResults        int
	GrepDefaultMaxResults int
	GrepMaxResults        int

	AuditLogDir      string
	ShutdownGraceSec int

	AuditEnabled     bool
	AuditMaxBytes    int64
	AuditBackupCount int

	AuditOutputBashHeadBytes int
	AuditOutputBashTailBytes int

	TransferEnabled       bool
	TransferMaxBytes      int64
	TransferDefaultTTLSec int
	TransferMaxTTLSec     int
	PublicBaseURL         string
	RecorderDataDir       string
	// RecorderMergeIntervalSec mirrors the sidecar's
	// MYMCP_RECORDER_MERGE_INTERVAL_SEC (both processes read the same .env).
	// The core uses it only to derive the overview staleness threshold.
	RecorderMergeIntervalSec int

	protectedPathsCSV string
}

// Load reads configuration. Values resolve as: process env > .env file > default.
// Load re-reads the environment and .env file on every call (no caching, unlike
// Python's get_settings) — call it once at startup and pass *Config down.
func Load() (*Config, error) {
	fileVals := map[string]string{}
	if envFile := discoverEnvFile(); envFile != "" {
		var err error
		fileVals, err = parseEnvFile(envFile)
		if err != nil {
			return nil, err
		}
	}
	get := func(key string) (string, bool) {
		if v, ok := os.LookupEnv(key); ok {
			return v, true
		}
		if v, ok := fileVals[key]; ok {
			return v, true
		}
		return "", false
	}

	cfg := &Config{}
	var err error
	cfg.Host = getStr(get, "MYMCP_HOST", "0.0.0.0")
	if cfg.Port, err = getInt(get, "MYMCP_PORT", 8765); err != nil {
		return nil, err
	}
	cfg.AdminToken = getStr(get, "MYMCP_ADMIN_TOKEN", "")
	cfg.TokenFile = getStr(get, "MYMCP_TOKEN_FILE", "/etc/mymcp/tokens.json")

	cfg.MetricsToken = getStr(get, "MYMCP_METRICS_TOKEN", "")
	if cfg.BashMaxOutputBytes, err = getInt(get, "MYMCP_BASH_MAX_OUTPUT_BYTES", 102400); err != nil {
		return nil, err
	}
	if cfg.BashMaxOutputBytesHard, err = getInt(get, "MYMCP_BASH_MAX_OUTPUT_BYTES_HARD", 1048576); err != nil {
		return nil, err
	}
	if cfg.WriteFileMaxBytes, err = getInt(get, "MYMCP_WRITE_FILE_MAX_BYTES", 10*1024*1024); err != nil {
		return nil, err
	}
	if cfg.EditStringMaxBytes, err = getInt(get, "MYMCP_EDIT_STRING_MAX_BYTES", 1024*1024); err != nil {
		return nil, err
	}

	if cfg.ReadFileDefaultLimit, err = getInt(get, "MYMCP_READ_FILE_DEFAULT_LIMIT", 2000); err != nil {
		return nil, err
	}
	if cfg.ReadFileMaxLimit, err = getInt(get, "MYMCP_READ_FILE_MAX_LIMIT", 50000); err != nil {
		return nil, err
	}
	if cfg.ReadFileMaxLineBytes, err = getInt(get, "MYMCP_READ_FILE_MAX_LINE_BYTES", 32768); err != nil {
		return nil, err
	}
	if cfg.GlobMaxResults, err = getInt(get, "MYMCP_GLOB_MAX_RESULTS", 1000); err != nil {
		return nil, err
	}
	if cfg.GrepDefaultMaxResults, err = getInt(get, "MYMCP_GREP_DEFAULT_MAX_RESULTS", 500); err != nil {
		return nil, err
	}
	if cfg.GrepMaxResults, err = getInt(get, "MYMCP_GREP_MAX_RESULTS", 5000); err != nil {
		return nil, err
	}
	cfg.AuditLogDir = getStr(get, "MYMCP_AUDIT_LOG_DIR", "/var/log/mymcp")
	if cfg.ShutdownGraceSec, err = getInt(get, "MYMCP_SHUTDOWN_GRACE_SEC", 5); err != nil {
		return nil, err
	}
	if cfg.AuditEnabled, err = getBool(get, "MYMCP_AUDIT_ENABLED", false); err != nil {
		return nil, err
	}
	auditMax, err := getInt(get, "MYMCP_AUDIT_MAX_BYTES", 10*1024*1024)
	if err != nil {
		return nil, err
	}
	cfg.AuditMaxBytes = int64(auditMax)
	if cfg.AuditBackupCount, err = getInt(get, "MYMCP_AUDIT_BACKUP_COUNT", 5); err != nil {
		return nil, err
	}
	if cfg.AuditOutputBashHeadBytes, err = getInt(get, "MYMCP_AUDIT_OUTPUT_BASH_HEAD_BYTES", 4096); err != nil {
		return nil, err
	}
	if cfg.AuditOutputBashTailBytes, err = getInt(get, "MYMCP_AUDIT_OUTPUT_BASH_TAIL_BYTES", 4096); err != nil {
		return nil, err
	}
	if cfg.TransferEnabled, err = getBool(get, "MYMCP_TRANSFER_ENABLED", true); err != nil {
		return nil, err
	}
	transferMax, err := getInt(get, "MYMCP_TRANSFER_MAX_BYTES", 2*1024*1024*1024)
	if err != nil {
		return nil, err
	}
	cfg.TransferMaxBytes = int64(transferMax)
	if cfg.TransferDefaultTTLSec, err = getInt(get, "MYMCP_TRANSFER_DEFAULT_TTL_SEC", 300); err != nil {
		return nil, err
	}
	if cfg.TransferMaxTTLSec, err = getInt(get, "MYMCP_TRANSFER_MAX_TTL_SEC", 900); err != nil {
		return nil, err
	}
	cfg.PublicBaseURL = getStr(get, "MYMCP_PUBLIC_BASE_URL", "")
	cfg.RecorderDataDir = getStr(get, "MYMCP_RECORDER_DATA_DIR", "/var/lib/mymcp/recorder")
	if cfg.RecorderMergeIntervalSec, err = getInt(get, "MYMCP_RECORDER_MERGE_INTERVAL_SEC", 300); err != nil {
		return nil, err
	}
	if cfg.RecorderMergeIntervalSec <= 0 {
		// A non-positive interval would make the staleness threshold 0 and flag
		// every overview; fall back rather than fail the whole server start.
		cfg.RecorderMergeIntervalSec = 300
	}
	cfg.protectedPathsCSV = getStr(get, "MYMCP_PROTECTED_PATHS", "")
	return cfg, nil
}

// ProtectedPaths returns the always-protected paths: the audit log dir plus
// comma-separated extras from MYMCP_PROTECTED_PATHS (trimmed, empties dropped).
func (c *Config) ProtectedPaths() []string {
	paths := []string{c.AuditLogDir}
	for _, p := range strings.Split(c.protectedPathsCSV, ",") {
		if t := strings.TrimSpace(p); t != "" {
			paths = append(paths, t)
		}
	}
	return paths
}

// DiscoveredEnvFile exposes env-file discovery for the temp-token decision.
func DiscoveredEnvFile() string { return discoverEnvFile() }

func discoverEnvFile() string {
	if explicit := os.Getenv("MYMCP_ENV_FILE"); explicit != "" {
		if st, err := os.Stat(explicit); err == nil && !st.IsDir() {
			return explicit
		}
	}
	for _, candidate := range []string{"/etc/mymcp/.env", ".env"} {
		if st, err := os.Stat(candidate); err == nil && !st.IsDir() {
			return candidate
		}
	}
	return ""
}

// parseEnvFile reads KEY=VALUE lines; ignores blanks and # comments; strips
// one level of matching single or double quotes around the value.
// Inline comments (KEY=VALUE # remark) are NOT stripped; production files
// must use plain KEY=VALUE lines.
func parseEnvFile(path string) (map[string]string, error) {
	f, err := os.Open(path)
	if err != nil {
		return nil, fmt.Errorf("open env file %s: %w", path, err)
	}
	defer f.Close()
	vals := map[string]string{}
	sc := bufio.NewScanner(f)
	sc.Buffer(make([]byte, 0, 64*1024), 1024*1024)
	for sc.Scan() {
		line := strings.TrimSpace(sc.Text())
		if line == "" || strings.HasPrefix(line, "#") {
			continue
		}
		k, v, ok := strings.Cut(line, "=")
		if !ok {
			continue
		}
		k = strings.TrimSpace(k)
		v = strings.TrimSpace(v)
		if len(v) >= 2 {
			if (v[0] == '"' && v[len(v)-1] == '"') || (v[0] == '\'' && v[len(v)-1] == '\'') {
				v = v[1 : len(v)-1]
			}
		}
		vals[k] = v
	}
	return vals, sc.Err()
}

type getter func(key string) (string, bool)

func getStr(get getter, key, def string) string {
	if v, ok := get(key); ok {
		return v
	}
	return def
}

func getInt(get getter, key string, def int) (int, error) {
	v, ok := get(key)
	if !ok {
		return def, nil
	}
	n, err := strconv.Atoi(strings.TrimSpace(v))
	if err != nil {
		return 0, fmt.Errorf("%s: invalid integer %q", key, v)
	}
	return n, nil
}

func getBool(get getter, key string, def bool) (bool, error) {
	v, ok := get(key)
	if !ok {
		return def, nil
	}
	return ParseBool(key, v)
}

// ParseBool parses the pydantic-accepted boolean spellings, case-insensitively.
func ParseBool(key, v string) (bool, error) {
	switch strings.ToLower(strings.TrimSpace(v)) {
	case "true", "1", "yes", "on":
		return true, nil
	case "false", "0", "no", "off":
		return false, nil
	}
	return false, fmt.Errorf("%s: invalid boolean %q", key, v)
}
