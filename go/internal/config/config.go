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

	ReadFileDefaultLimit int
	ReadFileMaxLimit     int
	ReadFileMaxLineBytes int

	GlobMaxResults        int
	GrepDefaultMaxResults int
	GrepMaxResults        int

	AuditLogDir      string
	ShutdownGraceSec int

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

// ParseBool parses the pydantic-accepted boolean spellings, case-insensitively.
// Reserved for M2 fields (audit_enabled etc.); exported now so semantics are
// pinned by tests from the start.
func ParseBool(key, v string) (bool, error) {
	switch strings.ToLower(strings.TrimSpace(v)) {
	case "true", "1", "yes", "on":
		return true, nil
	case "false", "0", "no", "off":
		return false, nil
	}
	return false, fmt.Errorf("%s: invalid boolean %q", key, v)
}
