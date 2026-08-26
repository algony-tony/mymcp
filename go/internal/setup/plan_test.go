package setup

import "testing"

func TestDefaultPlanMatchesSpecDefaults(t *testing.T) {
	p := DefaultPlan()
	if p.Bind != "0.0.0.0" || p.Port != 8765 {
		t.Fatalf("bind/port = %s:%d, want 0.0.0.0:8765", p.Bind, p.Port)
	}
	if p.ServiceUser != "root" {
		t.Fatalf("ServiceUser = %q, want root", p.ServiceUser)
	}
	if p.ConfigDir != "/etc/mymcp" || p.LogDir != "/var/log/mymcp" {
		t.Fatalf("dirs = %s, %s", p.ConfigDir, p.LogDir)
	}
	if p.RecorderDataDir != "/var/lib/mymcp/recorder" {
		t.Fatalf("RecorderDataDir = %s", p.RecorderDataDir)
	}
	if !p.AuditEnabled {
		t.Fatal("AuditEnabled must default true (config.go defaults it false)")
	}
	if p.ClientName != "default" || p.ClientRole != "rw" {
		t.Fatalf("client = %s/%s", p.ClientName, p.ClientRole)
	}
	if p.Recorder.Enabled {
		t.Fatal("recorder must default off")
	}
	if !p.Start {
		t.Fatal("Start must default true")
	}
}

func TestPlanDerivedPaths(t *testing.T) {
	p := DefaultPlan()
	p.ConfigDir = "/tmp/x"
	if got := p.EnvPath(); got != "/tmp/x/.env" {
		t.Fatalf("EnvPath = %s", got)
	}
	if got := p.TokenPath(); got != "/tmp/x/tokens.json" {
		t.Fatalf("TokenPath = %s", got)
	}
	if got := p.UnitPath(); got != "/etc/systemd/system/mymcp.service" {
		t.Fatalf("UnitPath = %s", got)
	}
	if got := p.RecorderUnitPath(); got != "/etc/systemd/system/mymcp-recorder.service" {
		t.Fatalf("RecorderUnitPath = %s", got)
	}
}
