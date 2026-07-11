package tools

import (
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
		return map[string]any{
			"success": false, "error": "RecorderDisabled",
			"message": "server_overview requires MYMCP_RECORDER_ENABLED=true",
		}
	}
	return map[string]any{"success": true, "overview": fsutil.DecodeReplace(raw)}
}
