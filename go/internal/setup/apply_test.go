package setup

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

// tempPlan returns a Plan whose every path lives under t.TempDir(), so Apply
// can run unprivileged in CI.
func tempPlan(t *testing.T) (*Plan, *fakeSystem) {
	t.Helper()
	root := t.TempDir()
	p := DefaultPlan()
	p.ConfigDir = filepath.Join(root, "etc")
	p.LogDir = filepath.Join(root, "log")
	p.RecorderDataDir = filepath.Join(root, "lib", "recorder")
	p.UnitDir = filepath.Join(root, "units")
	p.ExecPath = "/usr/local/bin/mymcp"
	p.Start = false
	if err := os.MkdirAll(p.UnitDir, 0o755); err != nil {
		t.Fatal(err)
	}
	return p, newFakeSystem()
}

func TestApplyCreatesEverythingOnFreshHost(t *testing.T) {
	p, sys := tempPlan(t)
	out, err := Apply(p, sys)
	if err != nil {
		t.Fatalf("Apply: %v", err)
	}
	for _, path := range []string{p.EnvPath(), p.TokenPath(), p.UnitPath()} {
		if _, err := os.Stat(path); err != nil {
			t.Errorf("expected %s to exist: %v", path, err)
		}
	}
	if out.AdminToken == "" || out.ClientToken == "" {
		t.Fatalf("both tokens must be reported: %+v", out)
	}
	st, err := os.Stat(p.EnvPath())
	if err != nil {
		t.Fatal(err)
	}
	if st.Mode().Perm() != 0o600 {
		t.Errorf(".env mode = %o, want 600 (it holds the admin token)", st.Mode().Perm())
	}
	if !sys.ran("systemctl daemon-reload") {
		t.Errorf("daemon-reload not issued; calls=%v", sys.Calls)
	}
}

func TestApplyIsIdempotentAndKeepsAdminToken(t *testing.T) {
	p, sys := tempPlan(t)
	first, err := Apply(p, sys)
	if err != nil {
		t.Fatal(err)
	}
	second, err := Apply(p, newFakeSystem())
	if err != nil {
		t.Fatal(err)
	}
	if second.AdminToken != first.AdminToken {
		t.Fatalf("admin token changed on re-run (%s -> %s); every existing admin client would break",
			first.AdminToken, second.AdminToken)
	}
	if second.ClientToken != first.ClientToken {
		t.Fatalf("client token %q duplicated on re-run (was %q)", second.ClientToken, first.ClientToken)
	}
	for _, r := range second.Results {
		if r.Status == StatusCreated {
			t.Errorf("step %q reported created on an unchanged re-run", r.Step)
		}
	}
	_ = sys
}

func TestApplyPreservesHandEditedEnvKeysAndBacksUp(t *testing.T) {
	p, sys := tempPlan(t)
	if _, err := Apply(p, sys); err != nil {
		t.Fatal(err)
	}
	// Operator hand-edits the file afterwards.
	raw, err := os.ReadFile(p.EnvPath())
	if err != nil {
		t.Fatal(err)
	}
	edited := string(raw) + "\nMYMCP_PROTECTED_PATHS=/root/.ssh\n# keep me\n"
	if err := os.WriteFile(p.EnvPath(), []byte(edited), 0o600); err != nil {
		t.Fatal(err)
	}

	p.Port = 9000
	if _, err := Apply(p, newFakeSystem()); err != nil {
		t.Fatal(err)
	}
	got, err := os.ReadFile(p.EnvPath())
	if err != nil {
		t.Fatal(err)
	}
	s := string(got)
	if !strings.Contains(s, "MYMCP_PORT=9000") {
		t.Errorf("owned key not updated:\n%s", s)
	}
	if !strings.Contains(s, "MYMCP_PROTECTED_PATHS=/root/.ssh") || !strings.Contains(s, "# keep me") {
		t.Errorf("hand-edited content was lost:\n%s", s)
	}
	matches, _ := filepath.Glob(filepath.Join(p.ConfigDir, ".env.bak-*"))
	if len(matches) == 0 {
		t.Error("no .env.bak-<timestamp> written before the merge")
	}
}

func TestApplyDryRunWritesNothing(t *testing.T) {
	p, sys := tempPlan(t)
	p.DryRun = true
	if _, err := Apply(p, sys); err != nil {
		t.Fatal(err)
	}
	for _, path := range []string{p.EnvPath(), p.TokenPath(), p.UnitPath()} {
		if _, err := os.Stat(path); !os.IsNotExist(err) {
			t.Errorf("%s must not exist after -dry-run (err=%v)", path, err)
		}
	}
	if len(sys.Calls) != 0 {
		t.Errorf("-dry-run must exec nothing, got %v", sys.Calls)
	}
}

func TestApplyDryRunExecsNothingEvenForANonRootServiceUser(t *testing.T) {
	// The DryRun check must sit before the `id -u` probe, not after it.
	p, sys := tempPlan(t)
	p.DryRun = true
	p.ServiceUser = "mymcp"
	if _, err := Apply(p, sys); err != nil {
		t.Fatal(err)
	}
	if len(sys.Calls) != 0 {
		t.Fatalf("-dry-run must exec nothing, got %v", sys.Calls)
	}
}

