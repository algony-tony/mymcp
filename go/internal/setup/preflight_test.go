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
			pf, err := RunPreflight(t.TempDir(), sys)
			if err != nil {
				t.Fatal(err)
			}
			if got := pf.Recorder; got != tc.want {
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
	pf, err := RunPreflight(dir, newFakeSystem())
	if err != nil {
		t.Fatal(err)
	}
	if pf.ExistingEnv == "" {
		t.Fatal("existing .env must be read so the wizard can seed its defaults")
	}
}

func TestRunPreflightDistinguishesUnreadableFromAbsent(t *testing.T) {
	dir := t.TempDir()
	envPath := filepath.Join(dir, ".env")
	if err := os.WriteFile(envPath, []byte("MYMCP_PORT=8765\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	if err := os.Chmod(envPath, 0o000); err != nil {
		t.Fatal(err)
	}
	if os.Geteuid() == 0 {
		t.Skip("root bypasses file permissions")
	}
	if _, err := RunPreflight(dir, newFakeSystem()); err == nil {
		t.Fatal("an unreadable .env must be an error, not a silent 'fresh install'")
	}
	// And absent is still fine.
	if _, err := RunPreflight(t.TempDir(), newFakeSystem()); err != nil {
		t.Fatalf("a missing .env is a normal fresh install, got %v", err)
	}
}
