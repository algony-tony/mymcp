package setup

import (
	"fmt"
	"net"
	"strconv"
	"strings"

	"github.com/algony-tony/mymcp/go/internal/auth"
)

// Options are the raw flag values of `mymcp init`.
type Options struct {
	Yes              bool
	Bind             string
	Port             int
	ServiceUser      string
	ConfigDir        string
	LogDir           string
	RecorderDataDir  string
	Audit            bool
	MetricsToken     string
	NoMetricsToken   bool
	ClientName       string
	ClientRole       string
	Recorder         bool
	RecorderProvider string
	RecorderModel    string
	RecorderAPIKey   string
	InstallRipgrep   bool
	RipgrepBinary    string
	Start            bool
	DryRun           bool
	UnitDir          string
	FilesOnly        bool

	// Explicit names the flags the user actually typed. Seeding from an
	// existing .env must never override those. Task 7 populates it via
	// flag.FlagSet.Visit; a nil map means "nothing was typed".
	Explicit map[string]bool
}

// envValue pulls one uncommented key out of an existing .env, else "".
func envValue(existing, key string) string {
	for _, line := range strings.Split(existing, "\n") {
		if keyOf(line) != key {
			continue
		}
		_, v, _ := strings.Cut(strings.TrimSpace(line), "=")
		return strings.TrimSpace(v)
	}
	return ""
}

// envHas reports whether an uncommented KEY= line exists, distinguishing an
// intentionally empty value from an absent key.
func envHas(existing, key string) bool {
	for _, line := range strings.Split(existing, "\n") {
		if keyOf(line) == key {
			return true
		}
	}
	return false
}

// seedFromExistingEnv makes a re-run non-destructive: values already on the
// host beat flag defaults, but never beat a flag the user actually typed.
func seedFromExistingEnv(o Options, existing string) Options {
	if existing == "" {
		return o
	}
	typed := func(name string) bool { return o.Explicit[name] }
	if v := envValue(existing, "MYMCP_HOST"); v != "" && !typed("bind") {
		o.Bind = v
	}
	if v := envValue(existing, "MYMCP_PORT"); v != "" && !typed("port") {
		if n, err := strconv.Atoi(v); err == nil {
			o.Port = n
		}
	}
	if envHas(existing, "MYMCP_METRICS_TOKEN") && !typed("metrics-token") && !typed("no-metrics-token") {
		if v := envValue(existing, "MYMCP_METRICS_TOKEN"); v == "" {
			o.NoMetricsToken = true // deliberately unauthenticated; keep it that way
		} else {
			o.MetricsToken = v // reusing it keeps existing Prometheus scrapes working
		}
	}
	if v := envValue(existing, "MYMCP_AUDIT_ENABLED"); v != "" && !typed("audit") {
		o.Audit = v == "true"
	}
	if envValue(existing, "MYMCP_RECORDER_ENABLED") == "true" && !typed("recorder") {
		o.Recorder = true
		if v := envValue(existing, "MYMCP_RECORDER_LLM_PROVIDER"); v != "" && !typed("recorder-provider") {
			o.RecorderProvider = v
		}
		if v := envValue(existing, "MYMCP_RECORDER_LLM_MODEL"); v != "" && !typed("recorder-model") {
			o.RecorderModel = v
		}
		if v := envValue(existing, "MYMCP_RECORDER_LLM_API_KEY"); v != "" && !typed("recorder-api-key") {
			o.RecorderAPIKey = v
		}
	}
	return o
}

// PlanFromOptions is the non-interactive path (-yes, CI, Ansible).
func PlanFromOptions(o Options, pf Preflight, sys System) (*Plan, error) {
	return buildPlan(seedFromExistingEnv(o, pf.ExistingEnv), pf, sys)
}

// buildPlan turns fully-resolved Options into a Plan. It does no seeding of
// its own: PlanFromOptions seeds once from any existing .env before calling
// it, and PlanFromWizard seeds once up front and calls it directly after the
// questionnaire so the user's own answers are never re-seeded over.
func buildPlan(o Options, pf Preflight, sys System) (*Plan, error) {
	p := DefaultPlan()
	p.Bind, p.Port = o.Bind, o.Port
	p.ServiceUser = o.ServiceUser
	p.ConfigDir, p.LogDir, p.RecorderDataDir = o.ConfigDir, o.LogDir, o.RecorderDataDir
	p.AuditEnabled = o.Audit
	p.ClientName, p.ClientRole = o.ClientName, o.ClientRole
	p.InstallRipgrep, p.RipgrepBinary = o.InstallRipgrep, o.RipgrepBinary
	p.Start, p.DryRun = o.Start, o.DryRun
	p.HasSystemd = pf.HasSystemd
	if o.UnitDir != "" {
		p.UnitDir = o.UnitDir
	}

	switch {
	case o.NoMetricsToken:
		p.MetricsToken = ""
	case o.MetricsToken != "":
		p.MetricsToken = o.MetricsToken
	default:
		tok, err := auth.GenerateToken()
		if err != nil {
			return nil, err
		}
		p.MetricsToken = tok
	}

	if o.Recorder {
		if pf.Recorder == RecorderUnavailable {
			return nil, fmt.Errorf("-recorder requested but neither mymcp-recorder nor pipx is on PATH; " +
				"install the extra first: pipx inject algony-mymcp \"algony-mymcp[recorder]\"")
		}
		p.Recorder = RecorderPlan{
			Enabled:     true,
			Provider:    o.RecorderProvider,
			Model:       o.RecorderModel,
			APIKey:      o.RecorderAPIKey,
			NeedsInject: pf.Recorder == RecorderViaPipx,
		}
	}

	if path, err := sys.LookPath("mymcp"); err == nil {
		p.ExecPath = path
	} else {
		p.ExecPath = "/usr/local/bin/mymcp"
	}
	return p, nil
}

