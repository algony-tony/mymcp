// Command mymcp is the Go core of the mymcp MCP server.
package main

import (
	"flag"
	"fmt"
	"net/http"
	"os"

	"github.com/algony-tony/mymcp/go/internal/auth"
	"github.com/algony-tony/mymcp/go/internal/config"
	"github.com/algony-tony/mymcp/go/internal/httpserver"
	"github.com/algony-tony/mymcp/go/internal/setup"
	"github.com/algony-tony/mymcp/go/internal/version"
)

func main() {
	os.Exit(run(os.Args[1:]))
}

func run(args []string) int {
	if len(args) == 0 {
		fmt.Fprintln(os.Stderr, "usage: mymcp {serve|init|doctor|version|token}")
		return 2
	}
	switch args[0] {
	case "version":
		fmt.Println("mymcp " + version.Version)
		return 0
	case "token":
		return runToken(args[1:])
	case "init":
		return runInit(args[1:])
	case "doctor":
		return runDoctor(args[1:])
	case "serve":
		fs := flag.NewFlagSet("serve", flag.ContinueOnError)
		envFile := fs.String("env-file", "", "path to .env file")
		host := fs.String("host", "", "bind host (overrides config)")
		port := fs.Int("port", 0, "bind port (overrides config)")
		if err := fs.Parse(args[1:]); err != nil {
			return 2
		}
		if *envFile != "" {
			os.Setenv("MYMCP_ENV_FILE", *envFile)
		}
		if err := httpserver.Serve(*host, *port, version.Version); err != nil && err != http.ErrServerClosed {
			fmt.Fprintln(os.Stderr, "serve:", err)
			return 1
		}
		return 0
	default:
		fmt.Fprintf(os.Stderr, "unknown command: %s\n", args[0])
		return 2
	}
}

func loadTokenStore() (*auth.TokenStore, error) {
	cfg, err := config.Load()
	if err != nil {
		return nil, err
	}
	admin := cfg.AdminToken
	if admin == "" {
		admin = "unset" // list/add/revoke operate on the file; admin value is not consulted here
	}
	return auth.NewTokenStore(cfg.TokenFile, admin)
}

func runToken(args []string) int {
	if len(args) == 0 {
		fmt.Fprintln(os.Stderr, "usage: mymcp token {list|add|revoke}")
		return 2
	}
	switch args[0] {
	case "list":
		store, err := loadTokenStore()
		if err != nil {
			fmt.Fprintln(os.Stderr, "token:", err)
			return 1
		}
		toks := store.ListTokens()
		if len(toks) == 0 {
			fmt.Println("(no ro/rw tokens)")
			return 0
		}
		for tok, info := range toks {
			fmt.Printf("%-2s  %-20s  %s\n", info.Role, info.Name, tok)
		}
		return 0
	case "add":
		fs := flag.NewFlagSet("add", flag.ContinueOnError)
		role := fs.String("role", "ro", "token role: ro or rw")
		if err := fs.Parse(args[1:]); err != nil {
			return 2
		}
		if fs.NArg() < 1 {
			fmt.Fprintln(os.Stderr, "usage: mymcp token add [--role ro|rw] <name>")
			return 2
		}
		store, err := loadTokenStore()
		if err != nil {
			fmt.Fprintln(os.Stderr, "token:", err)
			return 1
		}
		tok, err := store.CreateToken(fs.Arg(0), *role)
		if err != nil {
			fmt.Fprintln(os.Stderr, "token:", err)
			return 1
		}
		fmt.Println(tok)
		return 0
	case "revoke":
		if len(args) < 2 {
			fmt.Fprintln(os.Stderr, "usage: mymcp token revoke <token>")
			return 2
		}
		store, err := loadTokenStore()
		if err != nil {
			fmt.Fprintln(os.Stderr, "token:", err)
			return 1
		}
		found, err := store.RevokeToken(args[1])
		if err != nil {
			fmt.Fprintln(os.Stderr, "token:", err)
			return 1
		}
		if found {
			fmt.Printf("revoked %s\n", args[1])
			return 0
		}
		fmt.Fprintf(os.Stderr, "not found: %s\n", args[1])
		return 1
	default:
		fmt.Fprintf(os.Stderr, "unknown token subcommand: %s\n", args[0])
		return 2
	}
}

