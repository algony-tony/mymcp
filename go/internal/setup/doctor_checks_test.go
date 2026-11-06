package setup

import (
	"crypto/rand"
	"crypto/rsa"
	"crypto/x509"
	"crypto/x509/pkix"
	"encoding/pem"
	"math/big"
	"net"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

func TestTruncate(t *testing.T) {
	if got := truncate("short", 10); got != "short" {
		t.Errorf("truncate(short) = %q, want unchanged", got)
	}
	if got := truncate("abcdefghij", 3); got != "abc…" {
		t.Errorf("truncate(long) = %q, want %q", got, "abc…")
	}
}

func TestTokenStoreChecksMissingFile(t *testing.T) {
	path := filepath.Join(t.TempDir(), "tokens.json")
	checks := tokenStoreChecks(path)
	c := findCheck(checks, "token store")
	if c == nil || c.Severity != SevFail {
		t.Fatalf("checks = %+v, want a SevFail 'token store' check", checks)
	}
	if c.Remedy == "" {
		t.Error("a missing token store must carry a remedy")
	}
}

func TestTokenStoreChecksWrongMode(t *testing.T) {
	path, _ := newTestTokenStore(t, "rw")
	if err := os.Chmod(path, 0o644); err != nil {
		t.Fatal(err)
	}
	c := findCheck(tokenStoreChecks(path), "token store permissions")
	if c == nil || c.Severity != SevFail {
		t.Fatalf("checks = %+v, want SevFail for a 0644 token store", c)
	}
}

func TestTokenStoreChecksOnlyDisabledTokensFails(t *testing.T) {
	path := filepath.Join(t.TempDir(), "tokens.json")
	raw := `{"tokens":{"tok_old":{"name":"old","created_at":"2020-01-01T00:00:00Z","last_used":null,"enabled":false,"role":"rw"}},"admin_token":"unused"}`
	if err := os.WriteFile(path, []byte(raw), 0o600); err != nil {
		t.Fatal(err)
	}
	c := findCheck(tokenStoreChecks(path), "client tokens")
	if c == nil || c.Severity != SevFail {
		t.Fatalf("checks = %+v, want SevFail when every token is disabled", c)
	}
	if !strings.Contains(c.Detail, "no enabled tokens") {
		t.Errorf("detail = %q, want it to say no enabled tokens", c.Detail)
	}
}

func TestTokenStoreChecksHealthyStoreIsOK(t *testing.T) {
	path, _ := newTestTokenStore(t, "rw")
	checks := tokenStoreChecks(path)
	perm := findCheck(checks, "token store permissions")
	tokens := findCheck(checks, "client tokens")
	if perm == nil || perm.Severity != SevOK {
		t.Fatalf("permissions check = %+v, want SevOK", perm)
	}
	if tokens == nil || tokens.Severity != SevOK {
		t.Fatalf("client tokens check = %+v, want SevOK", tokens)
	}
	if !strings.Contains(tokens.Detail, "1 enabled") {
		t.Errorf("detail = %q, want it to count the enabled token", tokens.Detail)
	}
}

func writeUnit(t *testing.T, content string) string {
	t.Helper()
	path := filepath.Join(t.TempDir(), "mymcp.service")
	if err := os.WriteFile(path, []byte(content), 0o644); err != nil {
		t.Fatal(err)
	}
	return path
}

func TestExecStartCheckMissingUnitFileWarns(t *testing.T) {
	sys := newFakeSystem()
	c := execStartCheck(sys, filepath.Join(t.TempDir(), "does-not-exist.service"))
	if c.Severity != SevWarn {
		t.Fatalf("severity = %v, want SevWarn for a missing unit file", c.Severity)
	}
	if c.Remedy == "" {
		t.Error("a missing unit file must carry a remedy")
	}
}

func TestExecStartCheckNoExecStartLineWarns(t *testing.T) {
	sys := newFakeSystem()
	unit := writeUnit(t, "[Service]\nUser=mymcp\n")
	c := execStartCheck(sys, unit)
	if c.Severity != SevWarn {
		t.Fatalf("severity = %v, want SevWarn when the unit has no ExecStart=", c.Severity)
	}
}

func TestExecStartCheckMismatchFails(t *testing.T) {
	sys := newFakeSystem()
	sys.Paths["mymcp"] = "/usr/local/bin/mymcp"
	unit := writeUnit(t, "[Service]\nExecStart=/opt/old/mymcp serve\n")
	c := execStartCheck(sys, unit)
	if c.Severity != SevFail {
		t.Fatalf("severity = %v, want SevFail on a stale ExecStart", c.Severity)
	}
	if !strings.Contains(c.Detail, "/opt/old/mymcp") || !strings.Contains(c.Detail, "/usr/local/bin/mymcp") {
		t.Errorf("detail = %q, want it to name both binaries", c.Detail)
	}
}

func TestExecStartCheckMatchIsOK(t *testing.T) {
	sys := newFakeSystem()
	sys.Paths["mymcp"] = "/usr/local/bin/mymcp"
	unit := writeUnit(t, "[Service]\nExecStart=/usr/local/bin/mymcp serve\n")
	c := execStartCheck(sys, unit)
	if c.Severity != SevOK {
		t.Fatalf("severity = %v, want SevOK when the unit runs the resolved binary", c.Severity)
	}
}

func generateTestCertificate(t *testing.T) (certFile, keyFile string) {
	t.Helper()
	priv, err := rsa.GenerateKey(rand.Reader, 2048)
	if err != nil {
		t.Fatal(err)
	}
	template := x509.Certificate{
		SerialNumber: big.NewInt(1),
		Subject:      pkix.Name{Organization: []string{"MyMCP Test"}},
		NotBefore:    time.Now().Add(-1 * time.Hour),
		NotAfter:     time.Now().Add(24 * time.Hour),
		IPAddresses:  []net.IP{net.ParseIP("127.0.0.1")},
		KeyUsage:     x509.KeyUsageKeyEncipherment | x509.KeyUsageDigitalSignature,
		ExtKeyUsage:  []x509.ExtKeyUsage{x509.ExtKeyUsageServerAuth},
	}
	der, err := x509.CreateCertificate(rand.Reader, &template, &template, &priv.PublicKey, priv)
	if err != nil {
		t.Fatal(err)
	}
	certPath := filepath.Join(t.TempDir(), "test.crt")
	keyPath := filepath.Join(t.TempDir(), "test.key")
	certOut, err := os.Create(certPath)
	if err != nil {
		t.Fatal(err)
	}
	pem.Encode(certOut, &pem.Block{Type: "CERTIFICATE", Bytes: der})
	certOut.Close()

	keyOut, err := os.OpenFile(keyPath, os.O_CREATE|os.O_WRONLY, 0600)
	if err != nil {
		t.Fatal(err)
	}
	pem.Encode(keyOut, &pem.Block{Type: "RSA PRIVATE KEY", Bytes: x509.MarshalPKCS1PrivateKey(priv)})
	keyOut.Close()
	return certPath, keyPath
}

func TestDoctorTLSChecks(t *testing.T) {
	// 1. Valid TLS configuration
	certFile, keyFile := generateTestCertificate(t)
	checks := tlsChecks(certFile, keyFile)
	for _, c := range checks {
		if c.Severity != SevOK {
			t.Errorf("check %s failed: %s", c.Name, c.Detail)
		}
	}

	// 2. Missing cert file
	checks = tlsChecks("/nonexistent.crt", keyFile)
	if len(checks) == 0 || checks[0].Severity != SevFail {
		t.Errorf("expected SevFail on missing cert")
	}

	// 3. Missing key file
	checks = tlsChecks(certFile, "/nonexistent.key")
	if len(checks) < 2 || checks[1].Severity != SevFail {
		t.Errorf("expected SevFail on missing key")
	}

	// 4. Bad key permissions (e.g. 0644)
	badPermKey := filepath.Join(t.TempDir(), "badperm.key")
	rawKey, _ := os.ReadFile(keyFile)
	_ = os.WriteFile(badPermKey, rawKey, 0644)
	checks = tlsChecks(certFile, badPermKey)
	var foundPermWarn bool
	for _, c := range checks {
		if c.Name == "TLS key permissions" && c.Severity == SevWarn {
			foundPermWarn = true
		}
	}
	if !foundPermWarn {
		t.Errorf("expected SevWarn for 0644 key permissions")
	}

	// 4b. Read-only key permissions (0400) are accepted
	roPermKey := filepath.Join(t.TempDir(), "roperm.key")
	_ = os.WriteFile(roPermKey, rawKey, 0400)
	checks = tlsChecks(certFile, roPermKey)
	var foundPermOK bool
	for _, c := range checks {
		if c.Name == "TLS key permissions" && c.Severity == SevOK {
			foundPermOK = true
		}
	}
	if !foundPermOK {
		t.Errorf("expected SevOK for 0400 key permissions")
	}

	// 5. Plain HTTP on 0.0.0.0 without trusted proxies -> SevWarn
	check := bindSecurityCheck("0.0.0.0", false, "")
	if check.Severity != SevWarn {
		t.Errorf("expected SevWarn on public plain HTTP, got %v", check.Severity)
	}

	// 5b. Empty host normalizes to 0.0.0.0
	emptyCheck := bindSecurityCheck("", false, "")
	if emptyCheck.Severity != SevWarn {
		t.Errorf("expected SevWarn on empty host, got %v", emptyCheck.Severity)
	}
	if !strings.Contains(emptyCheck.Detail, "0.0.0.0") {
		t.Errorf("expected detail to mention 0.0.0.0, got %q", emptyCheck.Detail)
	}

	// 6. Plain HTTP on loopback hosts is OK
	for _, loopbackHost := range []string{"127.0.0.1", "::1", "localhost", "127.0.0.2"} {
		check = bindSecurityCheck(loopbackHost, false, "")
		if check.Severity != SevOK {
			t.Errorf("expected SevOK on loopback %s plain HTTP, got %v", loopbackHost, check.Severity)
		}
	}

	// 7. Plain HTTP on 0.0.0.0 with trusted proxies is OK
	check = bindSecurityCheck("0.0.0.0", false, "127.0.0.1/32")
	if check.Severity != SevOK {
		t.Errorf("expected SevOK on 0.0.0.0 with trusted proxies, got %v", check.Severity)
	}

	// 8. HTTPS on 0.0.0.0 is OK
	check = bindSecurityCheck("0.0.0.0", true, "")
	if check.Severity != SevOK {
		t.Errorf("expected SevOK on HTTPS on 0.0.0.0, got %v", check.Severity)
	}
}

func TestDoctorWiringTLS(t *testing.T) {
	dir := t.TempDir()
	certFile, keyFile := generateTestCertificate(t)
	// Partial TLS config
	envPath := filepath.Join(dir, ".env")
	if err := os.WriteFile(envPath, []byte("MYMCP_ADMIN_TOKEN=admin-tok\nMYMCP_TLS_CERT="+certFile+"\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	checks := Doctor(dir, newFakeSystem())
	c := findCheck(checks, "TLS configuration")
	if c == nil || c.Severity != SevFail {
		t.Fatalf("expected SevFail on partial TLS config, got %+v", c)
	}

	// Full valid TLS config
	if err := os.WriteFile(envPath, []byte("MYMCP_ADMIN_TOKEN=admin-tok\nMYMCP_TLS_CERT="+certFile+"\nMYMCP_TLS_KEY="+keyFile+"\nMYMCP_HOST=127.0.0.1\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	checks = Doctor(dir, newFakeSystem())
	certCheck := findCheck(checks, "TLS certificate")
	if certCheck == nil || certCheck.Severity != SevOK {
		t.Fatalf("expected SevOK on TLS certificate, got %+v", certCheck)
	}
	secCheck := findCheck(checks, "transport security")
	if secCheck == nil || secCheck.Severity != SevOK {
		t.Fatalf("expected SevOK on transport security, got %+v", secCheck)
	}
}
