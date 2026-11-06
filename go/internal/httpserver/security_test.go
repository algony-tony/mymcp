package httpserver

import "testing"

func TestConstantTimeEquals(t *testing.T) {
	if !constantTimeEquals("secret-token-123", "secret-token-123") {
		t.Errorf("expected matching strings to return true")
	}
	if constantTimeEquals("secret-token-123", "secret-token-456") {
		t.Errorf("expected mismatching strings to return false")
	}
	if constantTimeEquals("secret", "secret-longer") {
		t.Errorf("expected different length strings to return false")
	}
}
