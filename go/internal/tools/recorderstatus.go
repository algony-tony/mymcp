package tools

import (
	"bufio"
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"syscall"

	"github.com/algony-tony/mymcp/go/internal/config"
)

// mutatingTools is a port of MUTATING_TOOLS in src/mymcp/recorder/events.py.
//
// It is deliberately NOT mcpserver.writeTools. The recorder's set has two
// entries the permission model does not: prepare_download (classified
// read-only for auth, but it still hands out host bytes) and transfer_upload
// (the *endpoint* audit name for a redeemed upload ticket, which is not an MCP
// tool and so can never appear in writeTools). Counting with writeTools would
// report a backlog different from the one the sidecar actually drains.
//
// Keep in sync with events.py; a tool added there but not here silently
// under-counts the backlog.
var mutatingTools = map[string]bool{
	"bash_execute":     true,
	"write_file":       true,
	"edit_file":        true,
	"prepare_upload":   true,
	"prepare_download": true,
	"transfer_upload":  true,
}

// successResults mirrors _SUCCESS_RESULTS in events.py: the core writes "ok",
// and "success" is tolerated for forward-compat.
var successResults = map[string]bool{"ok": true, "success": true}

// recorderCursor is the on-disk shape of <recorder_data_dir>/cursor.json,
// written by src/mymcp/recorder/cursor.py.
type recorderCursor struct {
	File   string `json:"file"`
	Inode  uint64 `json:"inode"`
	Offset int64  `json:"offset"`
}

// loadCursor reads cursor.json. A missing or corrupt cursor yields the zero
// value, which means "nothing consumed yet" — the same fallback as
// Cursor.load() in cursor.py.
func loadCursor(dataDir string) recorderCursor {
	var c recorderCursor
	raw, err := os.ReadFile(filepath.Join(dataDir, "cursor.json"))
	if err != nil {
		return recorderCursor{}
	}
	if err := json.Unmarshal(raw, &c); err != nil {
		return recorderCursor{}
	}
	if c.Offset < 0 {
		c.Offset = 0
	}
	return c
}

func inodeOf(path string) (uint64, bool) {
	st, err := os.Stat(path)
	if err != nil {
		return 0, false
	}
	sys, ok := st.Sys().(*syscall.Stat_t)
	if !ok {
		return 0, false
	}
	return sys.Ino, true
}

// countFrom counts mutating+successful audit events in path from byteOffset to
// EOF. Unreadable files and malformed lines contribute zero rather than
// failing: this feeds an advisory freshness signal, never a tool's success.
func countFrom(path string, byteOffset int64) int {
	f, err := os.Open(path)
	if err != nil {
		return 0
	}
	defer f.Close()
	if byteOffset > 0 {
		if _, err := f.Seek(byteOffset, 0); err != nil {
			return 0
		}
	}
	count := 0
	sc := bufio.NewScanner(f)
	// Audit lines carry truncated tool output and can exceed the 64KB default.
	sc.Buffer(make([]byte, 0, 64*1024), 8*1024*1024)
	for sc.Scan() {
		line := strings.TrimSpace(sc.Text())
		if line == "" {
			continue
		}
		var entry map[string]any
		// Non-object JSON ("42", "[1,2]") fails to unmarshal into a map, which
		// is the behaviour we want — events.py skips those explicitly.
		if err := json.Unmarshal([]byte(line), &entry); err != nil {
			continue
		}
		result, _ := entry["result"].(string)
		if !successResults[result] {
			continue
		}
		tool, _ := entry["tool"].(string)
		if !mutatingTools[tool] {
			continue
		}
		count++
	}
	return count
}

// pendingEvents reports how many mutating+successful audit events sit past the
// recorder's committed cursor. It is the Go port of EventTailer.pending_count
// (src/mymcp/recorder/events.py:162), including the rotation branch: when the
// live audit.log's inode no longer matches the cursor's, the unread tail of
// audit.log.1 is counted first and the new file is counted from its head.
//
// Every failure path returns 0 — "no known backlog" — so a freshness probe can
// never make a tool call fail.
func pendingEvents(cfg *config.Config) int {
	logPath := filepath.Join(cfg.AuditLogDir, "audit.log")
	liveInode, ok := inodeOf(logPath)
	if !ok {
		return 0
	}
	cur := loadCursor(cfg.RecorderDataDir)
	if cur.Inode != 0 && cur.Inode != liveInode {
		count := 0
		rotated := filepath.Join(cfg.AuditLogDir, "audit.log.1")
		if rotInode, ok := inodeOf(rotated); ok && rotInode == cur.Inode {
			count += countFrom(rotated, cur.Offset)
		}
		return count + countFrom(logPath, 0)
	}
	return countFrom(logPath, cur.Offset)
}
