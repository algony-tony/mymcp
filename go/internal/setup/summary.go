package setup

import (
	"fmt"
	"io"
	"net"
	"strconv"
)

// PrimaryAddress turns a wildcard bind into an address a client can actually
// dial. This is the last metre of "paste it and it works".
func PrimaryAddress(bind string) string {
	if bind != "0.0.0.0" && bind != "::" && bind != "*" {
		return bind
	}
	// No packet is sent; this just asks the kernel which source address it
	// would pick for an off-box destination.
	conn, err := net.Dial("udp", "192.0.2.1:9")
	if err == nil {
		defer conn.Close()
		if host, _, err := net.SplitHostPort(conn.LocalAddr().String()); err == nil {
			return host
		}
	}
	// No default route (air-gapped host, or a container with only loopback):
	// prefer an IPv4 localhost result since it needs no bracketing and is the
	// more universally usable address; fall back to whatever localhost
	// resolves to (may be IPv6, which Summary brackets via JoinHostPort).
	if hosts, err := net.LookupHost("localhost"); err == nil {
		for _, h := range hosts {
			if ip := net.ParseIP(h); ip != nil && ip.To4() != nil {
				return h
			}
		}
		if len(hosts) > 0 {
			return hosts[0]
		}
	}
	return "127.0.0.1"
}

// Summary prints the closing report of `mymcp init`: a URL a client can
// actually reach, the tokens (admin shown exactly once), a pasteable
// `claude mcp add` command and JSON snippet, and the next step.
func Summary(p *Plan, out ApplyOutcome, w io.Writer) {
	// net.JoinHostPort brackets an IPv6 host correctly ("[::1]:8765") and
	// leaves an IPv4 or hostname host unchanged — a bare Sprintf("%s:%d", ...)
	// would emit an unparseable "http://::1:8765/mcp" for an IPv6 primary
	// address.
	url := "http://" + net.JoinHostPort(PrimaryAddress(p.Bind), strconv.Itoa(p.Port)) + "/mcp"
	fmt.Fprintf(w, "\n✓ mymcp is configured on %s:%d\n\n", p.Bind, p.Port)
	fmt.Fprintf(w, "  URL     %s\n", url)
	fmt.Fprintf(w, "  Token   %s   (%s, name=%s)\n", out.ClientToken, out.ClientRole, p.ClientName)
	if out.ClientRole != p.ClientRole {
		fmt.Fprintf(w, "  (existing token kept; role is %s, not %s — revoke and re-create to change it)\n",
			out.ClientRole, p.ClientRole)
	}
	fmt.Fprintln(w)
	fmt.Fprintf(w, "  claude mcp add --transport http mymcp %s \\\n", url)
	fmt.Fprintf(w, "      --header \"Authorization: Bearer %s\"\n\n", out.ClientToken)
	fmt.Fprintf(w, "  {\"mcpServers\":{\"mymcp\":{\"type\":\"http\",\"url\":%q,"+
		"\"headers\":{\"Authorization\":\"Bearer %s\"}}}}\n\n", url, out.ClientToken)
	fmt.Fprintf(w, "  Admin token: %s   (shown once; also recoverable from %s)\n\n", out.AdminToken, p.EnvPath())
	if !p.HasSystemd {
		fmt.Fprintf(w, "  No systemd here — run it yourself:\n    mymcp serve -env-file %s\n\n", p.EnvPath())
	}
	fmt.Fprintf(w, "  Next: mymcp doctor  |  journalctl -u mymcp -f\n")
	fmt.Fprintf(w, "  To remove: systemctl disable --now mymcp && rm %s && systemctl daemon-reload\n", p.UnitPath())
}
