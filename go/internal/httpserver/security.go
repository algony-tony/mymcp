package httpserver

import "crypto/subtle"

// constantTimeEquals compares two strings in constant time to mitigate timing attacks.
func constantTimeEquals(a, b string) bool {
	return subtle.ConstantTimeCompare([]byte(a), []byte(b)) == 1
}
