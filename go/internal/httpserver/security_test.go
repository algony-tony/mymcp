package httpserver

import (
	"errors"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/algony-tony/mymcp/go/internal/config"
	"github.com/algony-tony/mymcp/go/internal/metrics"
	"github.com/algony-tony/mymcp/go/internal/tools"
)

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

func TestSecurityHeaders(t *testing.T) {
	inner := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(200)
	})

	// Plain HTTP
	hHTTP := securityHeaders(false)(inner)
	rec := httptest.NewRecorder()
	hHTTP.ServeHTTP(rec, httptest.NewRequest("GET", "/health", nil))
	if rec.Header().Get("X-Content-Type-Options") != "nosniff" {
		t.Errorf("missing nosniff")
	}
	if rec.Header().Get("X-Frame-Options") != "DENY" {
		t.Errorf("missing DENY")
	}
	if rec.Header().Get("Content-Security-Policy") != "default-src 'none'" {
		t.Errorf("missing Content-Security-Policy")
	}
	if rec.Header().Get("Strict-Transport-Security") != "" {
		t.Errorf("HSTS should not be sent on plain HTTP")
	}

	// HTTPS
	hTLS := securityHeaders(true)(inner)
	recTLS := httptest.NewRecorder()
	hTLS.ServeHTTP(recTLS, httptest.NewRequest("GET", "/health", nil))
	if recTLS.Header().Get("Strict-Transport-Security") != "max-age=31536000; includeSubDomains" {
		t.Errorf("missing HSTS on TLS")
	}
}

func TestMaxBodySize(t *testing.T) {
	handler := maxBodySize(10)(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		_, err := io.ReadAll(r.Body)
		if err != nil {
			var maxErr *http.MaxBytesError
			if errors.As(err, &maxErr) {
				writeJSON(w, http.StatusRequestEntityTooLarge, map[string]string{"detail": "Request body too large"})
				return
			}
			http.Error(w, err.Error(), 500)
			return
		}
		writeJSON(w, 200, map[string]string{"status": "ok"})
	}))

	rec := httptest.NewRecorder()
	req := httptest.NewRequest("POST", "/", strings.NewReader("123456789012345")) // 15 bytes > 10 bytes limit
	handler.ServeHTTP(rec, req)
	if rec.Code != http.StatusRequestEntityTooLarge {
		t.Fatalf("expected 413, got %d", rec.Code)
	}
}

func TestBuildMuxSecurityHeaders(t *testing.T) {
	mux := testMux(t, "")
	rec := httptest.NewRecorder()
	mux.ServeHTTP(rec, httptest.NewRequest("GET", "/health", nil))
	if rec.Header().Get("X-Content-Type-Options") != "nosniff" {
		t.Errorf("missing nosniff header on BuildMux")
	}
	if rec.Header().Get("X-Frame-Options") != "DENY" {
		t.Errorf("missing DENY header on BuildMux")
	}
	if rec.Header().Get("Content-Security-Policy") != "default-src 'none'" {
		t.Errorf("missing Content-Security-Policy on BuildMux")
	}
	if rec.Header().Get("Strict-Transport-Security") != "" {
		t.Errorf("unexpected HSTS on plain HTTP BuildMux")
	}
}

func TestBuildMuxSecurityHeadersTLS(t *testing.T) {
	cfg := &config.Config{TLSCert: "/fake/cert.pem", TLSKey: "/fake/key.pem"}
	m := metrics.New(func() float64 { return 0 })
	mux := BuildMux(tools.Deps{Cfg: cfg}, nil, nil, m, "", "test")
	rec := httptest.NewRecorder()
	mux.ServeHTTP(rec, httptest.NewRequest("GET", "/health", nil))
	if rec.Header().Get("Strict-Transport-Security") != "max-age=31536000; includeSubDomains" {
		t.Errorf("missing Strict-Transport-Security on TLS BuildMux")
	}
}
