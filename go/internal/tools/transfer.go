package tools

import (
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"time"

	"github.com/algony-tony/mymcp/go/internal/fsutil"
)

func publicBaseURL(d Deps) string {
	return strings.TrimRight(d.Cfg.PublicBaseURL, "/")
}

func buildTransferURL(d Deps, ticketID string) string {
	base := publicBaseURL(d)
	if base == "" {
		return "/files/raw/" + ticketID
	}
	return base + "/files/raw/" + ticketID
}

func isoUTC(unixSec int64) string {
	return time.Unix(unixSec, 0).UTC().Format("2006-01-02T15:04:05Z")
}

// PrepareUpload ports prepare_upload: mint a single-use PUT ticket. createdBy/
// createdByRole are the issuer's identity, stamped on the ticket for audit.
func PrepareUpload(d Deps, destPath string, maxBytes *int64, expiresIn *int, overwrite bool, createdBy, createdByRole string) map[string]any {
	if !d.Cfg.TransferEnabled {
		return map[string]any{"success": false, "error": "TransferDisabled",
			"message": "File transfer feature is disabled on this server."}
	}
	if !filepath.IsAbs(destPath) {
		return map[string]any{"success": false, "error": "InvalidPath",
			"message": "dest_path must be an absolute path."}
	}
	if msg := fsutil.CheckProtectedPath(destPath, fsutil.ModeWrite, d.Protected); msg != "" {
		return map[string]any{"success": false, "error": "ProtectedPath", "message": msg}
	}
	if !overwrite {
		if _, err := os.Stat(destPath); err == nil {
			return map[string]any{"success": false, "error": "FileExists",
				"message": fmt.Sprintf("%s already exists and overwrite=False.", destPath)}
		}
	}
	limit := d.Cfg.TransferMaxBytes
	requested := limit
	if maxBytes != nil {
		requested = *maxBytes
	}
	if requested <= 0 {
		return map[string]any{"success": false, "error": "InvalidMaxBytes", "message": "max_bytes must be positive."}
	}
	effectiveMax := requested
	if effectiveMax > limit {
		effectiveMax = limit
	}
	ttl := d.Cfg.TransferDefaultTTLSec
	if expiresIn != nil {
		ttl = *expiresIn
	}
	if ttl <= 0 {
		return map[string]any{"success": false, "error": "InvalidExpiresIn", "message": "expires_in must be positive."}
	}
	if ttl > d.Cfg.TransferMaxTTLSec {
		ttl = d.Cfg.TransferMaxTTLSec
	}
	tk := d.Tickets.Mint("upload", destPath, effectiveMax, ttl, createdBy, createdByRole)
	url := buildTransferURL(d, tk.TicketID)
	return map[string]any{
		"success": true, "url": url, "method": "PUT", "ticket": tk.TicketID,
		"expires_in": ttl, "expires_at": isoUTC(tk.ExpiresAt), "max_bytes": effectiveMax,
		"dest_path":    destPath,
		"curl_example": fmt.Sprintf("curl -fsS -T /local/path/to/file '%s'", url),
		"instructions": "Run the curl above from the MCP client's local shell. " +
			"The file's raw bytes go in the request body. On success the " +
			"server returns {\"ok\": true, \"path\": \"...\", \"bytes_written\": N}.",
		"on_error": "If the URL returns 4xx, read the JSON error.hint field. " +
			"Tickets are single-use; do not retry the same URL — " +
			"call prepare_upload again to mint a fresh one.",
	}
}

// PrepareDownload ports prepare_download: mint a single-use GET ticket.
func PrepareDownload(d Deps, srcPath string, expiresIn *int, createdBy, createdByRole string) map[string]any {
	if !d.Cfg.TransferEnabled {
		return map[string]any{"success": false, "error": "TransferDisabled",
			"message": "File transfer feature is disabled on this server."}
	}
	if !filepath.IsAbs(srcPath) {
		return map[string]any{"success": false, "error": "InvalidPath",
			"message": "src_path must be an absolute path."}
	}
	if msg := fsutil.CheckProtectedPath(srcPath, fsutil.ModeRead, d.Protected); msg != "" {
		return map[string]any{"success": false, "error": "ProtectedPath", "message": msg}
	}
	st, err := os.Stat(srcPath)
	if err != nil {
		return map[string]any{"success": false, "error": "FileNotFound",
			"message": fmt.Sprintf("%s does not exist.", srcPath)}
	}
	if st.IsDir() {
		return map[string]any{"success": false, "error": "NotARegularFile",
			"message": fmt.Sprintf("%s is not a regular file.", srcPath)}
	}
	ttl := d.Cfg.TransferDefaultTTLSec
	if expiresIn != nil {
		ttl = *expiresIn
	}
	if ttl <= 0 {
		return map[string]any{"success": false, "error": "InvalidExpiresIn", "message": "expires_in must be positive."}
	}
	if ttl > d.Cfg.TransferMaxTTLSec {
		ttl = d.Cfg.TransferMaxTTLSec
	}
	size := st.Size()
	tk := d.Tickets.Mint("download", srcPath, size, ttl, createdBy, createdByRole)
	url := buildTransferURL(d, tk.TicketID)
	return map[string]any{
		"success": true, "url": url, "method": "GET", "ticket": tk.TicketID,
		"expires_in": ttl, "expires_at": isoUTC(tk.ExpiresAt), "size": size, "src_path": srcPath,
		"curl_example": fmt.Sprintf("curl -fsS '%s' -o /local/path/%s", url, filepath.Base(srcPath)),
		"instructions": "Run the curl above from the MCP client's local shell. " +
			"Bytes stream back as the response body.",
		"on_error": "If the URL returns 4xx, read the JSON error.hint field. " +
			"Tickets are single-use; mint a new one with prepare_download if needed.",
	}
}
