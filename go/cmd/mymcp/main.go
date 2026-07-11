// Command mymcp is the Go core of the mymcp MCP server.
package main

import (
	"flag"
	"fmt"
	"net/http"
	"os"

	"github.com/algony-tony/mymcp/go/internal/auth"
	"github.com/algony-tony/mymcp/go/internal/config"
	"github.com/algony-tony/mymcp/go/internal/httpserver"
	"github.com/algony-tony/mymcp/go/internal/version"
)

func main() {
	os.Exit(run(os.Args[1:]))
}

func run(args []string) int {
	if len(args) == 0 {
		fmt.Fprintln(os.Stderr, "usage: mymcp {serve|version|token}")
		return 2
	}
	switch args[0] {
	case "version":
		fmt.Println("mymcp " + version.Version)
		return 0
	case "token":
		return runToken(args[1:])
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

func loadTokenStore() (*auth.TokenStore, error) {
	cfg, err := config.Load()
	if err != nil {
		return nil, err
	}
	admin := cfg.AdminToken
	if admin == "" {
		admin = "unset" // list/add/revoke operate on the file; admin value is not consulted here
	}
	return auth.NewTokenStore(cfg.TokenFile, admin)
}

func runToken(args []string) int {
	if len(args) == 0 {
		fmt.Fprintln(os.Stderr, "usage: mymcp token {list|add|revoke}")
		return 2
	}
	switch args[0] {
	case "list":
		store, err := loadTokenStore()
		if err != nil {
			fmt.Fprintln(os.Stderr, "token:", err)
			return 1
		}
		toks := store.ListTokens()
		if len(toks) == 0 {
			fmt.Println("(no ro/rw tokens)")
			return 0
		}
		for tok, info := range toks {
			fmt.Printf("%-2s  %-20s  %s\n", info.Role, info.Name, tok)
		}
		return 0
	case "add":
		fs := flag.NewFlagSet("add", flag.ContinueOnError)
		role := fs.String("role", "ro", "token role: ro or rw")
		if err := fs.Parse(args[1:]); err != nil {
			return 2
		}
		if fs.NArg() < 1 {
			fmt.Fprintln(os.Stderr, "usage: mymcp token add [--role ro|rw] <name>")
			return 2
		}
		store, err := loadTokenStore()
		if err != nil {
			fmt.Fprintln(os.Stderr, "token:", err)
			return 1
		}
		tok, err := store.CreateToken(fs.Arg(0), *role)
		if err != nil {
			fmt.Fprintln(os.Stderr, "token:", err)
			return 1
		}
		fmt.Println(tok)
		return 0
	case "revoke":
		if len(args) < 2 {
			fmt.Fprintln(os.Stderr, "usage: mymcp token revoke <token>")
			return 2
		}
		store, err := loadTokenStore()
		if err != nil {
			fmt.Fprintln(os.Stderr, "token:", err)
			return 1
		}
		if store.RevokeToken(args[1]) {
			fmt.Printf("revoked %s\n", args[1])
			return 0
		}
		fmt.Fprintf(os.Stderr, "not found: %s\n", args[1])
		return 1
	default:
		fmt.Fprintf(os.Stderr, "unknown token subcommand: %s\n", args[0])
		return 2
	}
}
