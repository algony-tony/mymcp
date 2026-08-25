package setup

import (
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
func RunPreflight(configDir string, sys System) Preflight {
	pf := Preflight{IsRoot: os.Geteuid() == 0}
	if st, err := os.Stat("/run/systemd/system"); err == nil && st.IsDir() {
		pf.HasSystemd = true
	}
	if raw, err := os.ReadFile(filepath.Join(configDir, ".env")); err == nil {
		pf.ExistingEnv = string(raw)
	}
	switch {
	case lookOK(sys, "mymcp-recorder"):
		pf.Recorder = RecorderReady
	case lookOK(sys, "pipx"):
		pf.Recorder = RecorderViaPipx
	default:
		pf.Recorder = RecorderUnavailable
	}
	return pf
}

func lookOK(sys System, name string) bool {
	_, err := sys.LookPath(name)
	return err == nil
}
