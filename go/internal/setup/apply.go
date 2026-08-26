package setup

import (
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"time"

	"github.com/algony-tony/mymcp/go/internal/auth"
)

type Status string

const (
	StatusCreated   Status = "created"
	StatusUpdated   Status = "updated"
	StatusUnchanged Status = "unchanged"
	StatusSkipped   Status = "skipped"
)

// errNoSuchUser is what a fake System returns for `id -u <name>` when the
// account does not exist; the real System returns exec's non-zero exit error.
var errNoSuchUser = errors.New("no such user")

type Result struct {
	Step   string
	Status Status
	Detail string
}

type ApplyOutcome struct {
	Results     []Result
	AdminToken  string
	ClientToken string
}

// runOut wraps a System.Run failure with the command's own output, which is
// where the actionable message lives: mymcp-recorder prints a precise reason,
// and systemctl points at journalctl. Wrapping only err yields "exit status 1".
func runOut(sys System, what, name string, args ...string) error {
	out, err := sys.Run(name, args...)
	if err == nil {
		return nil
	}
	if trimmed := strings.TrimSpace(out); trimmed != "" {
		return fmt.Errorf("%s: %w: %s", what, err, trimmed)
	}
	return fmt.Errorf("%s: %w", what, err)
}

// Apply runs every step in order. Each step is idempotent, so a run that fails
// halfway can simply be re-run: there is deliberately no rollback, because a
// partial rollback is more dangerous than none.
func Apply(p *Plan, sys System) (ApplyOutcome, error) {
	out := ApplyOutcome{}
	add := func(step string, st Status, detail string) {
		out.Results = append(out.Results, Result{Step: step, Status: st, Detail: detail})
	}

	// 1. Service user.
	if p.ServiceUser != "root" && p.ServiceUser != "" {
		if p.DryRun {
			add("service user", StatusSkipped, "would ensure "+p.ServiceUser+" exists")
		} else if _, err := sys.Run("id", "-u", p.ServiceUser); err != nil {
			if err := runOut(sys, "useradd "+p.ServiceUser, "useradd", "-r", "-s", "/usr/sbin/nologin", p.ServiceUser); err != nil {
				return out, err
			}
			add("service user", StatusCreated, p.ServiceUser)
		} else {
			add("service user", StatusUnchanged, p.ServiceUser)
		}
	}

	// 2. Directories.
	for _, dir := range []string{p.ConfigDir, p.LogDir, p.RecorderDataDir} {
		st, err := ensureDir(p, dir)
		if err != nil {
			return out, err
		}
		add("dir "+dir, st, "")
	}

	// 3. .env — line-merged, never overwritten; admin token preserved.
	existing, _ := os.ReadFile(p.EnvPath())
	admin := ExistingAdminToken(string(existing))
	if admin == "" {
		tok, err := auth.GenerateToken()
		if err != nil {
			return out, err
		}
		admin = tok
	}
	out.AdminToken = admin

	var content string
	status := StatusUpdated
	if len(existing) == 0 {
		content = RenderEnv(p, admin)
		status = StatusCreated
	} else {
		content = MergeEnv(string(existing), OwnedKeys(p, admin))
		if content == string(existing) {
			status = StatusUnchanged
		}
	}
	if p.DryRun {
		add("env "+p.EnvPath(), StatusSkipped, "would write (dry-run)")
	} else {
		if status != StatusUnchanged {
			if len(existing) > 0 {
				bak := fmt.Sprintf("%s.bak-%s", p.EnvPath(), time.Now().UTC().Format("20060102T150405Z"))
				if err := os.WriteFile(bak, existing, 0o600); err != nil {
					return out, fmt.Errorf("backup %s: %w", bak, err)
				}
			}
			if err := os.WriteFile(p.EnvPath(), []byte(content), 0o600); err != nil {
				return out, fmt.Errorf("write %s: %w", p.EnvPath(), err)
			}
		}
		add("env "+p.EnvPath(), status, "")
	}

	// 4+5. Token store and the first client token, deduplicated by name.
	if p.DryRun {
		add("client token", StatusSkipped, "would create "+p.ClientName)
	} else {
		store, err := auth.NewTokenStore(p.TokenPath(), admin)
		if err != nil {
			return out, err
		}
		for tok, info := range store.ListTokens() {
			if info.Name == p.ClientName {
				out.ClientToken = tok
			}
		}
		if out.ClientToken == "" {
			tok, err := store.CreateToken(p.ClientName, p.ClientRole)
			if err != nil {
				return out, err
			}
			out.ClientToken = tok
			add("client token", StatusCreated, p.ClientName+" ("+p.ClientRole+")")
		} else {
			add("client token", StatusUnchanged, p.ClientName)
		}
	}

	// 5b. ripgrep. Optional: grep falls back to a native scan without it.
	if p.InstallRipgrep {
		if p.DryRun {
			add("ripgrep", StatusSkipped, "would install (dry-run)")
		} else if st, detail, err := installRipgrep(p, sys); err != nil {
			// A missing rg degrades grep; it must never fail the install.
			add("ripgrep", StatusSkipped, err.Error())
		} else {
			add("ripgrep", st, detail)
		}
	}

	// 6. Unit + daemon-reload. Skipped wholesale in degraded mode.
	if !p.HasSystemd {
		add("systemd unit", StatusSkipped, "systemd not present")
		add("service start", StatusSkipped, "systemd not present")
		return out, nil
	}
	st, err := writeIfChanged(p, p.UnitPath(), RenderUnit(p), 0o644)
	if err != nil {
		return out, err
	}
	add("systemd unit", st, p.UnitPath())
	if !p.DryRun {
		if err := runOut(sys, "systemctl daemon-reload", "systemctl", "daemon-reload"); err != nil {
			return out, err
		}
	}

	// 7. Start. The main service comes up before the optional recorder sidecar
	// (below) is touched: the product must not be held hostage to the sidecar.
	if !p.Start || p.DryRun {
		add("service start", StatusSkipped, "-start=false or dry-run")
	} else {
		if err := runOut(sys, "systemctl enable --now mymcp", "systemctl", "enable", "--now", "mymcp"); err != nil {
			return out, err
		}
		if err := runOut(sys, "systemctl restart mymcp", "systemctl", "restart", "mymcp"); err != nil {
			return out, err
		}
		add("service start", StatusUpdated, "enabled and running")
	}

	// 8. Recorder sidecar. The unit template is owned by the Python package;
	// we shell out to it rather than keeping a second copy of the template.
	// This runs after the main service is already started (see step 7):
	// it's optional, and its own unit declares After=/Wants=mymcp.service.
	if p.Recorder.Enabled {
		if p.DryRun {
			add("recorder", StatusSkipped, "would install the sidecar unit (dry-run)")
		} else {
			if p.Recorder.NeedsInject {
				if err := runOut(sys, "pipx inject recorder extra", "pipx", "inject", "algony-mymcp", "algony-mymcp[recorder]"); err != nil {
					return out, err
				}
				add("recorder deps", StatusCreated, "pipx inject")
			}
			if err := runOut(sys, "render recorder unit", "mymcp-recorder", "--install-unit",
				"--service-user", p.ServiceUser,
				"--env-file", p.EnvPath(),
				"--output", p.RecorderUnitPath()); err != nil {
				return out, err
			}
			add("recorder unit", StatusCreated, p.RecorderUnitPath())
			if err := runOut(sys, "systemctl daemon-reload", "systemctl", "daemon-reload"); err != nil {
				return out, err
			}
			if p.Start {
				if err := runOut(sys, "start mymcp-recorder", "systemctl", "enable", "--now", "mymcp-recorder"); err != nil {
					return out, err
				}
				add("recorder service", StatusUpdated, "enabled and running")
			}
		}
	}

	return out, nil
}

