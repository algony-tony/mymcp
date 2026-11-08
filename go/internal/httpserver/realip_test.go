package httpserver

import (
	"net"
	"net/http"
	"net/http/httptest"
	"testing"
)

func TestExtractClientIP(t *testing.T) {
	_, loopback4, _ := net.ParseCIDR("127.0.0.0/8")
	_, loopback6, _ := net.ParseCIDR("::1/128")
	_, priv10, _ := net.ParseCIDR("10.0.0.0/8")
	trusted := []*net.IPNet{loopback4, loopback6, priv10}

	t.Run("direct untrusted connection ignores XFF", func(t *testing.T) {
		req := httptest.NewRequest(http.MethodGet, "/", nil)
		req.RemoteAddr = "203.0.113.195:4321"
		req.Header.Set("X-Forwarded-For", "198.51.100.1")
		req.Header.Set("X-Real-IP", "198.51.100.2")

		ip := ExtractClientIP(req, trusted)
		if ip != "203.0.113.195" {
			t.Errorf("got %q, want 203.0.113.195", ip)
		}
	})

	t.Run("empty trusted nets ignores XFF", func(t *testing.T) {
		req := httptest.NewRequest(http.MethodGet, "/", nil)
		req.RemoteAddr = "127.0.0.1:4321"
		req.Header.Set("X-Forwarded-For", "198.51.100.1")

		ip := ExtractClientIP(req, nil)
		if ip != "127.0.0.1" {
			t.Errorf("got %q, want 127.0.0.1", ip)
		}
	})

	t.Run("trusted proxy extracts untrusted client from XFF", func(t *testing.T) {
		req := httptest.NewRequest(http.MethodGet, "/", nil)
		req.RemoteAddr = "127.0.0.1:4321"
		req.Header.Set("X-Forwarded-For", "203.0.113.50, 10.0.0.1")

		ip := ExtractClientIP(req, trusted)
		if ip != "203.0.113.50" {
			t.Errorf("got %q, want 203.0.113.50", ip)
		}
	})

	t.Run("trusted proxy falls back to X-Real-IP when XFF missing", func(t *testing.T) {
		req := httptest.NewRequest(http.MethodGet, "/", nil)
		req.RemoteAddr = "10.0.0.2:4321"
		req.Header.Set("X-Real-IP", "198.51.100.77")

		ip := ExtractClientIP(req, trusted)
		if ip != "198.51.100.77" {
			t.Errorf("got %q, want 198.51.100.77", ip)
		}
	})

	t.Run("trusted proxy with all trusted in XFF falls back to first", func(t *testing.T) {
		req := httptest.NewRequest(http.MethodGet, "/", nil)
		req.RemoteAddr = "127.0.0.1:4321"
		req.Header.Set("X-Forwarded-For", "10.0.0.5, 10.0.0.1")

		ip := ExtractClientIP(req, trusted)
		if ip != "10.0.0.5" {
			t.Errorf("got %q, want 10.0.0.5", ip)
		}
	})

	t.Run("trusted proxy with invalid XFF and invalid X-Real-IP falls back to direct IP", func(t *testing.T) {
		req := httptest.NewRequest(http.MethodGet, "/", nil)
		req.RemoteAddr = "127.0.0.1:4321"
		req.Header.Set("X-Forwarded-For", "invalid-ip")
		req.Header.Set("X-Real-IP", "also-invalid")

		ip := ExtractClientIP(req, trusted)
		if ip != "127.0.0.1" {
			t.Errorf("got %q, want 127.0.0.1", ip)
		}
	})

	t.Run("invalid direct RemoteAddr returns unknown", func(t *testing.T) {
		req := httptest.NewRequest(http.MethodGet, "/", nil)
		req.RemoteAddr = "not-an-ip"

		ip := ExtractClientIP(req, trusted)
		if ip != "unknown" {
			t.Errorf("got %q, want unknown", ip)
		}
	})

	t.Run("direct RemoteAddr without port parses correctly", func(t *testing.T) {
		req := httptest.NewRequest(http.MethodGet, "/", nil)
		req.RemoteAddr = "203.0.113.195"

		ip := ExtractClientIP(req, trusted)
		if ip != "203.0.113.195" {
			t.Errorf("got %q, want 203.0.113.195", ip)
		}
	})

	t.Run("multiple X-Forwarded-For header lines joined per RFC 9110", func(t *testing.T) {
		req := httptest.NewRequest(http.MethodGet, "/", nil)
		req.RemoteAddr = "10.0.0.1:4321"
		req.Header.Add("X-Forwarded-For", "203.0.113.50")
		req.Header.Add("X-Forwarded-For", "198.51.100.25, 10.0.0.2")

		// 10.0.0.2 is trusted. First untrusted right-to-left is 198.51.100.25
		ip := ExtractClientIP(req, trusted)
		if ip != "198.51.100.25" {
			t.Errorf("got %q, want 198.51.100.25", ip)
		}
	})

	t.Run("X-Forwarded-For and X-Real-IP with client port numbers", func(t *testing.T) {
		req := httptest.NewRequest(http.MethodGet, "/", nil)
		req.RemoteAddr = "10.0.0.1:4321"
		req.Header.Set("X-Forwarded-For", "203.0.113.50:54321, 10.0.0.2:8080")

		ip := ExtractClientIP(req, trusted)
		if ip != "203.0.113.50" {
			t.Errorf("got %q, want 203.0.113.50 (stripped port)", ip)
		}

		// IPv6 with port in brackets
		reqIPv6 := httptest.NewRequest(http.MethodGet, "/", nil)
		reqIPv6.RemoteAddr = "10.0.0.1:4321"
		reqIPv6.Header.Set("X-Forwarded-For", "[2001:db8::1]:1234, 10.0.0.2")

		ip = ExtractClientIP(reqIPv6, trusted)
		if ip != "2001:db8::1" {
			t.Errorf("got %q, want 2001:db8::1", ip)
		}

		// IPv6 in brackets without port in X-Forwarded-For
		reqIPv6NoPort := httptest.NewRequest(http.MethodGet, "/", nil)
		reqIPv6NoPort.RemoteAddr = "10.0.0.1:4321"
		reqIPv6NoPort.Header.Set("X-Forwarded-For", "[2001:db8::1], 10.0.0.2")

		ip = ExtractClientIP(reqIPv6NoPort, trusted)
		if ip != "2001:db8::1" {
			t.Errorf("got %q, want 2001:db8::1", ip)
		}

		// X-Real-IP with port
		reqRealIP := httptest.NewRequest(http.MethodGet, "/", nil)
		reqRealIP.RemoteAddr = "10.0.0.1:4321"
		reqRealIP.Header.Set("X-Real-IP", "198.51.100.77:9999")

		ip = ExtractClientIP(reqRealIP, trusted)
		if ip != "198.51.100.77" {
			t.Errorf("got %q, want 198.51.100.77", ip)
		}

		// X-Real-IP with bracketed IPv6 without port
		reqRealIPv6 := httptest.NewRequest(http.MethodGet, "/", nil)
		reqRealIPv6.RemoteAddr = "10.0.0.1:4321"
		reqRealIPv6.Header.Set("X-Real-IP", "[2001:db8::2]")

		ip = ExtractClientIP(reqRealIPv6, trusted)
		if ip != "2001:db8::2" {
			t.Errorf("got %q, want 2001:db8::2", ip)
		}

		// RemoteAddr with bracketed IPv6
		reqRemoteIPv6 := httptest.NewRequest(http.MethodGet, "/", nil)
		reqRemoteIPv6.RemoteAddr = "[2001:db8::3]"

		ip = ExtractClientIP(reqRemoteIPv6, trusted)
		if ip != "2001:db8::3" {
			t.Errorf("got %q, want 2001:db8::3", ip)
		}
	})
}
