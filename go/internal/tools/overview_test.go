package tools

import (
	"os"
	"path/filepath"
	"testing"
)

func TestServerOverviewAbsentReturnsRecorderDisabled(t *testing.T) {
	d := testDeps(t)
	d.Cfg.RecorderDataDir = t.TempDir() // no overview/overview.md
	res := ServerOverview(d)
	if res["success"] != false || res["error"] != "RecorderDisabled" {
		t.Fatalf("res = %v", res)
	}
	if res["message"] != "server_overview requires MYMCP_RECORDER_ENABLED=true" {
		t.Fatalf("message = %v", res["message"])
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