// parseInitFlags parses `init`'s flags into setup.Options and records, in
// Explicit, exactly which flags the user typed (via flag.FlagSet.Visit,
// which visits only flags actually set). A re-run of `mymcp init` seeds
// values from an existing .env, and Explicit is what stops that seeding from
// overriding a flag the user really passed on this invocation.
func parseInitFlags(args []string) (setup.Options, error) {
	o := setup.Options{}
	fs := flag.NewFlagSet("init", flag.ContinueOnError)
	fs.BoolVar(&o.Yes, "yes", false, "non-interactive; accept every default")
	fs.StringVar(&o.Bind, "bind", "0.0.0.0", "bind address")
	fs.IntVar(&o.Port, "port", 8765, "bind port")
	fs.StringVar(&o.ServiceUser, "service-user", "root", "systemd User=")
	fs.StringVar(&o.ConfigDir, "config-dir", "/etc/mymcp", "config directory")
	fs.StringVar(&o.LogDir, "log-dir", "/var/log/mymcp", "audit log directory")
	fs.StringVar(&o.RecorderDataDir, "recorder-data-dir", "/var/lib/mymcp/recorder", "recorder data directory")
	fs.BoolVar(&o.Audit, "audit", true, "enable the audit log")
	fs.StringVar(&o.MetricsToken, "metrics-token", "", "explicit /metrics token")
	fs.BoolVar(&o.NoMetricsToken, "no-metrics-token", false, "leave /metrics unauthenticated")
	fs.StringVar(&o.ClientName, "client-name", "default", "name of the first client token")
	fs.StringVar(&o.ClientRole, "client-role", "rw", "role of the first client token: ro or rw")
	fs.BoolVar(&o.Recorder, "recorder", false, "enable the overview recorder sidecar")
	fs.StringVar(&o.RecorderProvider, "recorder-provider", "anthropic", "anthropic or openai")
	fs.StringVar(&o.RecorderModel, "recorder-model", "", "recorder LLM model")
	fs.StringVar(&o.RecorderAPIKey, "recorder-api-key", os.Getenv("MYMCP_RECORDER_LLM_API_KEY"), "recorder LLM API key")
	fs.BoolVar(&o.InstallRipgrep, "install-ripgrep", true, "install ripgrep when missing")
	fs.StringVar(&o.RipgrepBinary, "ripgrep-binary", "", "use this ripgrep binary instead of a package manager")
	fs.BoolVar(&o.Start, "start", true, "enable and start the service")
	fs.BoolVar(&o.DryRun, "dry-run", false, "print what would change and write nothing")
	if err := fs.Parse(args); err != nil {
		return o, err
	}
	o.Explicit = map[string]bool{}
	fs.Visit(func(f *flag.Flag) { o.Explicit[f.Name] = true })
	// flag.Visit reports only flags typed on the command line, so an
	// env-sourced key would otherwise lose to the stale value already in
	// .env. Exporting the variable is deliberate; treat it as typed.
	if os.Getenv("MYMCP_RECORDER_LLM_API_KEY") != "" {
		o.Explicit["recorder-api-key"] = true
	}
	return o, nil
}

func runInit(args []string) int {
	o, err := parseInitFlags(args)
	if err != nil {
		return 2
	}

	sys := setup.RealSystem()
	pf, err := setup.RunPreflight(o.ConfigDir, sys)
	if err != nil {
		fmt.Fprintln(os.Stderr, "mymcp init:", err)
		return 1
	}
	if !pf.IsRoot && !o.DryRun {
		fmt.Fprintln(os.Stderr, "mymcp init must run as root: sudo mymcp init")
		return 1
	}
	if !pf.HasSystemd {
		fmt.Fprintln(os.Stderr, "[mymcp] systemd not detected — configuring files only (degraded mode)")
	}

	var plan *setup.Plan
	if o.Yes {
		plan, err = setup.PlanFromOptions(o, pf, sys)
	} else {
		pr, terr := setup.OpenTTYPrompter(sys)
		if terr != nil {
			fmt.Fprintf(os.Stderr, "mymcp init: %v\n  re-run with -yes for a non-interactive install\n", terr)
			return 1
		}
		defer pr.Close()
		plan, err = setup.PlanFromWizard(o, pf, pr, sys)
	}
	if err != nil {
		fmt.Fprintln(os.Stderr, "mymcp init:", err)
		return 1
	}

	outcome, err := setup.Apply(plan, sys)
	for _, r := range outcome.Results {
		fmt.Fprintf(os.Stderr, "  %-9s %s %s\n", r.Status, r.Step, r.Detail)
	}
	if err != nil {
		fmt.Fprintln(os.Stderr, "mymcp init failed:", err)
		fmt.Fprintln(os.Stderr, "every step is idempotent — fix the cause and re-run `mymcp init` to resume")
		return 1
	}
	setup.Summary(plan, outcome, os.Stdout)
	if plan.Start && !plan.DryRun {
		fmt.Fprintln(os.Stdout, "\nRunning mymcp doctor…")
		setup.RenderChecks(setup.Doctor(plan.ConfigDir, sys), os.Stdout)
	}
	return 0
}

func runDoctor(args []string) int {
	fs := flag.NewFlagSet("doctor", flag.ContinueOnError)
	configDir := fs.String("config-dir", "/etc/mymcp", "config directory")
	strict := fs.Bool("strict", false, "treat warnings as failures")
	asJSON := fs.Bool("json", false, "emit machine-readable JSON")
	if err := fs.Parse(args); err != nil {
		return 2
	}
	checks := setup.Doctor(*configDir, setup.RealSystem())
	if *asJSON {
		if err := setup.RenderChecksJSON(checks, os.Stdout); err != nil {
			fmt.Fprintln(os.Stderr, "doctor:", err)
			return 1
		}
	} else {
		setup.RenderChecks(checks, os.Stdout)
	}
	return setup.DoctorExitCode(checks, *strict)
}
