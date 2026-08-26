package setup

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"time"

	"github.com/algony-tony/mymcp/go/internal/auth"
)

type Severity int

const (
	SevOK Severity = iota
	SevWarn
	SevFail
)

func (s Severity) String() string {
	switch s {
	case SevOK:
		return "ok"
	case SevWarn:
		return "warn"
	default:
		return "fail"
	}
}

func (s Severity) MarshalJSON() ([]byte, error) { return json.Marshal(s.String()) }

func (s Severity) glyph() string {
	switch s {
	case SevOK:
		return "✓"
	case SevWarn:
		return "⚠"
	default:
		return "✗"
	}
}

type Check struct {
	Group    string   `json:"group"`
	Name     string   `json:"name"`
	Severity Severity `json:"severity"`
	Detail   string   `json:"detail,omitempty"`
	Remedy   string   `json:"remedy,omitempty"`
}

// Doctor runs every check. It never mutates the system: the only request it
// makes is a read-only tools/list.
func Doctor(configDir string, sys System) []Check {
	var checks []Check
	add := func(c Check) { checks = append(checks, c) }

	// --- INSTALL ---
	if path, err := sys.LookPath("mymcp"); err == nil {
		add(Check{Group: "INSTALL", Name: "binary", Severity: SevOK, Detail: path})
	} else {
		add(Check{Group: "INSTALL", Name: "binary", Severity: SevFail,
			Detail: "mymcp is not on PATH", Remedy: "pipx install algony-mymcp"})
	}
	if out, err := sys.Run("which", "-a", "mymcp"); err == nil {
		paths := nonEmptyLines(out)
		if len(paths) > 1 {
			add(Check{Group: "INSTALL", Name: "duplicate binaries", Severity: SevFail,
				Detail: strings.Join(paths, ", "),
				Remedy: "keep one copy; the unit's ExecStart decides which runs: pipx uninstall algony-mymcp"})
		} else {
			add(Check{Group: "INSTALL", Name: "duplicate binaries", Severity: SevOK, Detail: "1 copy"})
		}
	}
	if _, err := sys.LookPath("rg"); err != nil {
		add(Check{Group: "INSTALL", Name: "ripgrep", Severity: SevWarn,
			Detail: "not installed; grep falls back to a native scan",
			Remedy: "apt install -y ripgrep"})
	} else {
		add(Check{Group: "INSTALL", Name: "ripgrep", Severity: SevOK})
	}

	// --- CONFIG ---
	envPath := filepath.Join(configDir, ".env")
	raw, err := os.ReadFile(envPath)
	if err != nil {
		add(Check{Group: "CONFIG", Name: "env file", Severity: SevFail,
			Detail: envPath + " is missing", Remedy: "sudo mymcp init"})
		return checks // nothing downstream is meaningful without config
	}
	add(Check{Group: "CONFIG", Name: "env file", Severity: SevOK, Detail: envPath})
	if st, err := os.Stat(envPath); err == nil && st.Mode().Perm() != 0o600 {
		add(Check{Group: "CONFIG", Name: "env permissions", Severity: SevFail,
			Detail: fmt.Sprintf("%s is mode %o and holds the admin token", envPath, st.Mode().Perm()),
			Remedy: "chmod 600 " + envPath})
	} else {
		add(Check{Group: "CONFIG", Name: "env permissions", Severity: SevOK, Detail: "0600"})
	}
	admin := ExistingAdminToken(string(raw))
	if admin == "" {
		add(Check{Group: "CONFIG", Name: "admin token", Severity: SevFail,
			Detail: "MYMCP_ADMIN_TOKEN is empty; the server will refuse to start",
			Remedy: "sudo mymcp init"})
	} else {
		add(Check{Group: "CONFIG", Name: "admin token", Severity: SevOK})
	}
	if envValue(string(raw), "MYMCP_AUDIT_ENABLED") != "true" {
		add(Check{Group: "CONFIG", Name: "audit enabled", Severity: SevWarn,
			Detail: "audit logging is off; silent audit loss is this project's stated SOC red line",
			Remedy: "set MYMCP_AUDIT_ENABLED=true in " + envPath + " and restart"})
	} else {
		add(Check{Group: "CONFIG", Name: "audit enabled", Severity: SevOK})
	}
	tokenPath := envValue(string(raw), "MYMCP_TOKEN_FILE")
	if tokenPath == "" {
		tokenPath = filepath.Join(configDir, "tokens.json")
	}
	checks = append(checks, tokenStoreChecks(tokenPath)...)

	// --- RUNTIME ---
	checks = append(checks, runtimeChecks(sys)...)

	// --- FUNCTIONAL ---
	port := envValue(string(raw), "MYMCP_PORT")
	if port == "" {
		port = "8765"
	}
	checks = append(checks, functionalChecks(tokenPath, port)...)

	// --- RECORDER ---
	if envValue(string(raw), "MYMCP_RECORDER_ENABLED") == "true" {
		checks = append(checks, recorderChecks(sys, string(raw), port)...)
	}
	return checks
}

