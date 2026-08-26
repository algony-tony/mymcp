package setup

import (
	"strings"
	"testing"
)

func TestRenderUnitUsesResolvedBinaryAndEnvFile(t *testing.T) {
	p := DefaultPlan()
	p.ExecPath = "/usr/local/bin/mymcp"
	got := RenderUnit(p)
	want := "ExecStart=/usr/local/bin/mymcp serve -env-file /etc/mymcp/.env"
	if !strings.Contains(got, want) {
		t.Fatalf("missing %q in:\n%s", want, got)
	}
	if !strings.Contains(got, "EnvironmentFile=/etc/mymcp/.env") {
		t.Errorf("missing EnvironmentFile:\n%s", got)
	}
	if !strings.Contains(got, "User=root") {
		t.Errorf("default service user must be written explicitly:\n%s", got)
	}
	if !strings.Contains(got, "WorkingDirectory=/etc/mymcp") {
		t.Errorf("missing WorkingDirectory:\n%s", got)
	}
	if !strings.Contains(got, "WantedBy=multi-user.target") {
		t.Errorf("missing [Install]:\n%s", got)
	}
}

func TestRenderUnitHonoursNonRootServiceUser(t *testing.T) {
	p := DefaultPlan()
	p.ExecPath = "/usr/local/bin/mymcp"
	p.ServiceUser = "mymcp"
	got := RenderUnit(p)
	if !strings.Contains(got, "User=mymcp") {
		t.Fatalf("User not honoured:\n%s", got)
	}
}

func TestRenderUnitDoesNotSetNoNewPrivileges(t *testing.T) {
	// The main service exists to run privileged host commands via bash_execute;
	// NoNewPrivileges would break sudo-style escalation inside tool calls.
	// (The recorder unit, which never executes tools, does set it.)
	p := DefaultPlan()
	p.ExecPath = "/usr/local/bin/mymcp"
	if strings.Contains(RenderUnit(p), "NoNewPrivileges") {
		t.Fatal("main unit must not set NoNewPrivileges")
	}
}
