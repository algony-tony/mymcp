package tools

import (
	"path/filepath"
	"strings"
	"testing"

	"github.com/algony-tony/mymcp/go/internal/transfer"
)

func transferDeps(t *testing.T) Deps {
	d := testDeps(t)
	d.Tickets = transfer.NewTicketStore()
	return d
}

func TestPrepareUploadMintsTicketAndURL(t *testing.T) {
	d := transferDeps(t)
	p := filepath.Join(t.TempDir(), "up.bin")
	res := PrepareUpload(d, p, nil, nil, true, "n", "rw")
	if res["success"] != true || res["method"] != "PUT" {
		t.Fatalf("res = %v", res)
	}
	if !strings.HasPrefix(res["url"].(string), "/files/raw/") {
		t.Fatalf("relative url expected: %v", res["url"])
	}
	if res["dest_path"] != p {
		t.Fatalf("dest_path = %v", res["dest_path"])
	}
	tk := d.Tickets.Lookup(res["ticket"].(string))
	if tk == nil || tk.CreatedBy != "n" || tk.CreatedByRole != "rw" {
		t.Fatalf("ticket issuer not stamped: %+v", tk)
	}
}

func TestPrepareUploadRelativePathRejected(t *testing.T) {
	d := transferDeps(t)
	res := PrepareUpload(d, "rel/path", nil, nil, true, "n", "rw")
	if res["success"] != false || res["error"] != "InvalidPath" {
		t.Fatalf("res = %v", res)
	}
}

func TestPrepareUploadPublicBaseURL(t *testing.T) {
	d := transferDeps(t)
	d.Cfg.PublicBaseURL = "https://host.example/"
	res := PrepareUpload(d, filepath.Join(t.TempDir(), "x"), nil, nil, true, "n", "rw")
	if !strings.HasPrefix(res["url"].(string), "https://host.example/files/raw/") {
		t.Fatalf("absolute url expected: %v", res["url"])
	}
}

func TestPrepareUploadOverwriteFalse(t *testing.T) {
	d := transferDeps(t)
	p := writeTemp(t, "exists")
	res := PrepareUpload(d, p, nil, nil, false, "n", "rw")
	if res["success"] != false || res["error"] != "FileExists" {
		t.Fatalf("res = %v", res)
	}
}

func TestPrepareDownloadMintsTicket(t *testing.T) {
	d := transferDeps(t)
	p := writeTemp(t, "payload")
	res := PrepareDownload(d, p, nil, "n", "ro")
	if res["success"] != true || res["method"] != "GET" || res["size"] != int64(7) {
		t.Fatalf("res = %v", res)
	}
}

func TestPrepareDownloadMissing(t *testing.T) {
	d := transferDeps(t)
	res := PrepareDownload(d, filepath.Join(t.TempDir(), "nope"), nil, "n", "ro")
	if res["success"] != false || res["error"] != "FileNotFound" {
		t.Fatalf("res = %v", res)
	}
}

func TestPrepareUploadTTLClamped(t *testing.T) {
	d := transferDeps(t)
	d.Cfg.TransferMaxTTLSec = 60
	nine := 9999
	res := PrepareUpload(d, filepath.Join(t.TempDir(), "x"), nil, &nine, true, "n", "rw")
	if res["expires_in"] != 60 {
		t.Fatalf("ttl not clamped: %v", res["expires_in"])
	}
}
