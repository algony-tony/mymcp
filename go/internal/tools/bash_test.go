package tools

import (
	"strings"
	"testing"
	"time"
)

func TestRunBashBasic(t *testing.T) {
	d := testDeps(t)
	res := RunBash(d, "printf 'hi'", 30, "/", d.Cfg.BashMaxOutputBytes)
	if res["stdout"] != "hi" || res["exit_code"] != 0 || res["timed_out"] != false {
		t.Fatalf("res = %v", res)
	}
}

func TestRunBashNonZeroExit(t *testing.T) {
	d := testDeps(t)
	res := RunBash(d, "exit 3", 30, "/", d.Cfg.BashMaxOutputBytes)
	if res["exit_code"] != 3 || res["timed_out"] != false {
		t.Fatalf("res = %v", res)
	}
}

func TestRunBashTimeout(t *testing.T) {
	d := testDeps(t)
	start := time.Now()
	res := RunBash(d, "sleep 5", 1, "/", d.Cfg.BashMaxOutputBytes)
	if res["timed_out"] != true || res["exit_code"] != -1 {
		t.Fatalf("res = %v", res)
	}
	if res["stderr"] != "Command timed out after 1s" {
		t.Fatalf("stderr = %v", res["stderr"])
	}
	if time.Since(start) > 4*time.Second {
		t.Fatalf("timeout took too long: %v", time.Since(start))
	}
}

func TestRunBashOutputTruncation(t *testing.T) {
	d := testDeps(t)
	res := RunBash(d, "printf 'aaaaaaaaaa'", 30, "/", 4) // limit 4
	out := res["stdout"].(string)
	if !strings.HasPrefix(out, "aaaa\n[TRUNCATED: total 10 bytes, showing first 4 bytes]") {
		t.Fatalf("truncation wrong: %q", out)
	}
}

func TestRunBashBadWorkingDir(t *testing.T) {
	d := testDeps(t)
	res := RunBash(d, "true", 30, "/no/such/dir/xyz", d.Cfg.BashMaxOutputBytes)
	if res["success"] != false || res["error"] != "FileNotFoundError" {
		t.Fatalf("res = %v", res)
	}
}

func TestShutdownInflightKillsProcessGroup(t *testing.T) {
	d := testDeps(t)
	done := make(chan map[string]any, 1)
	go func() { done <- RunBash(d, "sleep 30", 600, "/", d.Cfg.BashMaxOutputBytes) }()
	// Wait for the child to register.
	deadline := time.Now().Add(2 * time.Second)
	for InflightCount() == 0 && time.Now().Before(deadline) {
		time.Sleep(10 * time.Millisecond)
	}
	if InflightCount() == 0 {
		t.Fatal("child never registered in the in-flight table")
	}
	ShutdownInflight(1) // TERM, 1s grace, KILL
	select {
	case res := <-done:
		// Killed process: negative/-1 exit or timed_out — either way it returned.
		_ = res
	case <-time.After(5 * time.Second):
		t.Fatal("ShutdownInflight did not stop the sleeping child")
	}
	if InflightCount() != 0 {
		t.Fatalf("in-flight table not drained: %d", InflightCount())
	}
}