func TestApplyDegradedModeSkipsUnitAndSystemctl(t *testing.T) {
	p, sys := tempPlan(t)
	p.HasSystemd = false
	out, err := Apply(p, sys)
	if err != nil {
		t.Fatalf("degraded mode must not fail: %v", err)
	}
	if _, err := os.Stat(p.UnitPath()); !os.IsNotExist(err) {
		t.Error("no unit may be written without systemd")
	}
	if len(sys.Calls) != 0 {
		t.Errorf("no systemctl calls without systemd, got %v", sys.Calls)
	}
	if _, err := os.Stat(p.EnvPath()); err != nil {
		t.Error(".env must still be written in degraded mode")
	}
	var skipped bool
	for _, r := range out.Results {
		if r.Status == StatusSkipped {
			skipped = true
		}
	}
	if !skipped {
		t.Error("degraded mode must report skipped steps, not silently omit them")
	}
}

func TestApplyInstallsRipgrepViaDetectedPackageManager(t *testing.T) {
	p, sys := tempPlan(t)
	p.InstallRipgrep = true
	sys.Paths["apt-get"] = "/usr/bin/apt-get"
	if _, err := Apply(p, sys); err != nil {
		t.Fatal(err)
	}
	if !sys.ran("apt-get install -y ripgrep") {
		t.Fatalf("ripgrep not installed; calls=%v", sys.Calls)
	}
}

func TestApplyPrefersSuppliedRipgrepBinaryOverPackageManager(t *testing.T) {
	p, sys := tempPlan(t)
	p.InstallRipgrep = true
	src := filepath.Join(t.TempDir(), "rg")
	if err := os.WriteFile(src, []byte("#!/bin/sh\n"), 0o755); err != nil {
		t.Fatal(err)
	}
	p.RipgrepBinary = src
	sys.Paths["apt-get"] = "/usr/bin/apt-get"
	if _, err := Apply(p, sys); err != nil {
		t.Fatal(err)
	}
	for _, c := range sys.Calls {
		if strings.Contains(c, "install") {
			t.Fatalf("air-gapped host must not shell out to a package manager: %v", sys.Calls)
		}
	}
	if _, err := os.Stat("/usr/local/bin/rg"); err != nil {
		t.Skip("cannot write /usr/local/bin unprivileged; covered by the e2e smoke instead")
	}
}

func TestApplySkipsRipgrepWhenNotRequested(t *testing.T) {
	p, sys := tempPlan(t)
	sys.Paths["apt-get"] = "/usr/bin/apt-get"
	if _, err := Apply(p, sys); err != nil {
		t.Fatal(err)
	}
	for _, c := range sys.Calls {
		if strings.Contains(c, "ripgrep") {
			t.Fatalf("ripgrep install not requested but ran %q", c)
		}
	}
}

func TestApplyCreatesServiceUserOnlyWhenNotRoot(t *testing.T) {
	p, sys := tempPlan(t)
	p.ServiceUser = "mymcp"
	sys.Paths["useradd"] = "/usr/sbin/useradd"
	sys.Errors["id -u mymcp"] = errNoSuchUser
	if _, err := Apply(p, sys); err != nil {
		t.Fatal(err)
	}
	if !sys.ran("useradd -r -s /usr/sbin/nologin mymcp") {
		t.Fatalf("service user not created; calls=%v", sys.Calls)
	}

	p2, sys2 := tempPlan(t)
	if _, err := Apply(p2, sys2); err != nil {
		t.Fatal(err)
	}
	for _, c := range sys2.Calls {
		if strings.HasPrefix(c, "useradd") {
			t.Fatalf("must not useradd for the root service user: %v", sys2.Calls)
		}
	}
}

func TestApplyRecorderInjectsThenRendersUnit(t *testing.T) {
	p, sys := tempPlan(t)
	p.Recorder = RecorderPlan{Enabled: true, Provider: "anthropic", APIKey: "sk-x", NeedsInject: true}
	if _, err := Apply(p, sys); err != nil {
		t.Fatal(err)
	}
	if !sys.ran(`pipx inject algony-mymcp algony-mymcp[recorder]`) {
		t.Errorf("missing pipx inject; calls=%v", sys.Calls)
	}
	want := "mymcp-recorder --install-unit --service-user root --env-file " +
		p.EnvPath() + " --output " + p.RecorderUnitPath()
	if !sys.ran(want) {
		t.Errorf("recorder unit must be rendered by the Python owner of the template.\nwant: %s\ngot:  %v", want, sys.Calls)
	}
}

func TestApplyRecorderSkipsInjectWhenAlreadyInstalled(t *testing.T) {
	p, sys := tempPlan(t)
	p.Recorder = RecorderPlan{Enabled: true, Provider: "anthropic", NeedsInject: false}
	if _, err := Apply(p, sys); err != nil {
		t.Fatal(err)
	}
	for _, c := range sys.Calls {
		if strings.HasPrefix(c, "pipx inject") {
			t.Fatalf("must not re-inject an installed recorder: %v", sys.Calls)
		}
	}
}

func TestApplySkipsRecorderEntirelyWhenDisabled(t *testing.T) {
	p, sys := tempPlan(t)
	if _, err := Apply(p, sys); err != nil {
		t.Fatal(err)
	}
	for _, c := range sys.Calls {
		if strings.Contains(c, "recorder") {
			t.Fatalf("recorder disabled but ran %q", c)
		}
	}
}
