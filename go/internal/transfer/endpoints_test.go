package transfer

import (
	"bytes"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"testing"

	"github.com/algony-tony/mymcp/go/internal/audit"
	"github.com/algony-tony/mymcp/go/internal/fsutil"
)

func testEndpoints(t *testing.T) (*Endpoints, *TicketStore, string) {
	t.Helper()
	dir := t.TempDir()
	a, _ := audit.New(false, t.TempDir(), 1<<20, 5)
	store := NewTicketStore()
	e := &Endpoints{Tickets: store, Audit: a, Protected: nil, Enabled: true}
	return e, store, dir
}

func TestUploadRoundTrip(t *testing.T) {
	e, store, dir := testEndpoints(t)
	dst := filepath.Join(dir, "out.bin")
	tk := store.Mint("upload", dst, 1024, 300, "n", "rw")

	req := httptest.NewRequest("PUT", "/files/raw/"+tk.TicketID, bytes.NewReader([]byte("hello")))
	req.SetPathValue("ticket_id", tk.TicketID)
	rec := httptest.NewRecorder()
	e.Upload(rec, req)
	if rec.Code != 200 {
		t.Fatalf("code=%d body=%s", rec.Code, rec.Body)
	}
	got, _ := os.ReadFile(dst)
	if string(got) != "hello" {
		t.Fatalf("file=%q", got)
	}
	req2 := httptest.NewRequest("PUT", "/files/raw/"+tk.TicketID, bytes.NewReader([]byte("x")))
	req2.SetPathValue("ticket_id", tk.TicketID)
	rec2 := httptest.NewRecorder()
	e.Upload(rec2, req2)
	if rec2.Code != 410 && rec2.Code != 404 {
		t.Fatalf("reuse code=%d", rec2.Code)
	}
}

func TestUploadSizeExceeded(t *testing.T) {
	e, store, dir := testEndpoints(t)
	dst := filepath.Join(dir, "big.bin")
	tk := store.Mint("upload", dst, 3, 300, "n", "rw")
	req := httptest.NewRequest("PUT", "/files/raw/"+tk.TicketID, bytes.NewReader([]byte("toolong")))
	req.SetPathValue("ticket_id", tk.TicketID)
	rec := httptest.NewRecorder()
	e.Upload(rec, req)
	if rec.Code != 413 {
		t.Fatalf("code=%d", rec.Code)
	}
	if _, err := os.Stat(dst); !os.IsNotExist(err) {
		t.Fatal("no partial file must remain")
	}
}

func TestUploadWrongMethodTicket(t *testing.T) {
	e, store, dir := testEndpoints(t)
	tk := store.Mint("download", filepath.Join(dir, "f"), 10, 300, "n", "ro")
	req := httptest.NewRequest("PUT", "/files/raw/"+tk.TicketID, http.NoBody)
	req.SetPathValue("ticket_id", tk.TicketID)
	rec := httptest.NewRecorder()
	e.Upload(rec, req)
	if rec.Code != 405 {
		t.Fatalf("code=%d", rec.Code)
	}
}

func TestDownloadRoundTrip(t *testing.T) {
	e, store, dir := testEndpoints(t)
	src := filepath.Join(dir, "src.bin")
	os.WriteFile(src, []byte("payload"), 0o644)
	tk := store.Mint("download", src, 7, 300, "n", "ro")
	req := httptest.NewRequest("GET", "/files/raw/"+tk.TicketID, http.NoBody)
	req.SetPathValue("ticket_id", tk.TicketID)
	rec := httptest.NewRecorder()
	e.Download(rec, req)
	if rec.Code != 200 {
		t.Fatalf("code=%d", rec.Code)
	}
	body, _ := io.ReadAll(rec.Result().Body)
	if string(body) != "payload" {
		t.Fatalf("body=%q", body)
	}
	if cd := rec.Result().Header.Get("Content-Disposition"); cd == "" {
		t.Fatal("missing content-disposition")
	}
}

func TestDownloadMissingFile(t *testing.T) {
	e, store, dir := testEndpoints(t)
	tk := store.Mint("download", filepath.Join(dir, "gone"), 1, 300, "n", "ro")
	req := httptest.NewRequest("GET", "/files/raw/"+tk.TicketID, http.NoBody)
	req.SetPathValue("ticket_id", tk.TicketID)
	rec := httptest.NewRecorder()
	e.Download(rec, req)
	if rec.Code != 404 {
		t.Fatalf("code=%d", rec.Code)
	}
}

func TestDisabledReturns404(t *testing.T) {
	e, store, dir := testEndpoints(t)
	e.Enabled = false
	tk := store.Mint("upload", filepath.Join(dir, "f"), 10, 300, "n", "rw")
	req := httptest.NewRequest("PUT", "/files/raw/"+tk.TicketID, http.NoBody)
	req.SetPathValue("ticket_id", tk.TicketID)
	rec := httptest.NewRecorder()
	e.Upload(rec, req)
	if rec.Code != 404 {
		t.Fatalf("code=%d", rec.Code)
	}
}

func TestUploadAuditFailureCountsAnd500(t *testing.T) {
	if os.Geteuid() == 0 {
		t.Skip("root bypasses file permissions; cannot force an audit write failure")
	}
	dir := t.TempDir()
	auditDir := t.TempDir()
	// maxBytes=1 forces a rotate on every Log; a read-only log file makes the
	// rotate's reopen fail, so Audit.Log returns an error.
	a, err := audit.New(true, auditDir, 1, 0)
	if err != nil {
		t.Fatal(err)
	}
	if err := os.Chmod(filepath.Join(auditDir, "audit.log"), 0o444); err != nil {
		t.Fatal(err)
	}
	failed := 0
	store := NewTicketStore()
	e := &Endpoints{Tickets: store, Audit: a, Enabled: true, OnAuditFail: func() { failed++ }}
	dst := filepath.Join(dir, "out.bin")
	tk := store.Mint("upload", dst, 1024, 300, "n", "rw")
	req := httptest.NewRequest("PUT", "/files/raw/"+tk.TicketID, bytes.NewReader([]byte("hi")))
	req.SetPathValue("ticket_id", tk.TicketID)
	rec := httptest.NewRecorder()
	e.Upload(rec, req)
	if rec.Code != 500 {
		t.Fatalf("audit failure must not confirm the upload: code=%d", rec.Code)
	}
	if failed == 0 {
		t.Fatal("OnAuditFail must be invoked (SOC: audit loss must be visible)")
	}
}

var _ = fsutil.ModeWrite
