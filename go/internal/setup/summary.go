package setup

import (
	"fmt"
	"io"
	"net"
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
	if host, err := net.LookupHost("localhost"); err == nil && len(host) > 0 {
		return host[0]
	}
	return "127.0.0.1"
}

// Summary prints the closing report of `mymcp init`: a URL a client can
// actually reach, the tokens (admin shown exactly once), a pasteable
// `claude mcp add` command and JSON snippet, and the next step.
func Summary(p *Plan, out ApplyOutcome, w io.Writer) {
	url := fmt.Sprintf("http://%s:%d/mcp", PrimaryAddress(p.Bind), p.Port)
	fmt.Fprintf(w, "\n✓ mymcp is configured on %s:%d\n\n", p.Bind, p.Port)
	fmt.Fprintf(w, "  URL     %s\n", url)
	fmt.Fprintf(w, "  Token   %s   (%s, name=%s)\n\n", out.ClientToken, p.ClientRole, p.ClientName)
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
