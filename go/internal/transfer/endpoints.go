package transfer

import (
	"encoding/json"
	"io"
	"net"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"time"

	"github.com/algony-tony/mymcp/go/internal/audit"
	"github.com/algony-tony/mymcp/go/internal/fsutil"
)

// Endpoints serves the ticket-only /files/raw routes. It shares the TicketStore
// with the prepare_* tools and writes transfer_upload/transfer_download audit.
type Endpoints struct {
	Tickets   *TicketStore
	Audit     *audit.Writer
	Protected []fsutil.ProtectedEntry
	Enabled   bool
	// OnAuditFail is invoked when a redemption audit record cannot be written;
	// it bumps mymcp_audit_write_failures_total (SOC: audit loss must be
	// visible). Nil disables the callback (used by tests).
	OnAuditFail func()
}

// Register wires PUT+GET /files/raw/{ticket_id} onto mux.
func (e *Endpoints) Register(mux *http.ServeMux) {
	mux.HandleFunc("PUT /files/raw/{ticket_id}", e.Upload)
	mux.HandleFunc("GET /files/raw/{ticket_id}", e.Download)
}

func writeErr(w http.ResponseWriter, status int, code, hint string) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(map[string]any{"ok": false, "error": code, "hint": hint})
}

func clientIP(r *http.Request) string {
	if host, _, err := net.SplitHostPort(r.RemoteAddr); err == nil {
		return host
	}
	return "unknown"
}

// resolveTicket runs the shared lookup/classify/method/consume gate. Returns the
// consumed ticket, or nil after having written the error response.
func (e *Endpoints) resolveTicket(w http.ResponseWriter, r *http.Request, wantOp string) *Ticket {
	if !e.Enabled {
		writeErr(w, 404, "transfer_disabled", "File transfer is disabled on this server.")
		return nil
	}
	id := r.PathValue("ticket_id")
	tk := e.Tickets.Lookup(id)
	if tk == nil {
		switch e.Tickets.Classify(id) {
		case "expired":
			writeErr(w, 410, "ticket_expired", "Mint a new ticket.")
		case "consumed":
			writeErr(w, 410, "ticket_not_found", "Ticket already used.")
		default:
			writeErr(w, 404, "ticket_not_found", "Mint a new ticket.")
		}
		return nil
	}
	if tk.Op != wantOp {
		if wantOp == "upload" {
			writeErr(w, 405, "wrong_method", "This ticket requires GET.")
		} else {
			writeErr(w, 405, "wrong_method", "This ticket requires PUT.")
		}
		return nil
	}
	if !e.Tickets.Consume(id) {
		writeErr(w, 410, "ticket_not_found", "Ticket already used.")
		return nil
	}
	return tk
}

// auditRedeem writes one transfer_upload/transfer_download record. On write
// failure it bumps mymcp_audit_write_failures_total (via OnAuditFail) and
// returns the error so a confirmable mutation is not acknowledged unaudited —
// silent audit loss is a SOC red line.
func (e *Endpoints) auditRedeem(tk *Ticket, ok bool, n int64, code, ip string) error {
	tool := "transfer_download"
	if tk.Op == "upload" {
		tool = "transfer_upload"
	}
	result := "ok"
	var errCode, errMsg string
	if !ok {
		result, errCode, errMsg = "error", code, code
	}
	err := e.Audit.Log(audit.Entry{
		TS: time.Now().UTC().Format(time.RFC3339Nano), TokenName: tk.CreatedBy,
		Role: tk.CreatedByRole, IP: ip, Tool: tool, Result: result,
		ErrorCode: errCode, ErrorMessage: errMsg,
		Params: map[string]any{
			"op": tk.Op, "path": tk.Path, "ticket": firstN(tk.TicketID, 8),
			"bytes": n, "issuer_token_name": tk.CreatedBy,
			"issuer_role": tk.CreatedByRole, "redeemer_ip": ip,
		},
	})
	if err != nil && e.OnAuditFail != nil {
		e.OnAuditFail()
	}
	return err
}

func firstN(s string, n int) string {
	if len(s) <= n {
		return s
	}
	return s[:n]
}

