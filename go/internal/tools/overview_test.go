package tools

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestServerOverviewAbsentPointsAtSidecar(t *testing.T) {
	d := testDeps(t)
	d.Cfg.RecorderDataDir = t.TempDir() // no overview/overview.md
	res := ServerOverview(d)
	if res["success"] != false || res["error"] != "RecorderDisabled" {
		t.Fatalf("res = %v", res)
	}
	msg, _ := res["message"].(string)
	if !strings.Contains(msg, "systemctl status mymcp-recorder") {
		t.Fatalf("message should name the sidecar, got %q", msg)
	}
	if strings.Contains(msg, "MYMCP_RECORDER_ENABLED") {
		t.Fatalf("absent-file message must not blame the config flag, got %q", msg)
	}
}

func TestServerOverviewUnreadableReportsReadFailure(t *testing.T) {
	if os.Geteuid() == 0 {
		t.Skip("root bypasses file permissions")
	}
	dir := t.TempDir()
	ov := filepath.Join(dir, "overview")
	if err := os.MkdirAll(ov, 0o755); err != nil {
		t.Fatal(err)
	}
	path := filepath.Join(ov, "overview.md")
	if err := os.WriteFile(path, []byte("# Server\n"), 0o000); err != nil {
		t.Fatal(err)
	}
	d := testDeps(t)
	d.Cfg.RecorderDataDir = dir
	res := ServerOverview(d)
	if res["success"] != false || res["error"] != "RecorderDisabled" {
		t.Fatalf("res = %v", res)
	}
	msg, _ := res["message"].(string)
	if !strings.Contains(msg, path) {
		t.Fatalf("unreadable message should name the path, got %q", msg)
	}
	if strings.Contains(msg, "systemctl") {
		t.Fatalf("unreadable message must not claim the sidecar never ran, got %q", msg)
	}
}

func TestServerOverviewPresentReturnsContent(t *testing.T) {
	d := testDeps(t)
	dir := t.TempDir()
	d.Cfg.RecorderDataDir = dir
	ov := filepath.Join(dir, "overview")
	os.MkdirAll(ov, 0o755)
	os.WriteFile(filepath.Join(ov, "overview.md"), []byte("# Server\nstuff\n"), 0o644)
	res := ServerOverview(d)
	if res["success"] != true || res["overview"] != "# Server\nstuff\n" {
		t.Fatalf("res = %v", res)
	}
}