func nonEmptyLines(s string) []string {
	var out []string
	for _, l := range strings.Split(s, "\n") {
		if l = strings.TrimSpace(l); l != "" {
			out = append(out, l)
		}
	}
	return out
}

// tokenStoreChecks is deliberately read-only. auth.NewTokenStore creates the
// store (and its parent directory) when the file is absent, so this MUST
// os.Stat first and return before ever calling into auth on a missing file —
// a diagnostic command must never mutate the system it is diagnosing.
func tokenStoreChecks(path string) []Check {
	st, err := os.Stat(path)
	if err != nil {
		return []Check{{Group: "CONFIG", Name: "token store", Severity: SevFail,
			Detail: path + " is missing", Remedy: "sudo mymcp init"}}
	}
	out := []Check{}
	if st.Mode().Perm() != 0o600 {
		out = append(out, Check{Group: "CONFIG", Name: "token store permissions", Severity: SevFail,
			Detail: fmt.Sprintf("%s is mode %o", path, st.Mode().Perm()),
			Remedy: "chmod 600 " + path})
	} else {
		out = append(out, Check{Group: "CONFIG", Name: "token store permissions", Severity: SevOK})
	}
	store, err := auth.NewTokenStore(path, "unused-by-list")
	if err != nil {
		return append(out, Check{Group: "CONFIG", Name: "token store", Severity: SevFail,
			Detail: err.Error(), Remedy: "sudo mymcp init"})
	}
	var enabled int
	for _, info := range store.ListTokens() {
		if info.Enabled {
			enabled++
		}
	}
	if enabled == 0 {
		return append(out, Check{Group: "CONFIG", Name: "client tokens", Severity: SevFail,
			Detail: "no enabled tokens; no client can connect",
			Remedy: "mymcp token add --role rw default"})
	}
	return append(out, Check{Group: "CONFIG", Name: "client tokens", Severity: SevOK,
		Detail: fmt.Sprintf("%d enabled", enabled)})
}

func runtimeChecks(sys System) []Check {
	if _, err := os.Stat("/run/systemd/system"); err != nil {
		return []Check{{Group: "RUNTIME", Name: "systemd", Severity: SevWarn,
			Detail: "not present; this host runs mymcp in the foreground",
			Remedy: "mymcp serve -env-file /etc/mymcp/.env"}}
	}
	out := []Check{{Group: "RUNTIME", Name: "systemd", Severity: SevOK}}
	if got, err := sys.Run("systemctl", "is-enabled", "mymcp"); err != nil ||
		strings.TrimSpace(got) != "enabled" {
		out = append(out, Check{Group: "RUNTIME", Name: "unit enabled", Severity: SevWarn,
			Detail: "mymcp.service will not start at boot",
			Remedy: "systemctl enable mymcp"})
	} else {
		out = append(out, Check{Group: "RUNTIME", Name: "unit enabled", Severity: SevOK})
	}
	if got, err := sys.Run("systemctl", "is-active", "mymcp"); err != nil ||
		strings.TrimSpace(got) != "active" {
		out = append(out, Check{Group: "RUNTIME", Name: "unit active", Severity: SevFail,
			Detail: "mymcp.service is not running",
			Remedy: "systemctl start mymcp && journalctl -u mymcp -n 50"})
	} else {
		out = append(out, Check{Group: "RUNTIME", Name: "unit active", Severity: SevOK})
	}
	out = append(out, execStartCheck(sys))
	return out
}

// execStartCheck pins down WHICH copy of the binary systemd actually runs.
// With two install channels (pipx and the raw binary) an upgrade can land on
// the copy the unit does not use, which presents as "I upgraded but nothing
// changed".
func execStartCheck(sys System) Check {
	raw, err := os.ReadFile("/etc/systemd/system/mymcp.service")
	if err != nil {
		return Check{Group: "RUNTIME", Name: "unit ExecStart", Severity: SevWarn,
			Detail: "no unit file at /etc/systemd/system/mymcp.service",
			Remedy: "sudo mymcp init"}
	}
	var unitBin string
	for _, line := range strings.Split(string(raw), "\n") {
		if after, ok := strings.CutPrefix(strings.TrimSpace(line), "ExecStart="); ok {
			unitBin = strings.Fields(after)[0]
			break
		}
	}
	pathBin, _ := sys.LookPath("mymcp")
	switch {
	case unitBin == "":
		return Check{Group: "RUNTIME", Name: "unit ExecStart", Severity: SevWarn,
			Detail: "no ExecStart= line", Remedy: "sudo mymcp init"}
	case pathBin != "" && unitBin != pathBin:
		return Check{Group: "RUNTIME", Name: "unit ExecStart", Severity: SevFail,
			Detail: fmt.Sprintf("unit runs %s but PATH resolves %s", unitBin, pathBin),
			Remedy: "sudo mymcp init   # repoints ExecStart at the current binary"}
	default:
		return Check{Group: "RUNTIME", Name: "unit ExecStart", Severity: SevOK, Detail: unitBin}
	}
}