// installRipgrep prefers a binary handed to us (the offline bundle's) and
// otherwise asks whichever package manager this distro has.
func installRipgrep(p *Plan, sys System) (Status, string, error) {
	if _, err := sys.LookPath("rg"); err == nil {
		return StatusUnchanged, "already installed", nil
	}
	if p.RipgrepBinary != "" {
		raw, err := os.ReadFile(p.RipgrepBinary)
		if err != nil {
			return StatusSkipped, "", err
		}
		dest := "/usr/local/bin/rg"
		if err := os.WriteFile(dest, raw, 0o755); err != nil {
			return StatusSkipped, "", err
		}
		return StatusCreated, dest, nil
	}
	for _, pm := range [][]string{
		{"apt-get", "install", "-y", "ripgrep"},
		{"dnf", "install", "-y", "ripgrep"},
		{"pacman", "-S", "--noconfirm", "ripgrep"},
	} {
		if _, err := sys.LookPath(pm[0]); err != nil {
			continue
		}
		if _, err := sys.Run(pm[0], pm[1:]...); err != nil {
			return StatusSkipped, "", fmt.Errorf("%s: %w", pm[0], err)
		}
		return StatusCreated, pm[0], nil
	}
	return StatusSkipped, "", fmt.Errorf("no supported package manager found")
}

func ensureDir(p *Plan, dir string) (Status, error) {
	if _, err := os.Stat(dir); err == nil {
		return StatusUnchanged, nil
	}
	if p.DryRun {
		return StatusSkipped, nil
	}
	if err := os.MkdirAll(dir, 0o750); err != nil {
		return StatusUnchanged, fmt.Errorf("mkdir %s: %w", dir, err)
	}
	return StatusCreated, nil
}

func writeIfChanged(p *Plan, path, content string, mode os.FileMode) (Status, error) {
	old, err := os.ReadFile(path)
	if err == nil && string(old) == content {
		return StatusUnchanged, nil
	}
	if p.DryRun {
		return StatusSkipped, nil
	}
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		return StatusUnchanged, err
	}
	if err := os.WriteFile(path, []byte(content), mode); err != nil {
		return StatusUnchanged, fmt.Errorf("write %s: %w", path, err)
	}
	if len(old) == 0 {
		return StatusCreated, nil
	}
	return StatusUpdated, nil
}
