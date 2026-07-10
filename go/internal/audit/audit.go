// Package audit writes JSON-lines audit records with size-based rotation,
// byte-compatible with the Python core (src/mymcp/audit.py). Rotation matches
// logging.handlers.RotatingFileHandler: rollover when the current size plus the
// incoming record (incl. newline) meets maxBytes, backups named audit.log.1,
// audit.log.2, … (higher = older) so the recorder EventTailer's rotation path
// (src/mymcp/recorder/events.py) works unchanged.
package audit

import (
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"sync"

	"github.com/algony-tony/mymcp/go/internal/fsutil"
)

// Entry mirrors the audit JSON object. Field order matches Python insertion
// order (cosmetic — the tailer parses by key). Params is always emitted (even
// {}); other optionals use omitempty. DurationMs is a pointer so a genuine 0 is
// emitted while an unset duration (denied calls) is omitted, matching Python.
type Entry struct {
	TS           string         `json:"ts"`
	TokenName    string         `json:"token_name"`
	Role         string         `json:"role"`
	IP           string         `json:"ip"`
	Tool         string         `json:"tool"`
	Params       map[string]any `json:"params"`
	Result       string         `json:"result"`
	RequestID    string         `json:"request_id,omitempty"`
	Reason       string         `json:"reason,omitempty"`
	ErrorCode    string         `json:"error_code,omitempty"`
	ErrorMessage string         `json:"error_message,omitempty"`
	DurationMs   *int           `json:"duration_ms,omitempty"`
	Output       map[string]any `json:"output,omitempty"`
}

// Writer is a thread-safe rotating JSON-lines writer.
type Writer struct {
	mu          sync.Mutex
	enabled     bool
	path        string
	maxBytes    int64
	backupCount int
	f           *os.File
	size        int64
}

// New opens the writer. When enabled is false the writer is a no-op that never
// touches the filesystem (audit disabled → src/mymcp/audit.py returns None).
func New(enabled bool, logDir string, maxBytes int64, backupCount int) (*Writer, error) {
	w := &Writer{enabled: enabled, maxBytes: maxBytes, backupCount: backupCount}
	if !enabled {
		return w, nil
	}
	if err := os.MkdirAll(logDir, 0o755); err != nil {
		return nil, err
	}
	w.path = filepath.Join(logDir, "audit.log")
	if err := w.open(); err != nil {
		return nil, err
	}
	return w, nil
}

func (w *Writer) open() error {
	f, err := os.OpenFile(w.path, os.O_CREATE|os.O_WRONLY|os.O_APPEND, 0o644)
	if err != nil {
		return err
	}
	st, err := f.Stat()
	if err != nil {
		f.Close()
		return err
	}
	w.f = f
	w.size = st.Size()
	return nil
}

// Log serializes and appends one record. Returns an error on any write/rotate
// failure; the caller increments mymcp_audit_write_failures_total and returns
// InternalError to the client (silent audit loss is a SOC red line).
func (w *Writer) Log(e Entry) error {
	if !w.enabled {
		return nil
	}
	raw, err := json.Marshal(e)
	if err != nil {
		return err
	}
	w.mu.Lock()
	defer w.mu.Unlock()
	// RotatingFileHandler.shouldRollover compares size + len(msg+"\n") >= maxBytes.
	if w.maxBytes > 0 && w.size+int64(len(raw))+1 >= w.maxBytes {
		if err := w.rotate(); err != nil {
			return err
		}
	}
	n, err := w.f.Write(append(raw, '\n'))
	w.size += int64(n)
	return err
}

// rotate mirrors RotatingFileHandler.doRollover: close, shift audit.log.i →
// audit.log.(i+1), audit.log → audit.log.1, reopen fresh.
func (w *Writer) rotate() error {
	if w.f != nil {
		w.f.Close()
		w.f = nil
	}
	if w.backupCount > 0 {
		for i := w.backupCount - 1; i >= 1; i-- {
			src := fmt.Sprintf("%s.%d", w.path, i)
			dst := fmt.Sprintf("%s.%d", w.path, i+1)
			if _, err := os.Stat(src); err == nil {
				_ = os.Rename(src, dst) // best-effort, as in stdlib logging
			}
		}
		_ = os.Rename(w.path, w.path+".1")
	}
	return w.open()
}

// Close flushes and releases the file (no-op when disabled).
func (w *Writer) Close() error {
	w.mu.Lock()
	defer w.mu.Unlock()
	if w.f == nil {
		return nil
	}
	err := w.f.Close()
	w.f = nil
	return err
}

// --- output summaries (port of src/mymcp/audit_output.py) ---

// TruncateBashOutput summarises stdout into head/tail with a sha256 of the whole.
func TruncateBashOutput(raw []byte, headBytes, tailBytes int) map[string]any {
	total := len(raw)
	sum := sha256.Sum256(raw)
	sha := hex.EncodeToString(sum[:])
	if total <= headBytes+tailBytes {
		return map[string]any{
			"stdout_head": fsutil.DecodeReplace(raw), "stdout_tail": "",
			"stdout_truncated_bytes": 0, "stdout_sha256": sha,
		}
	}
	return map[string]any{
		"stdout_head":            fsutil.DecodeReplace(raw[:headBytes]),
		"stdout_tail":            fsutil.DecodeReplace(raw[total-tailBytes:]),
		"stdout_truncated_bytes": total - headBytes - tailBytes,
		"stdout_sha256":          sha,
	}
}

// WriteFileOutput summarises a write_file effect.
func WriteFileOutput(path string, content []byte) map[string]any {
	firstLine := ""
	if len(content) > 0 {
		if i := bytes.IndexByte(content, '\n'); i >= 0 {
			firstLine = fsutil.DecodeReplace(content[:i])
		} else {
			firstLine = fsutil.DecodeReplace(content)
		}
	}
	sum := sha256.Sum256(content)
	return map[string]any{
		"path": path, "size_bytes": len(content),
		"sha256": hex.EncodeToString(sum[:]), "first_line": firstLine,
	}
}

// EditFileOutput summarises an edit_file effect.
func EditFileOutput(path string, linesAdded, linesRemoved, hunkCount int) map[string]any {
	return map[string]any{
		"path": path, "lines_added": linesAdded,
		"lines_removed": linesRemoved, "hunk_count": hunkCount,
	}
}
