package tools

import (
	"fmt"
	"os"
	"path/filepath"
	"time"

	"github.com/algony-tony/mymcp/go/internal/fsutil"
)

// ServerOverview returns the recorder-maintained overview. In the v3 sidecar
// model the core reads <recorder_data_dir>/overview/overview.md written by the
// mymcp-recorder process; when absent it returns the Python core's
// RecorderDisabled shape so the compat gate (recorder disabled) matches.
//
// On success the result also carries freshness derived from disk by
// recorderStatusFor: last_updated (RFC3339, empty if unknown), pending_events
// (the unconsumed mutating-event backlog), and stale. When stale is true the
// overview body is additionally prefixed with a warning banner — the fields
// serve programmatic callers, the banner serves a model that reads only the
// prose, and issue #92 is why both are needed.
func ServerOverview(d Deps) map[string]any {
	path := filepath.Join(d.Cfg.RecorderDataDir, "overview", "overview.md")
	raw, err := os.ReadFile(path)
	if err != nil {
		// The error code stays RecorderDisabled for compat (the Python core's
		// shape); only the message distinguishes the causes. "File absent" is
		// overwhelmingly "the sidecar was never started" — issue #92 — so the
		// message names the sidecar rather than a config flag the Go core does
		// not gate on.
		msg := fmt.Sprintf("overview not generated yet at %s — is the recorder "+
			"sidecar running? Check: systemctl status mymcp-recorder", path)
		if !os.IsNotExist(err) {
			msg = fmt.Sprintf("could not read overview at %s: %v", path, err)
		}
		return map[string]any{"success": false, "error": "RecorderDisabled", "message": msg}
	}
	st := recorderStatusFor(d.Cfg, path, time.Now())
	body := fsutil.DecodeReplace(raw)
	if st.Stale {
		// Prefix rather than replace: a model that reads only the prose still
		// sees this, which is the whole point — issue #92 was a frozen overview
		// being consumed as current fact for four weeks.
		body = fmt.Sprintf("_⚠️ %d events pending; recorder overview stale for %d"+
			" minutes — check: systemctl status mymcp-recorder_\n\n%s",
			st.PendingEvents, st.StaleMinutes, body)
	}
	// Always emit RFC3339 here regardless of which source (header vs. mtime
	// fallback) LastUpdated came from — a structured consumer parsing this
	// field with one fixed format must not see two different formats.
	// LastUpdatedRaw stays on the struct for internal use; it does not leak
	// out of this tool.
	lastUpdated := ""
	if !st.LastUpdated.IsZero() {
		lastUpdated = st.LastUpdated.Format(time.RFC3339)
	}
	return map[string]any{
		"success":        true,
		"overview":       body,
		"last_updated":   lastUpdated,
		"pending_events": st.PendingEvents,
		"stale":          st.Stale,
	}
}