func functionalChecks(tokenPath, port string) []Check {
	// Same trap as tokenStoreChecks: auth.NewTokenStore creates the file
	// (and its parent directory) when missing. Doctor must never do that, so
	// confirm the store exists before touching auth at all.
	if _, err := os.Stat(tokenPath); err != nil {
		return []Check{{Group: "FUNCTIONAL", Name: "tools/list", Severity: SevFail,
			Detail: "cannot test: token store is missing",
			Remedy: "sudo mymcp init"}}
	}
	store, err := auth.NewTokenStore(tokenPath, "unused-by-list")
	if err != nil {
		return []Check{{Group: "FUNCTIONAL", Name: "tools/list", Severity: SevFail,
			Detail: "cannot read the token store: " + err.Error()}}
	}
	// Prefer rw: only an rw token sees the full tool set, because tools/list
	// is role-filtered in mcpserver.go.
	var token, role string
	for tok, info := range store.ListTokens() {
		if !info.Enabled {
			continue
		}
		if info.Role == "rw" {
			token, role = tok, "rw"
			break
		}
		if token == "" {
			token, role = tok, info.Role
		}
	}
	if token == "" {
		return []Check{{Group: "FUNCTIONAL", Name: "tools/list", Severity: SevFail,
			Detail: "no enabled token to test with",
			Remedy: "mymcp token add --role rw default"}}
	}

	start := time.Now()
	code, body, err := httpJSON("http://127.0.0.1:"+port+"/mcp", token,
		`{"jsonrpc":"2.0","id":1,"method":"tools/list"}`)
	elapsed := time.Since(start).Round(time.Millisecond)
	if err != nil {
		return []Check{{Group: "FUNCTIONAL", Name: "tools/list", Severity: SevFail,
			Detail: err.Error(), Remedy: "systemctl start mymcp && journalctl -u mymcp -n 50"}}
	}
	if code != http.StatusOK {
		return []Check{{Group: "FUNCTIONAL", Name: "tools/list", Severity: SevFail,
			Detail: fmt.Sprintf("HTTP %d: %s", code, truncate(body, 120)),
			Remedy: "journalctl -u mymcp -n 50"}}
	}
	n := strings.Count(body, `"inputSchema"`)
	if role == "rw" && n != 9 {
		return []Check{{Group: "FUNCTIONAL", Name: "tools/list", Severity: SevFail,
			Detail: fmt.Sprintf("rw token saw %d tools, want 9", n),
			Remedy: "journalctl -u mymcp -n 50"}}
	}
	if n == 0 {
		return []Check{{Group: "FUNCTIONAL", Name: "tools/list", Severity: SevFail,
			Detail: "empty tool list", Remedy: "journalctl -u mymcp -n 50"}}
	}
	return []Check{{Group: "FUNCTIONAL", Name: "tools/list", Severity: SevOK,
		Detail: fmt.Sprintf("%d tools (%s token, %s)", n, role, elapsed)}}
}

func truncate(s string, n int) string {
	if len(s) <= n {
		return s
	}
	return s[:n] + "…"
}

// metricValue pulls a single unlabelled gauge/counter out of a /metrics scrape.
func metricValue(scrape, name string) (float64, bool) {
	for _, line := range strings.Split(scrape, "\n") {
		if !strings.HasPrefix(line, name+" ") {
			continue
		}
		var v float64
		if _, err := fmt.Sscanf(strings.TrimPrefix(line, name+" "), "%g", &v); err == nil {
			return v, true
		}
	}
	return 0, false
}

