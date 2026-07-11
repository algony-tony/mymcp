// Package transfer implements one-shot, TTL-bounded file-transfer tickets and
// the ticket-only /files/raw streaming endpoints (port of src/mymcp/transfer).
package transfer

import (
	"crypto/rand"
	"encoding/base64"
	"sync"
	"time"
)

// Ticket grants single-use access to PUT or GET one server path.
type Ticket struct {
	TicketID      string
	Op            string // "upload" | "download"
	Path          string
	MaxBytes      int64
	ExpiresAt     int64 // unix seconds
	CreatedBy     string
	CreatedByRole string
	Consumed      bool
}

// TicketStore is a thread-safe in-memory ticket table.
type TicketStore struct {
	mu      sync.Mutex
	tickets map[string]*Ticket
}

func NewTicketStore() *TicketStore {
	return &TicketStore{tickets: map[string]*Ticket{}}
}

func newTicketID() string {
	b := make([]byte, 18)
	_, _ = rand.Read(b)
	return base64.RawURLEncoding.EncodeToString(b)
}

// Mint sweeps expired entries then inserts a fresh ticket. ttlSec<=0 yields an
// already-expired ticket (Lookup returns nil), matching Python's time math.
func (s *TicketStore) Mint(op, path string, maxBytes int64, ttlSec int, createdBy, createdByRole string) *Ticket {
	s.SweepExpired()
	tk := &Ticket{
		TicketID: newTicketID(), Op: op, Path: path, MaxBytes: maxBytes,
		ExpiresAt: time.Now().Unix() + int64(ttlSec), CreatedBy: createdBy, CreatedByRole: createdByRole,
	}
	s.mu.Lock()
	s.tickets[tk.TicketID] = tk
	s.mu.Unlock()
	return tk
}

// Lookup returns the live ticket or nil (missing, consumed, or expired).
func (s *TicketStore) Lookup(id string) *Ticket {
	s.mu.Lock()
	defer s.mu.Unlock()
	t := s.tickets[id]
	if t == nil || t.Consumed || t.ExpiresAt <= time.Now().Unix() {
		return nil
	}
	return t
}

// Classify explains why Lookup returned nil, atomically.
func (s *TicketStore) Classify(id string) string {
	s.mu.Lock()
	defer s.mu.Unlock()
	t := s.tickets[id]
	switch {
	case t == nil:
		return "missing"
	case t.Consumed:
		return "consumed"
	case t.ExpiresAt <= time.Now().Unix():
		return "expired"
	default:
		return "valid"
	}
}

// Consume marks a ticket used; false if missing/already-consumed.
func (s *TicketStore) Consume(id string) bool {
	s.mu.Lock()
	defer s.mu.Unlock()
	t := s.tickets[id]
	if t == nil || t.Consumed {
		return false
	}
	t.Consumed = true
	return true
}

// SweepExpired drops consumed/expired entries; returns the count removed.
func (s *TicketStore) SweepExpired() int {
	now := time.Now().Unix()
	s.mu.Lock()
	defer s.mu.Unlock()
	n := 0
	for id, t := range s.tickets {
		if t.Consumed || t.ExpiresAt <= now {
			delete(s.tickets, id)
			n++
		}
	}
	return n
}