// Upload handles PUT /files/raw/{ticket_id}.
func (e *Endpoints) Upload(w http.ResponseWriter, r *http.Request) {
	tk := e.resolveTicket(w, r, "upload")
	if tk == nil {
		return
	}
	ip := clientIP(r)
	if msg := fsutil.CheckProtectedPath(tk.Path, fsutil.ModeWrite, e.Protected); msg != "" {
		e.auditRedeem(tk, false, 0, "path_protected", ip)
		writeErr(w, 403, "path_protected", msg)
		return
	}
	if cl := r.Header.Get("Content-Length"); cl != "" {
		if n, err := strconv.ParseInt(cl, 10, 64); err != nil {
			e.auditRedeem(tk, false, 0, "bad_content_length", ip)
			writeErr(w, 400, "bad_content_length", "Content-Length is not an integer.")
			return
		} else if n > tk.MaxBytes {
			e.auditRedeem(tk, false, n, "size_exceeded", ip)
			writeErr(w, 413, "size_exceeded", "Body exceeds max_bytes="+strconv.FormatInt(tk.MaxBytes, 10)+".")
			return
		}
	}
	parent := filepath.Dir(tk.Path)
	if parent == "" {
		parent = "/"
	}
	if err := os.MkdirAll(parent, 0o755); err != nil {
		e.auditRedeem(tk, false, 0, "mkdir_failed", ip)
		writeErr(w, 500, "mkdir_failed", err.Error())
		return
	}
	tmp, err := os.CreateTemp(parent, ".mymcp-upload-*")
	if err != nil {
		e.auditRedeem(tk, false, 0, "write_failed", ip)
		writeErr(w, 500, "write_failed", err.Error())
		return
	}
	tmpPath := tmp.Name()
	written, exceeded, copyErr := copyCapped(tmp, r.Body, tk.MaxBytes)
	tmp.Close()
	if exceeded {
		os.Remove(tmpPath)
		e.auditRedeem(tk, false, written, "size_exceeded", ip)
		writeErr(w, 413, "size_exceeded", "Body exceeds max_bytes="+strconv.FormatInt(tk.MaxBytes, 10)+".")
		return
	}
	if copyErr != nil {
		os.Remove(tmpPath)
		e.auditRedeem(tk, false, written, "write_failed", ip)
		writeErr(w, 500, "write_failed", copyErr.Error())
		return
	}
	if err := os.Rename(tmpPath, tk.Path); err != nil {
		os.Remove(tmpPath)
		e.auditRedeem(tk, false, written, "write_failed", ip)
		writeErr(w, 500, "write_failed", err.Error())
		return
	}
	if err := e.auditRedeem(tk, true, written, "", ip); err != nil {
		// The bytes are on disk, but do not confirm an unauditable mutation.
		writeErr(w, 500, "audit_failed", "upload succeeded but the audit record could not be written")
		return
	}
	w.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(w).Encode(map[string]any{"ok": true, "path": tk.Path, "bytes_written": written})
}

// copyCapped streams src→dst, stopping if more than limit bytes arrive.
func copyCapped(dst io.Writer, src io.Reader, limit int64) (written int64, exceeded bool, err error) {
	buf := make([]byte, 64*1024)
	for {
		n, rerr := src.Read(buf)
		if n > 0 {
			if written+int64(n) > limit {
				return written, true, nil
			}
			if _, werr := dst.Write(buf[:n]); werr != nil {
				return written, false, werr
			}
			written += int64(n)
		}
		if rerr == io.EOF {
			return written, false, nil
		}
		if rerr != nil {
			return written, false, rerr
		}
	}
}

// Download handles GET /files/raw/{ticket_id}.
func (e *Endpoints) Download(w http.ResponseWriter, r *http.Request) {
	tk := e.resolveTicket(w, r, "download")
	if tk == nil {
		return
	}
	ip := clientIP(r)
	if msg := fsutil.CheckProtectedPath(tk.Path, fsutil.ModeRead, e.Protected); msg != "" {
		e.auditRedeem(tk, false, 0, "path_protected", ip)
		writeErr(w, 403, "path_protected", msg)
		return
	}
	st, err := os.Stat(tk.Path)
	if err != nil || st.IsDir() {
		e.auditRedeem(tk, false, 0, "path_not_found", ip)
		writeErr(w, 404, "path_not_found", "Server file no longer exists.")
		return
	}
	f, err := os.Open(tk.Path)
	if err != nil {
		e.auditRedeem(tk, false, 0, "path_not_found", ip)
		writeErr(w, 404, "path_not_found", "Server file no longer exists.")
		return
	}
	defer f.Close()
	w.Header().Set("Content-Type", "application/octet-stream")
	w.Header().Set("Content-Length", strconv.FormatInt(st.Size(), 10))
	w.Header().Set("Content-Disposition", contentDisposition(filepath.Base(tk.Path)))
	sent, cerr := io.Copy(w, f)
	if cerr != nil {
		e.auditRedeem(tk, false, sent, "stream_aborted", ip)
		return
	}
	e.auditRedeem(tk, true, sent, "", ip)
}

// contentDisposition builds a safe attachment header (port of _content_disposition).
func contentDisposition(filename string) string {
	var b strings.Builder
	for _, c := range filename {
		if c >= 0x20 && c != 0x7f {
			b.WriteRune(c)
		}
	}
	safe := b.String()
	quoted := strings.ReplaceAll(safe, `\`, `\\`)
	quoted = strings.ReplaceAll(quoted, `"`, `\"`)
	asciiOnly := strings.Map(func(r rune) rune {
		if r > 127 {
			return -1
		}
		return r
	}, quoted)
	star := url.PathEscape(safe)
	return `attachment; filename="` + asciiOnly + `"; filename*=UTF-8''` + star
}
