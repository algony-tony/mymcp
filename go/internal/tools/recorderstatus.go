package tools

import (
	"bufio"
	"encoding/json"
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"syscall"
	"time"

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

// lastUpdatedRe matches the header line the sidecar stamps on every write —
// see _build_header in src/mymcp/recorder/merge_cycle.py and stamp_last_updated
// in src/mymcp/recorder/overview.py.
var lastUpdatedRe = regexp.MustCompile(`(?m)^_Last updated: ([^_]+)_\s*$`)

// lastUpdatedLayouts. Every overview.md that OverviewStore.write_overview
// produces goes through _stamp_last_updated (src/mymcp/recorder/overview.py:
// 84-104), which unconditionally strips any existing "_Last updated: ..._"
// line and replaces it with
// datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00",
// "Z") — e.g. "2026-07-13T02:08:00Z". That is the only format a real,
// sidecar-written file will ever contain; time.RFC3339 matches it. The other
// three layouts ("2006-01-02 15:04 MST" — merge_cycle's _build_header, which
// is overwritten by the stamp above before anything hits disk — and two
// looser ISO8601 variants) match nothing produced today but cost nothing to
// tolerate for hand-edited or older-format files, so they stay.
var lastUpdatedLayouts = []string{
	"2006-01-02 15:04 MST",
	time.RFC3339,
	"2006-01-02T15:04:05.999999-07:00",
	"2006-01-02T15:04:05",
}

// RecorderStatus is the freshness of the on-disk overview, derived entirely
// from files: the core cannot see the sidecar's in-memory state (the sidecar
// serves no HTTP and pushes metrics via OTLP).
type RecorderStatus struct {
	// LastUpdated is when the overview was last written. Zero if unknown.
	LastUpdated time.Time
	// LastUpdatedRaw is the header's verbatim text, empty if it was absent or
	// unparseable and LastUpdated came from the file's mtime instead.
	LastUpdatedRaw string
	// PendingEvents is the unconsumed mutating-event backlog.
	PendingEvents int
	// Stale is PendingEvents > 0 AND LastUpdated older than 2x the merge
	// interval. Both conjuncts matter: an idle server has no backlog and is
	// never stale, which is the false positive the metrics-based version of
	// this check originally had.
	Stale bool
	// StaleMinutes is the age of LastUpdated in whole minutes; 0 unless Stale.
	StaleMinutes int
}

func parseLastUpdated(body []byte) (time.Time, string) {
	m := lastUpdatedRe.FindSubmatch(body)
	if m == nil {
		return time.Time{}, ""
	}
	raw := strings.TrimSpace(string(m[1]))
	for _, layout := range lastUpdatedLayouts {
		if ts, err := time.Parse(layout, raw); err == nil {
			return ts.UTC(), raw
		}
	}
	return time.Time{}, ""
}

// recorderStatusFor derives freshness for the overview at overviewPath. A
// missing or unreadable overview yields the zero RecorderStatus (never stale) —
// that case is already reported by ServerOverview's RecorderDisabled branch.
func recorderStatusFor(cfg *config.Config, overviewPath string, now time.Time) RecorderStatus {
	var st RecorderStatus
	body, err := os.ReadFile(overviewPath)
	if err != nil {
		return st
	}
	st.LastUpdated, st.LastUpdatedRaw = parseLastUpdated(body)
	if st.LastUpdated.IsZero() {
		if fi, err := os.Stat(overviewPath); err == nil {
			st.LastUpdated = fi.ModTime().UTC()
		}
	}
	st.PendingEvents = pendingEvents(cfg)

	interval := cfg.RecorderMergeIntervalSec
	if interval <= 0 {
		interval = 300
	}
	// 2x the interval so a single slow cycle is not flagged — the same
	// threshold v2's in-process banner used, before it was ported here and
	// the original was deleted.
	threshold := time.Duration(2*interval) * time.Second
	age := now.Sub(st.LastUpdated)
	if st.PendingEvents > 0 && !st.LastUpdated.IsZero() && age > threshold {
		st.Stale = true
		st.StaleMinutes = int(age.Minutes())
	}
	return st
}
