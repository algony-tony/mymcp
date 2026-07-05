// Command mymcp is the Go core of the mymcp MCP server.
package main

import (
	"flag"
	"fmt"
	"net/http"
	"os"

	"github.com/algony-tony/mymcp/go/internal/httpserver"
	"github.com/algony-tony/mymcp/go/internal/version"
)

func main() {
	os.Exit(run(os.Args[1:]))
}

func run(args []string) int {
	if len(args) == 0 {
		fmt.Fprintln(os.Stderr, "usage: mymcp {serve|version}")
		return 2
	}
	switch args[0] {
	case "version":
		fmt.Println("mymcp " + version.Version)
		return 0
	case "serve":
		fs := flag.NewFlagSet("serve", flag.ContinueOnError)
		envFile := fs.String("env-file", "", "path to .env file")
		host := fs.String("host", "", "bind host (overrides config)")
		port := fs.Int("port", 0, "bind port (overrides config)")
		if err := fs.Parse(args[1:]); err != nil {
			return 2
		}
		if *envFile != "" {
			os.Setenv("MYMCP_ENV_FILE", *envFile)
		}
		if err := httpserver.Serve(*host, *port, version.Version); err != nil && err != http.ErrServerClosed {
			fmt.Fprintln(os.Stderr, "serve:", err)
			return 1
		}
		return 0
	default:
		fmt.Fprintf(os.Stderr, "unknown command: %s\n", args[0])
		return 2
	}
}