// PlanFromWizard asks the seven questions, seeding defaults from any existing
// .env (update mode), then builds the Plan from the answers directly.
func PlanFromWizard(o Options, pf Preflight, pr *Prompter, sys System) (*Plan, error) {
	o = seedFromExistingEnv(o, pf.ExistingEnv)

	// 1. Bind + port.
	o.Bind = pr.Ask("Bind address", o.Bind)
	if err := pr.Err(); err != nil {
		return nil, fmt.Errorf("reading bind address: %w; re-run with -yes for a non-interactive install", err)
	}
	if w := ExposureWarning(o.Bind); w != "" {
		fmt.Fprintln(pr.out, w)
		if h := FirewallHint(sys, o.Port); h != "" {
			fmt.Fprintln(pr.out, h)
		}
	}
	const maxRetries = 5
	chosen := 0
	for attempt := 0; attempt < maxRetries; attempt++ {
		v := pr.Ask("Port", strconv.Itoa(o.Port))
		if err := pr.Err(); err != nil {
			return nil, fmt.Errorf("reading port: %w; re-run with -yes for a non-interactive install", err)
		}
		n, err := strconv.Atoi(v)
		if err != nil || n < 1 || n > 65535 {
			fmt.Fprintln(pr.out, "  not a valid port")
			continue
		}
		if PortInUse(sys, o.Bind, n) {
			fmt.Fprintf(pr.out, "  port %d is already listening; choose another\n", n)
			continue
		}
		chosen = n
		break
	}
	if chosen == 0 {
		return nil, fmt.Errorf("no usable port after %d attempts", maxRetries)
	}
	o.Port = chosen

	// 2. Service user.
	fmt.Fprintln(pr.out, serviceUserWarning)
	o.ServiceUser = pr.Ask("Run the service as", o.ServiceUser)

	// 3. Audit.
	o.Audit = pr.Confirm("Enable the audit log", o.Audit)

	// 4. First client token.
	o.ClientName = pr.Ask("Name for the first client token", o.ClientName)
	o.ClientRole = pr.Ask("Role for it (rw = all tools, ro = read-only, safer)", o.ClientRole)

	// 5. Recorder.
	if pf.Recorder == RecorderUnavailable {
		fmt.Fprintln(pr.out, "Overview recorder: unavailable (no pipx and no mymcp-recorder on PATH) — skipping.")
	} else if pr.Confirm("Enable the overview recorder sidecar", o.Recorder) {
		o.Recorder = true
		o.RecorderProvider = pr.Ask("LLM provider (anthropic|openai)", "anthropic")
		o.RecorderModel = pr.Ask("Model (blank = adapter default)", o.RecorderModel)
		o.RecorderAPIKey = pr.AskSecret("API key")
	}

	// 6. ripgrep.
	if _, err := sys.LookPath("rg"); err != nil {
		o.InstallRipgrep = pr.Confirm("ripgrep is missing (grep falls back to a native scan). Install it", true)
	} else {
		o.InstallRipgrep = false
	}

	return buildPlan(o, pf, sys)
}

const serviceUserWarning = `
  SECURITY: running as root gives every token holder a root shell —
  bash_execute is deliberately NOT subject to protected paths. Issue 'ro'
  tokens to clients you do not fully trust. 'root' is still the default
  because operating the host is what mymcp is for.`

// ExposureWarning returns a warning for wildcard binds, else "".
func ExposureWarning(bind string) string {
	if bind != "0.0.0.0" && bind != "::" && bind != "*" {
		return ""
	}
	return "  WARNING: binding " + bind + " exposes mymcp to every reachable network.\n" +
		"  Anyone who reaches this port with a valid token controls this host.\n" +
		"  Safer: bind 127.0.0.1 and put a TLS reverse proxy in front."
}

// FirewallHint returns a concrete allow command for whichever firewall is present.
func FirewallHint(sys System, port int) string {
	if _, err := sys.LookPath("ufw"); err == nil {
		return fmt.Sprintf("  Firewall: sudo ufw allow %d/tcp", port)
	}
	if _, err := sys.LookPath("firewall-cmd"); err == nil {
		return fmt.Sprintf("  Firewall: sudo firewall-cmd --add-port=%d/tcp --permanent && sudo firewall-cmd --reload", port)
	}
	return ""
}

// PortInUse reports whether anything is already listening on bind:port.
// A wildcard on either side conflicts with everything on that port; two
// distinct concrete addresses on the same port do not conflict.
func PortInUse(sys System, bind string, port int) bool {
	out, err := sys.Run("ss", "-tlnH")
	if err != nil {
		return false // no ss: do not block the install on a missing tool
	}
	want := strconv.Itoa(port)
	for _, line := range strings.Split(out, "\n") {
		for _, f := range strings.Fields(line) {
			host, p, err := net.SplitHostPort(f)
			if err != nil || p != want {
				continue
			}
			if isWildcardHost(host) || isWildcardHost(bind) || host == bind {
				return true
			}
		}
	}
	return false
}

func isWildcardHost(h string) bool {
	return h == "" || h == "0.0.0.0" || h == "::" || h == "*"
}
