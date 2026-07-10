package tools

import (
	"bytes"
	"errors"
	"fmt"
	"io/fs"
	"os/exec"
	"sync"
	"syscall"
	"time"

	"github.com/algony-tony/mymcp/go/internal/fsutil"
)

var (
	inflightMu sync.Mutex
	inflight   = map[*exec.Cmd]struct{}{}
)

func trackProcess(c *exec.Cmd) {
	inflightMu.Lock()
	inflight[c] = struct{}{}
	inflightMu.Unlock()
}

func untrackProcess(c *exec.Cmd) {
	inflightMu.Lock()
	delete(inflight, c)
	inflightMu.Unlock()
}

func stillTracked(c *exec.Cmd) bool {
	inflightMu.Lock()
	defer inflightMu.Unlock()
	_, ok := inflight[c]
	return ok
}

// InflightCount is the live count of tracked bash subprocesses (metrics gauge).
func InflightCount() int {
	inflightMu.Lock()
	defer inflightMu.Unlock()
	return len(inflight)
}

// signalProcessGroup sends sig to the child's process group. With Setsid the
// child leads its own group (pgid == pid). If the child unexpectedly shares our
// group, fall back to a per-process signal so we never SIGTERM the server —
// parity with _signal_process_tree in src/mymcp/tools/bash.py.
func signalProcessGroup(c *exec.Cmd, sig syscall.Signal) {
	if c.Process == nil {
		return
	}
	pgid, err := syscall.Getpgid(c.Process.Pid)
	if err != nil {
		return
	}
	if pgid == syscall.Getpgrp() {
		_ = c.Process.Signal(sig)
		return
	}
	_ = syscall.Kill(-pgid, sig)
}

// ShutdownInflight SIGTERMs every tracked process group, waits graceSec, then
// SIGKILLs survivors. Safe to call from the shutdown path; mirrors
// shutdown_inflight_processes in src/mymcp/tools/bash.py.
func ShutdownInflight(graceSec int) {
	inflightMu.Lock()
	snapshot := make([]*exec.Cmd, 0, len(inflight))
	for c := range inflight {
		snapshot = append(snapshot, c)
	}
	inflightMu.Unlock()

	for _, c := range snapshot {
		signalProcessGroup(c, syscall.SIGTERM)
	}
	if graceSec < 0 {
		graceSec = 0
	}
	deadline := time.Now().Add(time.Duration(graceSec) * time.Second)
	for time.Now().Before(deadline) {
		if allExited(snapshot) {
			return
		}
		time.Sleep(50 * time.Millisecond)
	}
	for _, c := range snapshot {
		signalProcessGroup(c, syscall.SIGKILL)
	}
}

func allExited(cmds []*exec.Cmd) bool {
	for _, c := range cmds {
		if stillTracked(c) {
			return false
		}
	}
	return true
}

// RunBash runs command via /bin/sh -c in its own session, capturing stdout and
// stderr, each truncated to maxOutputBytes. Return keys are the compat contract.
func RunBash(d Deps, command string, timeout int, workingDir string, maxOutputBytes int) map[string]any {
	if timeout < 1 {
		timeout = 1
	}
	if timeout > 600 {
		timeout = 600
	}
	if maxOutputBytes < 1 {
		maxOutputBytes = 1
	}
	if maxOutputBytes > d.Cfg.BashMaxOutputBytesHard {
		maxOutputBytes = d.Cfg.BashMaxOutputBytesHard
	}

	cmd := exec.Command("/bin/sh", "-c", command)
	cmd.Dir = workingDir
	cmd.SysProcAttr = &syscall.SysProcAttr{Setsid: true}
	var stdout, stderr bytes.Buffer
	cmd.Stdout = &stdout
	cmd.Stderr = &stderr

	if err := cmd.Start(); err != nil {
		switch {
		case errors.Is(err, fs.ErrNotExist):
			return map[string]any{
				"success": false, "error": "FileNotFoundError",
				"message":    "Working directory not found: " + workingDir,
				"suggestion": "Check that the working_dir path exists",
			}
		case errors.Is(err, fs.ErrPermission):
			return map[string]any{
				"success": false, "error": "PermissionError",
				"message": err.Error(), "suggestion": "Check directory permissions",
			}
		default:
			return map[string]any{"success": false, "error": "OSError", "message": err.Error()}
		}
	}

	trackProcess(cmd)
	defer untrackProcess(cmd)

	done := make(chan struct{})
	go func() { _ = cmd.Wait(); close(done) }()

	select {
	case <-time.After(time.Duration(timeout) * time.Second):
		signalProcessGroup(cmd, syscall.SIGTERM)
		select {
		case <-done:
		case <-time.After(2 * time.Second):
			signalProcessGroup(cmd, syscall.SIGKILL)
			<-done
		}
		return map[string]any{
			"stdout": "", "stderr": fmt.Sprintf("Command timed out after %ds", timeout),
			"exit_code": -1, "timed_out": true,
		}
	case <-done:
	}

	return map[string]any{
		"stdout":    truncateOutput(stdout.Bytes(), maxOutputBytes),
		"stderr":    truncateOutput(stderr.Bytes(), maxOutputBytes),
		"exit_code": cmd.ProcessState.ExitCode(),
		"timed_out": false,
	}
}

func truncateOutput(data []byte, limit int) string {
	if len(data) <= limit {
		return fsutil.DecodeReplace(data)
	}
	shown := fsutil.DecodeReplace(data[:limit])
	return fmt.Sprintf("%s\n[TRUNCATED: total %d bytes, showing first %d bytes]", shown, len(data), limit)
}
