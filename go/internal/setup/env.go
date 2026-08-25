package setup

import (
	_ "embed"
	"fmt"
	"sort"
	"strings"
	"text/template"
)

//go:embed templates/env.tmpl
var envTemplate string

// envMarker heads the block where MergeEnv appends keys that the existing
// file did not already define. It is created once and reused on re-runs.
const envMarker = "# --- written by mymcp init ---"

type envFields struct {
	Bind, LogDir, TokenFile, MetricsToken string
	Port                                  int
	AdminToken                            string
	AuditEnabled                          bool
	RecorderEnabled                       bool
	RecorderDataDir                       string
	RecorderProvider                      string
	RecorderModel                         string
	RecorderAPIKey                        string
}

func fieldsFor(p *Plan, adminToken string) envFields {
	return envFields{
		Bind:             p.Bind,
		Port:             p.Port,
		AdminToken:       adminToken,
		TokenFile:        p.TokenPath(),
		LogDir:           p.LogDir,
		MetricsToken:     p.MetricsToken,
		AuditEnabled:     p.AuditEnabled,
		RecorderEnabled:  p.Recorder.Enabled,
		RecorderDataDir:  p.RecorderDataDir,
		RecorderProvider: p.Recorder.Provider,
		RecorderModel:    p.Recorder.Model,
		RecorderAPIKey:   p.Recorder.APIKey,
	}
}

// RenderEnv produces a complete, commented .env for a fresh install.
func RenderEnv(p *Plan, adminToken string) string {
	t := template.Must(template.New("env").Parse(envTemplate))
	var sb strings.Builder
	if err := t.Execute(&sb, fieldsFor(p, adminToken)); err != nil {
		panic(fmt.Sprintf("bad embedded env template: %v", err))
	}
	return sb.String()
}

// OwnedKeys are the only keys MergeEnv will rewrite in an existing file.
func OwnedKeys(p *Plan, adminToken string) map[string]string {
	m := map[string]string{
		"MYMCP_HOST":              p.Bind,
		"MYMCP_PORT":              fmt.Sprintf("%d", p.Port),
		"MYMCP_ADMIN_TOKEN":       adminToken,
		"MYMCP_TOKEN_FILE":        p.TokenPath(),
		"MYMCP_AUDIT_LOG_DIR":     p.LogDir,
		"MYMCP_AUDIT_ENABLED":     fmt.Sprintf("%t", p.AuditEnabled),
		"MYMCP_METRICS_TOKEN":     p.MetricsToken,
		"MYMCP_RECORDER_ENABLED":  fmt.Sprintf("%t", p.Recorder.Enabled),
		"MYMCP_RECORDER_DATA_DIR": p.RecorderDataDir,
	}
	if p.Recorder.Enabled {
		m["MYMCP_RECORDER_LLM_PROVIDER"] = p.Recorder.Provider
		m["MYMCP_RECORDER_LLM_MODEL"] = p.Recorder.Model
		m["MYMCP_RECORDER_LLM_API_KEY"] = p.Recorder.APIKey
	}
	return m
}

// keyOf returns the key of an uncommented "KEY=value" line, else "".
func keyOf(line string) string {
	t := strings.TrimSpace(line)
	if t == "" || strings.HasPrefix(t, "#") {
		return ""
	}
	k, _, ok := strings.Cut(t, "=")
	if !ok {
		return ""
	}
	return strings.TrimSpace(k)
}

// MergeEnv rewrites owned keys in place and appends the rest under envMarker,
// preserving every other line — comments, blanks, and unowned user keys —
// byte-for-byte in its original order.
func MergeEnv(existing string, owned map[string]string) string {
	lines := strings.Split(existing, "\n")
	seen := map[string]bool{}
	for i, line := range lines {
		k := keyOf(line)
		if k == "" {
			continue
		}
		if v, ok := owned[k]; ok {
			lines[i] = k + "=" + v
			seen[k] = true
		}
	}
	var missing []string
	for k := range owned {
		if !seen[k] {
			missing = append(missing, k)
		}
	}
	sort.Strings(missing) // deterministic output for tests and diffs
	out := strings.Join(lines, "\n")
	if len(missing) == 0 {
		return out
	}
	if !strings.HasSuffix(out, "\n") {
		out += "\n"
	}
	if !strings.Contains(out, envMarker) {
		out += "\n" + envMarker + "\n"
	}
	for _, k := range missing {
		out += k + "=" + owned[k] + "\n"
	}
	return out
}

// ExistingAdminToken returns a non-empty uncommented MYMCP_ADMIN_TOKEN value,
// else "". Re-running init must never regenerate a live admin token.
func ExistingAdminToken(existing string) string {
	for _, line := range strings.Split(existing, "\n") {
		if keyOf(line) != "MYMCP_ADMIN_TOKEN" {
			continue
		}
		_, v, _ := strings.Cut(strings.TrimSpace(line), "=")
		if v = strings.TrimSpace(v); v != "" {
			return v
		}
	}
	return ""
}
