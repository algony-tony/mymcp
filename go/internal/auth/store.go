// Package auth implements the tokens.json-backed token store, format-compatible
// with the Python core (src/mymcp/auth.py): admin_token always comes from
// config, missing roles default to rw, last_used updates in memory and is
// persisted only by Flush (called at shutdown).
package auth

import (
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"sync"
	"time"
)

type TokenInfo struct {
	Name      string  `json:"name"`
	CreatedAt string  `json:"created_at"`
	LastUsed  *string `json:"last_used"`
	Enabled   bool    `json:"enabled"`
	Role      string  `json:"role"`
}

type storeData struct {
	Tokens     map[string]*TokenInfo `json:"tokens"`
	AdminToken string                `json:"admin_token"`
}

type TokenStore struct {
	path string
	mu   sync.Mutex
	data storeData
}

func NewTokenStore(path, adminToken string) (*TokenStore, error) {
	st := &TokenStore{path: path}
	st.data = storeData{Tokens: map[string]*TokenInfo{}, AdminToken: adminToken}
	raw, err := os.ReadFile(path)
	switch {
	case err == nil:
		if err := json.Unmarshal(raw, &st.data); err != nil {
			return nil, fmt.Errorf("parse %s: %w", path, err)
		}
		st.data.AdminToken = adminToken // config wins over file
		if st.data.Tokens == nil {
			st.data.Tokens = map[string]*TokenInfo{}
		}
		for _, info := range st.data.Tokens {
			if info.Role == "" {
				info.Role = "rw" // backward compat, same as Python _load
			}
		}
	case os.IsNotExist(err):
		if err := st.saveLocked(); err != nil {
			return nil, err
		}
	default:
		return nil, err
	}
	return st, nil
}

func (s *TokenStore) AdminToken() string { return s.data.AdminToken }

// Validate returns a copy of the token info if the token exists and is
// enabled, else nil. last_used is bumped in memory only.
func (s *TokenStore) Validate(token string) *TokenInfo {
	s.mu.Lock()
	defer s.mu.Unlock()
	info, ok := s.data.Tokens[token]
	if !ok || !info.Enabled {
		return nil
	}
	now := time.Now().UTC().Format("2006-01-02T15:04:05.000000-07:00")
	info.LastUsed = &now
	cp := *info
	return &cp
}

// AddEphemeral registers an in-memory token (the temp-rw token printed by
// `serve` when nothing is configured). created_at is the literal "ephemeral",
// matching the Python CLI.
func (s *TokenStore) AddEphemeral(token, name, role string) {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.data.Tokens[token] = &TokenInfo{
		Name: name, CreatedAt: "ephemeral", LastUsed: nil, Enabled: true, Role: role,
	}
}

// Flush persists in-memory state atomically: tmp file + chmod 0600 + rename.
func (s *TokenStore) Flush() error {
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.saveLocked()
}

func (s *TokenStore) saveLocked() error {
	if err := os.MkdirAll(filepath.Dir(s.path), 0o755); err != nil {
		return err
	}
	raw, err := json.MarshalIndent(s.data, "", "  ")
	if err != nil {
		return err
	}
	tmp := s.path + ".tmp"
	if err := os.WriteFile(tmp, raw, 0o600); err != nil {
		return err
	}
	_ = os.Chmod(tmp, 0o600) // best-effort, as in Python
	if err := os.Rename(tmp, s.path); err != nil {
		os.Remove(tmp)
		return err
	}
	return nil
}

// GenerateToken returns "tok_" + 32 hex chars (16 random bytes), the same
// shape the Python core mints.
func GenerateToken() (string, error) {
	b := make([]byte, 16)
	if _, err := rand.Read(b); err != nil {
		return "", err
	}
	return "tok_" + hex.EncodeToString(b), nil
}
