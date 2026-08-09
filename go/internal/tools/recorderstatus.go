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
// Cursor.load() in cursor.py. It does not clamp a negative offset: Python's
// Cursor.load() doesn't either, and a negative offset is left to reach
// f.Seek in countFrom, where it fails and the file counts as 0 — the same
// fail-safe path Python takes via the OSError from f.seek(). Clamping it to
// 0 here would instead recount the whole file as pending.
func loadCursor(dataDir string) recorderCursor {
	var c recorderCursor
	raw, err := os.ReadFile(filepath.Join(dataDir, "cursor.json"))
	if err != nil {
		return recorderCursor{}
	}
	if err := json.Unmarshal(raw, &c); err != nil {
		return recorderCursor{}
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
// EOF. It reads line-by-line via bufio.Reader.ReadString rather than
// bufio.Scanner: Scanner permanently stops the whole scan — silently
// dropping every line after, not just the offending one — the moment a
// single line exceeds its (necessarily finite) buffer. ReadString has no
// such limit, matching events.py's _count_from, which iterates every line
// unconditionally and only skips per-line on JSONDecodeError. A negative or
// out-of-range byteOffset makes Seek fail, and the whole file counts as 0 —
// unreadable files and malformed lines contribute zero rather than failing:
// this feeds an advisory freshness signal, never a tool's success.
func countFrom(path string, byteOffset int64) int {
	f, err := os.Open(path)
	if err != nil {
		return 0
	}
	defer f.Close()
	if _, err := f.Seek(byteOffset, 0); err != nil {
		return 0
	}
	count := 0
	r := bufio.NewReader(f)
	for {
		raw, readErr := r.ReadString('\n')
		line := strings.TrimSpace(raw)
		if line != "" {
			var entry map[string]any
			// Non-object JSON ("42", "[1,2]") fails to unmarshal into a map,
			// which is the behaviour we want — events.py skips those
			// explicitly.
			if err := json.Unmarshal([]byte(line), &entry); err == nil {
				result, _ := entry["result"].(string)
				tool, _ := entry["tool"].(string)
				if successResults[result] && mutatingTools[tool] {
					count++
				}
			}
		}
		if readErr != nil {
			// io.EOF is the normal end of file; ReadString still returns
			// whatever it read of a final, unterminated line alongside it,
			// which is handled above before we break. Any other read error
			// also stops here, keeping the partial count gathered so far.
			break
		}
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