func recorderChecks(sys System, env, port string) []Check {
	out := []Check{}
	if _, err := sys.LookPath("mymcp-recorder"); err != nil {
		return append(out, Check{Group: "RECORDER", Name: "sidecar installed", Severity: SevFail,
			Detail: "MYMCP_RECORDER_ENABLED=true but mymcp-recorder is not on PATH",
			Remedy: `pipx inject algony-mymcp "algony-mymcp[recorder]" && sudo mymcp init`})
	}
	out = append(out, Check{Group: "RECORDER", Name: "sidecar installed", Severity: SevOK})
	if got, err := sys.Run("systemctl", "is-active", "mymcp-recorder"); err != nil ||
		strings.TrimSpace(got) != "active" {
		out = append(out, Check{Group: "RECORDER", Name: "sidecar active", Severity: SevFail,
			Detail: "mymcp-recorder.service is not running",
			Remedy: "systemctl start mymcp-recorder && journalctl -u mymcp-recorder -n 50"})
		return out
	}
	out = append(out, Check{Group: "RECORDER", Name: "sidecar active", Severity: SevOK})

	code, scrape, err := httpJSON("http://127.0.0.1:"+port+"/metrics", envValue(env, "MYMCP_METRICS_TOKEN"), "")
	if err != nil || code != http.StatusOK {
		out = append(out, Check{Group: "RECORDER", Name: "backlog", Severity: SevWarn,
			Detail: "could not scrape /metrics to judge the backlog"})
		return out
	}
	if open, ok := metricValue(scrape, "mymcp_recorder_circuit_open"); ok && open == 1 {
		out = append(out, Check{Group: "RECORDER", Name: "circuit breaker", Severity: SevFail,
			Detail: "the merge circuit breaker is open",
			Remedy: "journalctl -u mymcp-recorder -n 50"})
	}
	interval := 300.0
	if v := envValue(env, "MYMCP_RECORDER_MERGE_INTERVAL_SEC"); v != "" {
		fmt.Sscanf(v, "%g", &interval)
	}
	pending, okP := metricValue(scrape, "mymcp_recorder_pending_events")
	lastAttempt, okA := metricValue(scrape, "mymcp_recorder_merge_last_attempt_timestamp")
	age := float64(time.Now().Unix()) - lastAttempt
	switch {
	case !okP || !okA:
		out = append(out, Check{Group: "RECORDER", Name: "backlog", Severity: SevWarn,
			Detail: "recorder metrics not exported yet"})
	case pending > 0 && age > 2*interval:
		// The project's own stall predicate (CLAUDE.md). This is the failure
		// that went unnoticed for four weeks in production.
		out = append(out, Check{Group: "RECORDER", Name: "backlog", Severity: SevFail,
			Detail: fmt.Sprintf("%.0f events pending, last merge attempt %.0fs ago", pending, age),
			Remedy: "journalctl -u mymcp-recorder -n 50"})
	default:
		out = append(out, Check{Group: "RECORDER", Name: "backlog", Severity: SevOK,
			Detail: fmt.Sprintf("%.0f pending", pending)})
	}
	return out
}

func DoctorExitCode(checks []Check, strict bool) int {
	for _, c := range checks {
		if c.Severity == SevFail || (strict && c.Severity == SevWarn) {
			return 1
		}
	}
	return 0
}

func RenderChecksJSON(checks []Check, w io.Writer) error {
	enc := json.NewEncoder(w)
	enc.SetIndent("", "  ")
	return enc.Encode(checks)
}

func RenderChecks(checks []Check, w io.Writer) {
	var problems, warnings int
	group := ""
	for _, c := range checks {
		if c.Group != group {
			group = c.Group
			fmt.Fprintf(w, "\n%s\n", group)
		}
		fmt.Fprintf(w, "  %s %-20s %s\n", c.Severity.glyph(), c.Name, c.Detail)
		if c.Remedy != "" && c.Severity != SevOK {
			fmt.Fprintf(w, "    → %s\n", c.Remedy)
		}
		switch c.Severity {
		case SevFail:
			problems++
		case SevWarn:
			warnings++
		}
	}
	fmt.Fprintf(w, "\n%d %s, %d %s.\n",
		problems, plural(problems, "problem"), warnings, plural(warnings, "warning"))
}

func plural(n int, word string) string {
	if n == 1 {
		return word
	}
	return word + "s"
}

// httpJSON is the shared helper for the functional and recorder checks.
func httpJSON(url, token, body string) (int, string, error) {
	method := http.MethodPost
	if body == "" {
		method = http.MethodGet
	}
	req, err := http.NewRequest(method, url, bytes.NewBufferString(body))
	if err != nil {
		return 0, "", err
	}
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Accept", "application/json, text/event-stream")
	if token != "" {
		req.Header.Set("Authorization", "Bearer "+token)
	}
	resp, err := (&http.Client{Timeout: 5 * time.Second}).Do(req)
	if err != nil {
		return 0, "", err
	}
	defer resp.Body.Close()
	raw, _ := io.ReadAll(resp.Body)
	return resp.StatusCode, string(raw), nil
}
