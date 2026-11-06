package httpserver

import (
	"net"
	"net/http"
	"strings"
)

// ExtractClientIP returns the real client IP. If the direct remote IP is within
// trustedCIDRs, it inspects X-Forwarded-For (walking right-to-left for the first
// untrusted IP) and X-Real-IP. If direct remote IP is not trusted or trustedCIDRs
// is empty, it returns the direct connection IP to prevent spoofing.
func ExtractClientIP(r *http.Request, trustedCIDRs []*net.IPNet) string {
	remoteHost, _, err := net.SplitHostPort(r.RemoteAddr)
	if err != nil {
		remoteHost = r.RemoteAddr
	}
	directIP := net.ParseIP(remoteHost)
	if directIP == nil {
		return "unknown"
	}
	if len(trustedCIDRs) == 0 || !ipInCIDRs(directIP, trustedCIDRs) {
		return directIP.String()
	}

	if xff := r.Header.Get("X-Forwarded-For"); xff != "" {
		parts := strings.Split(xff, ",")
		for i := len(parts) - 1; i >= 0; i-- {
			candidate := strings.TrimSpace(parts[i])
			cip := net.ParseIP(candidate)
			if cip == nil {
				continue
			}
			if !ipInCIDRs(cip, trustedCIDRs) {
				return cip.String()
			}
		}
		if len(parts) > 0 {
			if first := net.ParseIP(strings.TrimSpace(parts[0])); first != nil {
				return first.String()
			}
		}
	}

	if xri := strings.TrimSpace(r.Header.Get("X-Real-IP")); xri != "" {
		if rip := net.ParseIP(xri); rip != nil {
			return rip.String()
		}
	}

	return directIP.String()
}

func ipInCIDRs(ip net.IP, cidrs []*net.IPNet) bool {
	for _, cidr := range cidrs {
		if cidr.Contains(ip) {
			return true
		}
	}
	return false
}
