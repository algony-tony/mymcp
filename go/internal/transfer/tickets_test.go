package transfer

import (
	"testing"
	"time"
)

func TestMintLookupConsume(t *testing.T) {
	s := NewTicketStore()
	tk := s.Mint("upload", "/tmp/x", 100, 300, "n", "rw")
	if tk.TicketID == "" || tk.Op != "upload" {
		t.Fatalf("bad ticket: %+v", tk)
	}
	if got := s.Lookup(tk.TicketID); got == nil || got.Path != "/tmp/x" {
		t.Fatalf("lookup failed: %+v", got)
	}
	if !s.Consume(tk.TicketID) {
		t.Fatal("first consume must succeed")
	}
	if s.Consume(tk.TicketID) {
		t.Fatal("second consume must fail")
	}
	if s.Lookup(tk.TicketID) != nil {
		t.Fatal("consumed ticket must not look up")
	}
}

func TestClassify(t *testing.T) {
	s := NewTicketStore()
	if s.Classify("nope") != "missing" {
		t.Fatal("missing")
	}
	tk := s.Mint("download", "/f", 1, 300, "n", "ro")
	if s.Classify(tk.TicketID) != "valid" {
		t.Fatal("valid")
	}
	s.Consume(tk.TicketID)
	if s.Classify(tk.TicketID) != "consumed" {
		t.Fatal("consumed")
	}
	exp := s.Mint("download", "/f", 1, 300, "n", "ro")
	exp.ExpiresAt = time.Now().Add(-time.Second).Unix() // reach in; test-only
	if s.Classify(exp.TicketID) != "expired" {
		t.Fatal("expired")
	}
}

func TestExpiryHidesTicket(t *testing.T) {
	s := NewTicketStore()
	tk := s.Mint("upload", "/f", 1, 0, "n", "rw") // ttl 0 → already expired
	if s.Lookup(tk.TicketID) != nil {
		t.Fatal("ttl<=0 must not be lookable")
	}
}
