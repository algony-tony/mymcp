// Package setup implements `mymcp init` and `mymcp doctor`: it renders the
// .env and systemd unit, creates directories and tokens, and verifies the
// result. Every external command goes through the System interface so the
// rest of the package is testable against t.TempDir() with no root and no TTY.
package setup

import "path/filepath"

// RecorderPlan captures the optional sidecar decisions.
type RecorderPlan struct {
	Enabled     bool
	Provider    string // anthropic | openai
	Model       string
	APIKey      string
	NeedsInject bool // true when the [recorder] extra must be installed via pipx
}

// Plan is the complete product of the wizard (or of the non-interactive
// flags). It is the only value shared between wizard.go and apply.go.
type Plan struct {
	Bind            string
	Port            int
	ServiceUser     string
	ConfigDir       string
	LogDir          string
	RecorderDataDir string
	AuditEnabled    bool
	MetricsToken    string
	ClientName      string
	ClientRole      string
	Recorder        RecorderPlan
	InstallRipgrep  bool
	RipgrepBinary   string // pre-supplied binary (offline bundle); empty = use a package manager
	Start           bool
	DryRun          bool

	// HasSystemd is false in degraded mode (containers, WSL, OpenRC): every
	// unit and start step is skipped, everything else still runs.
	HasSystemd bool
	// ExecPath is the mymcp binary the unit's ExecStart will name.
	ExecPath string
	// UnitDir is overridable so tests can write units into t.TempDir().
	UnitDir string
	// RipgrepDest is where a supplied ripgrep binary is installed. It is
	// overridable so tests never write into a real system path: a test that
	// dropped a stub `rg` into /usr/local/bin broke an unrelated test in the
	// same `go test ./...` run and left the machine with a no-op ripgrep.
	RipgrepDest string
}

func DefaultPlan() *Plan {
	return &Plan{
		Bind:            "0.0.0.0",
		Port:            8765,
		ServiceUser:     "root",
		ConfigDir:       "/etc/mymcp",
		LogDir:          "/var/log/mymcp",
		RecorderDataDir: "/var/lib/mymcp/recorder",
		AuditEnabled:    true,
		ClientName:      "default",
		ClientRole:      "rw",
		Start:           true,
		HasSystemd:      true,
		UnitDir:         "/etc/systemd/system",
		RipgrepDest:     "/usr/local/bin/rg",
	}
}

func (p *Plan) EnvPath() string   { return filepath.Join(p.ConfigDir, ".env") }
func (p *Plan) TokenPath() string { return filepath.Join(p.ConfigDir, "tokens.json") }
func (p *Plan) UnitPath() string  { return filepath.Join(p.UnitDir, "mymcp.service") }
func (p *Plan) RecorderUnitPath() string {
	return filepath.Join(p.UnitDir, "mymcp-recorder.service")
}
