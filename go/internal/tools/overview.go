package tools

import (
	"fmt"
	"os"
	"path/filepath"

	"github.com/algony-tony/mymcp/go/internal/fsutil"
)

// ServerOverview returns the recorder-maintained overview. In the v3 sidecar
// model the core reads <recorder_data_dir>/overview/overview.md written by the
// mymcp-recorder process; when absent it returns the Python core's
// RecorderDisabled shape so the compat gate (recorder disabled) matches.
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
	return map[string]any{"success": true, "overview": fsutil.DecodeReplace(raw)}
}
