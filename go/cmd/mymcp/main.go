// Command mymcp is the Go core of the mymcp MCP server.
package main

import (
	"fmt"
	"os"

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
		fmt.Fprintln(os.Stderr, "serve: not implemented yet")
		return 1
	default:
		fmt.Fprintf(os.Stderr, "unknown command: %s\n", args[0])
		return 2
	}
}
