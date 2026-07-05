// Package version exposes the build version, injected via ldflags:
//
//	go build -ldflags "-X github.com/algony-tony/mymcp/go/internal/version.Version=v3.0.0"
package version

var Version = "dev"
