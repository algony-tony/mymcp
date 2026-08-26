package setup

import (
	"fmt"
	"os"
	"path/filepath"
)

// RecorderAvail is how init decides whether to even ask about the recorder.
type RecorderAvail int

const (
	// RecorderUnavailable: no Python tooling — the raw-binary install path.
	RecorderUnavailable RecorderAvail = iota
	// RecorderViaPipx: pipx is present, so `pipx inject` can add the extra.
	RecorderViaPipx
	// RecorderReady: mymcp-recorder is already on PATH; nothing to install.
	RecorderReady
)

type Preflight struct {
	IsRoot      bool
	HasSystemd  bool
	ExistingEnv string // "" when no .env exists
	Recorder    RecorderAvail
}

// RunPreflight gathers everything init must know BEFORE asking any question.
// Failing a user after a questionnaire is hostile.
//
// A missing .env is a normal fresh install and returns no error. Any other
// read failure IS returned: silently treating an unreadable root-owned .env as
// "no existing install" would let the wizard regenerate a live host's config.
func RunPreflight(configDir string, sys System) (Preflight, error) {
	pf := Preflight{IsRoot: os.Geteuid() == 0}
	if st, err := os.Stat("/run/systemd/system"); err == nil && st.IsDir() {
		pf.HasSystemd = true
	}
	raw, err := os.ReadFile(filepath.Join(configDir, ".env"))
	switch {
	case err == nil:
		pf.ExistingEnv = string(raw)
	case !os.IsNotExist(err):
		return pf, fmt.Errorf("cannot read %s: %w", filepath.Join(configDir, ".env"), err)
	}
	switch {
	case lookOK(sys, "mymcp-recorder"):
		pf.Recorder = RecorderReady
	case lookOK(sys, "pipx"):
		pf.Recorder = RecorderViaPipx
	default:
		pf.Recorder = RecorderUnavailable
	}
	return pf, nil
}

func lookOK(sys System, name string) bool {
	_, err := sys.LookPath(name)
	return err == nil
}
