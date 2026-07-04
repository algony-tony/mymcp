package fsutil

import (
	"os"
	"path/filepath"
	"testing"
)

func TestCheckProtectedPathBlocksInsideDir(t *testing.T) {
	dir := t.TempDir()
	pp := []ProtectedEntry{{Pattern: dir, Modes: ModeRead | ModeWrite}}
	msg := CheckProtectedPath(filepath.Join(dir, "audit.log"), ModeRead, pp)
	want := "Access denied: path is within protected directory " + dir
	if msg != want {
		t.Fatalf("got %q, want %q", msg, want)
	}
	if CheckProtectedPath(dir, ModeWrite, pp) == "" {
		t.Fatal("the protected dir itself must be blocked")
	}
}

func TestCheckProtectedPathAllowsOutsideAndOtherMode(t *testing.T) {
	dir := t.TempDir()
	other := t.TempDir()
	pp := []ProtectedEntry{{Pattern: dir, Modes: ModeWrite}} // write-only protection
	if msg := CheckProtectedPath(filepath.Join(other, "f"), ModeWrite, pp); msg != "" {
		t.Fatalf("outside path blocked: %q", msg)
	}
	if msg := CheckProtectedPath(filepath.Join(dir, "f"), ModeRead, pp); msg != "" {
		t.Fatalf("read must be allowed on write-only protection: %q", msg)
	}
	if CheckProtectedPath(filepath.Join(dir, "f"), ModeWrite, pp) == "" {
		t.Fatal("write must be blocked")
	}
}

func TestCheckProtectedPathResolvesSymlinks(t *testing.T) {
	real := t.TempDir()
	linkParent := t.TempDir()
	link := filepath.Join(linkParent, "link")
	if err := os.Symlink(real, link); err != nil {
		t.Skip("symlinks unavailable")
	}
	pp := []ProtectedEntry{{Pattern: real, Modes: ModeRead | ModeWrite}}
	if CheckProtectedPath(filepath.Join(link, "esc.txt"), ModeRead, pp) == "" {
		t.Fatal("symlinked path into protected dir must be blocked")
	}
}

func TestCheckProtectedPathNonexistentCandidate(t *testing.T) {
	dir := t.TempDir()
	pp := []ProtectedEntry{{Pattern: dir, Modes: ModeRead | ModeWrite}}
	// File doesn't exist yet — must still be recognized as inside.
	if CheckProtectedPath(filepath.Join(dir, "not-yet", "deep.log"), ModeWrite, pp) == "" {
		t.Fatal("nonexistent path under protected dir must be blocked")
	}
}

func TestDecodeReplacePerByte(t *testing.T) {
	// Two invalid bytes → two replacement chars (Python errors="replace").
	in := []byte{'a', 0xff, 0xfe, 'b'}
	got := DecodeReplace(in)
	want := "a��b"
	if got != want {
		t.Fatalf("got %q, want %q", got, want)
	}
	if DecodeReplace([]byte("héllo")) != "héllo" {
		t.Fatal("valid UTF-8 must pass through")
	}
}
