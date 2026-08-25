package setup

import (
	"os"
	"path/filepath"
	"testing"
)

func TestRecorderAvailabilityThreeWayRule(t *testing.T) {
	cases := []struct {
		name  string
		paths map[string]string
		want  RecorderAvail
	}{
		{"recorder already installed", map[string]string{"mymcp-recorder": "/usr/local/bin/mymcp-recorder"}, RecorderReady},
		{"pipx present only", map[string]string{"pipx": "/usr/bin/pipx"}, RecorderViaPipx},
		{"neither", map[string]string{}, RecorderUnavailable},
		{"both prefers ready", map[string]string{
			"mymcp-recorder": "/usr/local/bin/mymcp-recorder",
			"pipx":           "/usr/bin/pipx",
		}, RecorderReady},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			sys := newFakeSystem()
			sys.Paths = tc.paths
			if got := RunPreflight(t.TempDir(), sys).Recorder; got != tc.want {
				t.Fatalf("Recorder = %v, want %v", got, tc.want)
			}
		})
	}
}

func TestPreflightReadsExistingEnvForUpdateMode(t *testing.T) {
	dir := t.TempDir()
	if err := os.WriteFile(filepath.Join(dir, ".env"), []byte("MYMCP_PORT=9000\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	pf := RunPreflight(dir, newFakeSystem())
	if pf.ExistingEnv == "" {
		t.Fatal("existing .env must be read so the wizard can seed its defaults")
	}
}
