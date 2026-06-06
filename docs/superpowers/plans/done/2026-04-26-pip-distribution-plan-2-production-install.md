# mymcp 2.0 Plan 2: Production Install

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the production-deployment subcommands (`install-service`, `uninstall-service`, `token`, `migrate-from-legacy`, `doctor`) on top of the Plan 1 foundation. Replace `deploy/install.sh` for new installs while keeping the legacy bash flow available for 1.x users.

**Architecture:** All system-touching logic lives in `src/mymcp/deploy/` with three layers: pure builders (`render_service_unit`, `build_env_dict`) — easy to unit-test; effectful wrappers (`write_env_file`, `enable_systemd_unit`) — minimal, mockable; CLI subcommands in `cli.py` that orchestrate them. Token management reuses the existing `TokenStore` from `mymcp.auth`. Migration command reads the 1.x `.env`, rewrites `MCP_*`→`MYMCP_*` keys, and reuses the install-service flow.

**Tech Stack:** Python 3.11+ stdlib (subprocess, shutil, secrets, getpass, importlib.resources), pytest with monkeypatch for system-side mocking, jinja-style template rendering via `str.format` (no jinja dep).

**Source spec:** `docs/superpowers/specs/2026-04-26-pip-distribution-design.md` (§4, §5).

**Out of scope (Plan 3):** PyPI publish, offline bundle, README rewrite, CHANGELOG 2.0.0 entry, version 2.0.0 tag.

---

## File Structure

**Create:**
- `src/mymcp/deploy/__init__.py`
- `src/mymcp/deploy/setup.py` — pure builders + effectful wrappers for config/dir/env bootstrap
- `src/mymcp/deploy/service.py` — systemd unit render + install/enable/stop/disable + ripgrep installer
- `src/mymcp/deploy/migrate.py` — legacy-install detection and migration
- `src/mymcp/deploy/templates/mymcp.service.in` — systemd unit template
- `tests/test_deploy_setup.py`
- `tests/test_deploy_service.py`
- `tests/test_deploy_migrate.py`
- `tests/test_token_cli.py`
- `tests/test_doctor.py`

**Modify:**
- `src/mymcp/cli.py` — register `install-service`, `uninstall-service`, `token`, `migrate-from-legacy`, `doctor` subparsers
- `pyproject.toml` — already declares `package-data = ["deploy/templates/*.in"]` from Plan 1; verify

---

### Task 1: Scaffold deploy package + service template

**Files:** `src/mymcp/deploy/__init__.py`, `src/mymcp/deploy/templates/mymcp.service.in`

- [ ] **Step 1: Write `src/mymcp/deploy/__init__.py`** (empty package marker):

```python
"""System-deployment helpers (install-service, migrate-from-legacy, etc.)."""
```

- [ ] **Step 2: Write `src/mymcp/deploy/templates/mymcp.service.in`**:

```
[Unit]
Description=MyMCP Server
After=network.target

[Service]
Type=simple
User={service_user}
EnvironmentFile={env_file}
ExecStart={exec_start}
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```

- [ ] **Step 3: Commit**

```bash
git add src/mymcp/deploy/__init__.py src/mymcp/deploy/templates/mymcp.service.in
git commit -m "feat(deploy): scaffold deploy package + systemd unit template"
```

---

### Task 2: Failing tests for setup builders

**Files:** `tests/test_deploy_setup.py`

- [ ] **Step 1: Write the failing tests**

```python
"""Pure-function tests for deploy/setup.py builders."""
import json
import os

import pytest


def test_build_env_dict_minimal():
    from mymcp.deploy.setup import build_env_dict
    d = build_env_dict(
        host="0.0.0.0", port=8765, admin_token="adm",
        metrics_token="", token_file="/etc/mymcp/tokens.json",
        audit_enabled=True, audit_log_dir="/var/log/mymcp",
    )
    assert d["MYMCP_HOST"] == "0.0.0.0"
    assert d["MYMCP_PORT"] == "8765"
    assert d["MYMCP_ADMIN_TOKEN"] == "adm"
    assert d["MYMCP_TOKEN_FILE"] == "/etc/mymcp/tokens.json"
    assert d["MYMCP_AUDIT_ENABLED"] == "true"
    assert d["MYMCP_AUDIT_LOG_DIR"] == "/var/log/mymcp"
    assert "MYMCP_METRICS_TOKEN" in d
    assert d["MYMCP_METRICS_TOKEN"] == ""


def test_build_env_dict_audit_disabled_lowercased():
    from mymcp.deploy.setup import build_env_dict
    d = build_env_dict(
        host="0.0.0.0", port=8765, admin_token="adm", metrics_token="m",
        token_file="/etc/mymcp/tokens.json",
        audit_enabled=False, audit_log_dir="/var/log/mymcp",
    )
    assert d["MYMCP_AUDIT_ENABLED"] == "false"


def test_format_env_file_round_trips_dict():
    from mymcp.deploy.setup import format_env_file
    text = format_env_file({"A": "1", "B": "two", "C": ""})
    lines = text.strip().splitlines()
    assert "A=1" in lines
    assert "B=two" in lines
    assert "C=" in lines


def test_write_env_file_sets_mode_600(tmp_path):
    from mymcp.deploy.setup import write_env_file
    target = tmp_path / "new.env"
    write_env_file(target, {"X": "1"})
    assert target.exists()
    assert (target.stat().st_mode & 0o777) == 0o600


def test_write_empty_token_store(tmp_path):
    from mymcp.deploy.setup import write_empty_token_store
    target = tmp_path / "tokens.json"
    write_empty_token_store(target, admin_token="adm")
    body = json.loads(target.read_text())
    assert body == {"tokens": {}, "admin_token": "adm"}
    assert (target.stat().st_mode & 0o777) == 0o600


def test_make_token_returns_prefixed_hex():
    from mymcp.deploy.setup import make_token
    t = make_token()
    assert t.startswith("tok_")
    assert len(t) == len("tok_") + 32  # 16 bytes hex
```

- [ ] **Step 2: Run, expect all to fail with ImportError**

```bash
.venv/bin/pytest tests/test_deploy_setup.py -v --tb=short
```

- [ ] **Step 3: Commit**

```bash
git add tests/test_deploy_setup.py
git commit -m "test: add failing tests for deploy/setup pure builders"
```

---

### Task 3: Implement deploy/setup.py

**Files:** `src/mymcp/deploy/setup.py`

- [ ] **Step 1: Write the module**

```python
"""Builders + writers for the install-service config bootstrap step."""
from __future__ import annotations

import json
import os
import secrets
from pathlib import Path


def make_token() -> str:
    return "tok_" + secrets.token_hex(16)


def build_env_dict(
    *,
    host: str,
    port: int,
    admin_token: str,
    metrics_token: str,
    token_file: str,
    audit_enabled: bool,
    audit_log_dir: str,
) -> dict[str, str]:
    return {
        "MYMCP_HOST": host,
        "MYMCP_PORT": str(port),
        "MYMCP_ADMIN_TOKEN": admin_token,
        "MYMCP_METRICS_TOKEN": metrics_token,
        "MYMCP_TOKEN_FILE": token_file,
        "MYMCP_AUDIT_ENABLED": "true" if audit_enabled else "false",
        "MYMCP_AUDIT_LOG_DIR": audit_log_dir,
    }


def format_env_file(env: dict[str, str]) -> str:
    lines = [f"{k}={v}" for k, v in env.items()]
    return "\n".join(lines) + "\n"


def write_env_file(path: Path | str, env: dict[str, str]) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(format_env_file(env))
    os.chmod(p, 0o600)


def write_empty_token_store(path: Path | str, *, admin_token: str) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    body = {"tokens": {}, "admin_token": admin_token}
    p.write_text(json.dumps(body, indent=2))
    os.chmod(p, 0o600)


def ensure_directory(path: Path | str, mode: int = 0o750) -> None:
    p = Path(path)
    p.mkdir(parents=True, exist_ok=True)
    os.chmod(p, mode)


def update_env_file(path: Path | str, updates: dict[str, str]) -> None:
    """Merge `updates` into an existing .env file, preserving order. Mode 600."""
    p = Path(path)
    if p.exists():
        existing: dict[str, str] = {}
        for raw in p.read_text().splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            existing[k.strip()] = v.strip()
        existing.update(updates)
    else:
        existing = dict(updates)
    write_env_file(p, existing)
```

- [ ] **Step 2: Verify all tests pass**

```bash
.venv/bin/pytest tests/test_deploy_setup.py -v
```

- [ ] **Step 3: Commit**

```bash
git add src/mymcp/deploy/setup.py
git commit -m "feat(deploy): add setup builders for env file and token store"
```

---

### Task 4: Failing tests for service unit rendering + ripgrep helper

**Files:** `tests/test_deploy_service.py`

- [ ] **Step 1: Write the tests**

```python
"""Tests for deploy/service.py — unit render, helpers, ripgrep install."""
import textwrap
from unittest.mock import patch


def test_render_service_unit_substitutes_fields():
    from mymcp.deploy.service import render_service_unit
    out = render_service_unit(
        service_user="root",
        env_file="/etc/mymcp/.env",
        exec_start="/usr/local/bin/mymcp serve --env-file /etc/mymcp/.env",
    )
    assert "User=root" in out
    assert "EnvironmentFile=/etc/mymcp/.env" in out
    assert "ExecStart=/usr/local/bin/mymcp serve --env-file /etc/mymcp/.env" in out
    assert out.strip().startswith("[Unit]")


def test_resolve_mymcp_executable_uses_which(monkeypatch):
    from mymcp.deploy import service
    monkeypatch.setattr(service.shutil, "which", lambda name: "/opt/pipx/bin/mymcp")
    assert service.resolve_mymcp_executable() == "/opt/pipx/bin/mymcp"


def test_resolve_mymcp_executable_raises_if_missing(monkeypatch):
    from mymcp.deploy import service
    monkeypatch.setattr(service.shutil, "which", lambda name: None)
    import pytest
    with pytest.raises(RuntimeError, match="not on PATH"):
        service.resolve_mymcp_executable()


def test_systemd_available_checks_run_systemd_dir(monkeypatch, tmp_path):
    from mymcp.deploy import service
    monkeypatch.setattr(service, "_RUN_SYSTEMD", str(tmp_path))
    assert service.systemd_available() is True

    nonex = tmp_path / "absent"
    monkeypatch.setattr(service, "_RUN_SYSTEMD", str(nonex))
    assert service.systemd_available() is False


def test_render_logrotate_config():
    from mymcp.deploy.service import render_logrotate_config
    out = render_logrotate_config("/var/log/mymcp")
    assert "/var/log/mymcp/audit.log" in out
    assert "rotate" in out
```

- [ ] **Step 2: Run, expect failure**

```bash
.venv/bin/pytest tests/test_deploy_service.py -v --tb=short
```

- [ ] **Step 3: Commit**

```bash
git add tests/test_deploy_service.py
git commit -m "test: add failing tests for deploy/service render + helpers"
```

---

### Task 5: Implement deploy/service.py

**Files:** `src/mymcp/deploy/service.py`

- [ ] **Step 1: Write the module**

```python
"""Systemd unit rendering and side-effectful service install helpers."""
from __future__ import annotations

import os
import shutil
import subprocess
from importlib import resources
from pathlib import Path

_RUN_SYSTEMD = "/run/systemd/system"
_SYSTEMD_UNIT_PATH = Path("/etc/systemd/system/mymcp.service")
_LOGROTATE_PATH = Path("/etc/logrotate.d/mymcp")


def systemd_available() -> bool:
    return Path(_RUN_SYSTEMD).is_dir()


def resolve_mymcp_executable() -> str:
    path = shutil.which("mymcp")
    if not path:
        raise RuntimeError("`mymcp` executable not on PATH; pipx install may have failed.")
    return path


def _read_template() -> str:
    return resources.files("mymcp.deploy.templates").joinpath("mymcp.service.in").read_text()


def render_service_unit(*, service_user: str, env_file: str, exec_start: str) -> str:
    return _read_template().format(
        service_user=service_user,
        env_file=env_file,
        exec_start=exec_start,
    )


def write_systemd_unit(text: str, path: Path = _SYSTEMD_UNIT_PATH) -> None:
    path.write_text(text)
    os.chmod(path, 0o644)


def render_logrotate_config(log_dir: str) -> str:
    return (
        f"{log_dir}/audit.log {{\n"
        "    weekly\n"
        "    rotate 8\n"
        "    compress\n"
        "    missingok\n"
        "    notifempty\n"
        "    create 0640 root root\n"
        "}\n"
    )


def write_logrotate_config(text: str, path: Path = _LOGROTATE_PATH) -> None:
    path.write_text(text)
    os.chmod(path, 0o644)


def systemctl(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(["systemctl", *args], check=check, capture_output=True, text=True)


def daemon_reload() -> None:
    systemctl("daemon-reload")


def enable_service(name: str = "mymcp") -> None:
    systemctl("enable", name)


def disable_service(name: str = "mymcp", check: bool = False) -> None:
    systemctl("disable", name, check=check)


def stop_service(name: str = "mymcp", check: bool = False) -> None:
    systemctl("stop", name, check=check)


def ensure_service_user(username: str) -> None:
    """Create a system user for running the service. No-op if already exists."""
    try:
        subprocess.run(["id", "-u", username], check=True, capture_output=True)
        return
    except subprocess.CalledProcessError:
        pass
    subprocess.run(
        ["useradd", "-r", "-s", "/usr/sbin/nologin", username],
        check=True, capture_output=True, text=True,
    )


def install_ripgrep() -> bool:
    """Install ripgrep via apt/dnf/pacman or fall back to GitHub release tarball.
    Returns True on success."""
    if shutil.which("rg"):
        return True
    for cmd in (
        ["apt-get", "install", "-y", "-qq", "ripgrep"],
        ["dnf", "install", "-y", "-q", "ripgrep"],
        ["pacman", "-S", "--noconfirm", "ripgrep"],
    ):
        if shutil.which(cmd[0]):
            try:
                subprocess.run(cmd, check=True, capture_output=True)
                if shutil.which("rg"):
                    return True
            except subprocess.CalledProcessError:
                pass
    return False
```

- [ ] **Step 2: Verify tests pass**

```bash
.venv/bin/pytest tests/test_deploy_service.py -v
```

- [ ] **Step 3: Commit**

```bash
git add src/mymcp/deploy/service.py
git commit -m "feat(deploy): add systemd unit render + service helpers"
```

---

### Task 6: install-service / uninstall-service CLI tests + impl

**Files:** `tests/test_install_service_cli.py`, `src/mymcp/cli.py`

- [ ] **Step 1: Write CLI orchestration tests with mocked side effects**

```python
"""End-to-end tests for the install-service / uninstall-service CLI subcommands.

System-side calls (systemctl, useradd, ripgrep install) are patched. File
writes go to tmp_path. The point: verify the orchestration calls the right
helpers in the right order with the right arguments."""
import sys
from unittest.mock import patch, MagicMock

import pytest


@pytest.fixture
def mocked_root(monkeypatch):
    monkeypatch.setattr("os.geteuid", lambda: 0)


def _run_cli(*args):
    from mymcp.cli import main
    return main(list(args))


def test_install_service_requires_root(monkeypatch, capsys):
    monkeypatch.setattr("os.geteuid", lambda: 1000)
    rc = _run_cli("install-service", "--yes")
    out = capsys.readouterr()
    assert rc != 0
    assert "sudo" in (out.out + out.err).lower() or "root" in (out.out + out.err).lower()


def test_install_service_writes_files_and_unit(tmp_path, mocked_root, monkeypatch, capsys):
    cfg = tmp_path / "mymcp"
    log = tmp_path / "mymcp-log"
    monkeypatch.setattr(
        "mymcp.deploy.service.systemd_available", lambda: True,
    )
    monkeypatch.setattr(
        "mymcp.deploy.service.resolve_mymcp_executable", lambda: "/usr/bin/mymcp",
    )
    monkeypatch.setattr("mymcp.deploy.service.daemon_reload", lambda: None)
    monkeypatch.setattr("mymcp.deploy.service.enable_service", lambda *a, **kw: None)
    written_unit = {}
    def _write_unit(text, path=None):
        written_unit["text"] = text
        written_unit["path"] = path
    monkeypatch.setattr("mymcp.deploy.service.write_systemd_unit", _write_unit)
    monkeypatch.setattr("mymcp.deploy.service.write_logrotate_config", lambda *a, **kw: None)
    monkeypatch.setattr("mymcp.deploy.service.install_ripgrep", lambda: True)
    monkeypatch.setattr("mymcp.deploy.service.ensure_service_user", lambda u: None)

    rc = _run_cli(
        "install-service",
        "--config-dir", str(cfg),
        "--log-dir", str(log),
        "--port", "9999",
        "--bind", "127.0.0.1",
        "--no-metrics",
        "--enable-audit",
        "--service-user", "root",
        "--skip-ripgrep",
        "--yes",
    )
    assert rc == 0
    assert (cfg / ".env").exists()
    env_text = (cfg / ".env").read_text()
    assert "MYMCP_PORT=9999" in env_text
    assert "MYMCP_HOST=127.0.0.1" in env_text
    assert "MYMCP_AUDIT_ENABLED=true" in env_text
    # METRICS_TOKEN line present but empty
    assert "MYMCP_METRICS_TOKEN=" in env_text
    assert (cfg / "tokens.json").exists()
    assert (log).exists()

    assert "User=root" in written_unit["text"]
    assert f"EnvironmentFile={cfg}/.env" in written_unit["text"]
    assert f"--env-file {cfg}/.env" in written_unit["text"]


def test_uninstall_service_calls_stop_disable(monkeypatch, mocked_root):
    calls = []
    monkeypatch.setattr("mymcp.deploy.service.systemd_available", lambda: True)
    monkeypatch.setattr(
        "mymcp.deploy.service.stop_service",
        lambda *a, **kw: calls.append(("stop", a, kw)),
    )
    monkeypatch.setattr(
        "mymcp.deploy.service.disable_service",
        lambda *a, **kw: calls.append(("disable", a, kw)),
    )
    monkeypatch.setattr("mymcp.deploy.service.daemon_reload", lambda: calls.append(("reload",)))
    # Patch the unit/logrotate path to a tmp file we control
    import mymcp.deploy.service as svc
    from pathlib import Path
    fake_unit = MagicMock(spec=Path)
    fake_unit.exists.return_value = True
    fake_unit.unlink = lambda: calls.append(("unlink-unit",))
    monkeypatch.setattr(svc, "_SYSTEMD_UNIT_PATH", fake_unit)
    fake_lr = MagicMock(spec=Path)
    fake_lr.exists.return_value = False
    monkeypatch.setattr(svc, "_LOGROTATE_PATH", fake_lr)

    rc = _run_cli("uninstall-service")
    assert rc == 0
    op_names = [c[0] for c in calls]
    assert "stop" in op_names
    assert "disable" in op_names
    assert "unlink-unit" in op_names
    assert "reload" in op_names
```

- [ ] **Step 2: Run, expect failure**

```bash
.venv/bin/pytest tests/test_install_service_cli.py -v --tb=short
```

- [ ] **Step 3: Extend `src/mymcp/cli.py` with the new subcommands**

Add the following functions after `cmd_version` (before `build_parser`):

```python
def _require_root() -> bool:
    import os as _os
    if _os.geteuid() != 0:
        print("error: install-service requires root. Re-run with sudo.", file=sys.stderr)
        return False
    return True


def cmd_install_service(args: argparse.Namespace) -> int:
    if not _require_root():
        return 2

    from mymcp.deploy import service, setup

    if not service.systemd_available():
        print("error: systemd does not appear to be running.", file=sys.stderr)
        return 2

    exec_path = service.resolve_mymcp_executable()
    cfg_dir = os.path.abspath(args.config_dir)
    log_dir = os.path.abspath(args.log_dir)
    env_file = os.path.join(cfg_dir, ".env")
    token_file = os.path.join(cfg_dir, "tokens.json")

    if args.service_user != "root":
        service.ensure_service_user(args.service_user)

    setup.ensure_directory(cfg_dir, mode=0o750)
    setup.ensure_directory(log_dir, mode=0o750)

    admin_token = setup.make_token()
    metrics_token = setup.make_token() if args.enable_metrics else ""

    env = setup.build_env_dict(
        host=args.bind,
        port=args.port,
        admin_token=admin_token,
        metrics_token=metrics_token,
        token_file=token_file,
        audit_enabled=args.enable_audit,
        audit_log_dir=log_dir,
    )
    setup.write_env_file(env_file, env)
    setup.write_empty_token_store(token_file, admin_token=admin_token)

    if args.enable_audit:
        service.write_logrotate_config(service.render_logrotate_config(log_dir))

    if args.install_ripgrep:
        service.install_ripgrep()

    unit_text = service.render_service_unit(
        service_user=args.service_user,
        env_file=env_file,
        exec_start=f"{exec_path} serve --env-file {env_file}",
    )
    service.write_systemd_unit(unit_text)
    service.daemon_reload()
    service.enable_service()

    print("=== mymcp install-service complete ===")
    print(f"  Config dir:    {cfg_dir}")
    print(f"  Log dir:       {log_dir}")
    print(f"  Service user:  {args.service_user}")
    print(f"  Listening:     {args.bind}:{args.port}")
    print(f"\n  *** Admin token:   {admin_token} ***")
    if metrics_token:
        print(f"  *** Metrics token: {metrics_token} ***")
    print("  (Tokens are in /etc/mymcp/.env — last chance to copy them)\n")
    print(f"  Start:  sudo systemctl start mymcp")
    print(f"  Status: sudo systemctl status mymcp")
    print(f"  Logs:   sudo journalctl -u mymcp -f")
    return 0


def cmd_uninstall_service(args: argparse.Namespace) -> int:
    if not _require_root():
        return 2
    from mymcp.deploy import service
    if not service.systemd_available():
        print("error: systemd does not appear to be running.", file=sys.stderr)
        return 2
    service.stop_service()
    service.disable_service()
    if service._SYSTEMD_UNIT_PATH.exists():
        service._SYSTEMD_UNIT_PATH.unlink()
    if service._LOGROTATE_PATH.exists():
        service._LOGROTATE_PATH.unlink()
    service.daemon_reload()
    if args.purge:
        import shutil as _sh
        for p in (args.config_dir, args.log_dir):
            if os.path.isdir(p):
                _sh.rmtree(p)
        print(f"Purged: {args.config_dir} {args.log_dir}")
    print("mymcp service removed.")
    return 0
```

Then in `build_parser`, add the new subparsers (after `p_version`):

```python
    p_install = sub.add_parser("install-service", help="Install systemd service (requires sudo)")
    p_install.add_argument("--port", type=int, default=8765)
    p_install.add_argument("--bind", default="0.0.0.0")
    p_install.add_argument("--config-dir", default="/etc/mymcp")
    p_install.add_argument("--log-dir", default="/var/log/mymcp")
    p_install.add_argument("--service-user", default="root", choices=["root", "mymcp"])
    grp_m = p_install.add_mutually_exclusive_group()
    grp_m.add_argument("--enable-metrics", dest="enable_metrics", action="store_true", default=True)
    grp_m.add_argument("--no-metrics", dest="enable_metrics", action="store_false")
    grp_a = p_install.add_mutually_exclusive_group()
    grp_a.add_argument("--enable-audit", dest="enable_audit", action="store_true", default=True)
    grp_a.add_argument("--no-audit", dest="enable_audit", action="store_false")
    grp_r = p_install.add_mutually_exclusive_group()
    grp_r.add_argument("--install-ripgrep", dest="install_ripgrep", action="store_true", default=True)
    grp_r.add_argument("--skip-ripgrep", dest="install_ripgrep", action="store_false")
    p_install.add_argument("--yes", action="store_true")
    p_install.set_defaults(func=cmd_install_service)

    p_uninst = sub.add_parser("uninstall-service", help="Remove systemd service (requires sudo)")
    p_uninst.add_argument("--config-dir", default="/etc/mymcp")
    p_uninst.add_argument("--log-dir", default="/var/log/mymcp")
    p_uninst.add_argument("--purge", action="store_true",
                          help="Also delete config-dir and log-dir")
    p_uninst.set_defaults(func=cmd_uninstall_service)
```

- [ ] **Step 4: Run all CLI tests**

```bash
.venv/bin/pytest tests/test_cli.py tests/test_install_service_cli.py -v --tb=short
```

- [ ] **Step 5: Commit**

```bash
git add tests/test_install_service_cli.py src/mymcp/cli.py
git commit -m "feat(cli): add install-service / uninstall-service subcommands"
```

---

### Task 7: token CLI tests + impl

**Files:** `tests/test_token_cli.py`, `src/mymcp/cli.py`

- [ ] **Step 1: Write the tests**

```python
"""Tests for `mymcp token list/add/revoke/rotate-admin/rotate-metrics/disable-metrics`."""
import json
from unittest.mock import patch


def _run(*args, env_file: str | None = None):
    from mymcp.cli import main
    if env_file:
        import os
        os.environ["MYMCP_ENV_FILE"] = env_file
    return main(list(args))


def _bootstrap(tmp_path):
    """Write a minimal .env and tokens.json for token CLI tests."""
    env = tmp_path / ".env"
    tok = tmp_path / "tokens.json"
    env.write_text(
        "MYMCP_ADMIN_TOKEN=adm_initial\n"
        "MYMCP_METRICS_TOKEN=met_initial\n"
        f"MYMCP_TOKEN_FILE={tok}\n"
    )
    tok.write_text(json.dumps({"tokens": {}, "admin_token": "adm_initial"}))
    return env, tok


def test_token_add_creates_rw_token(tmp_path, capsys):
    env, tok = _bootstrap(tmp_path)
    rc = _run("token", "add", "--name", "laptop", "--role", "rw",
              env_file=str(env))
    assert rc == 0
    out = capsys.readouterr().out
    assert "tok_" in out
    body = json.loads(tok.read_text())
    names = [v["name"] for v in body["tokens"].values()]
    assert "laptop" in names


def test_token_list_shows_admin_metrics_status(tmp_path, capsys):
    env, _ = _bootstrap(tmp_path)
    rc = _run("token", "list", env_file=str(env))
    assert rc == 0
    out = capsys.readouterr().out.lower()
    assert "admin" in out
    assert "metrics" in out


def test_token_revoke_removes(tmp_path, capsys):
    env, tok = _bootstrap(tmp_path)
    body = json.loads(tok.read_text())
    body["tokens"]["tok_xxx"] = {
        "name": "old", "role": "ro", "enabled": True,
        "created_at": "x", "last_used": None,
    }
    tok.write_text(json.dumps(body))

    rc = _run("token", "revoke", "tok_xxx", env_file=str(env))
    assert rc == 0
    body = json.loads(tok.read_text())
    assert "tok_xxx" not in body["tokens"]


def test_token_rotate_admin_updates_env(tmp_path, capsys):
    env, _ = _bootstrap(tmp_path)
    rc = _run("token", "rotate-admin", env_file=str(env))
    assert rc == 0
    text = env.read_text()
    assert "MYMCP_ADMIN_TOKEN=adm_initial" not in text
    out = capsys.readouterr().out
    assert "tok_" in out


def test_token_rotate_metrics_updates_env(tmp_path):
    env, _ = _bootstrap(tmp_path)
    rc = _run("token", "rotate-metrics", env_file=str(env))
    assert rc == 0
    text = env.read_text()
    assert "MYMCP_METRICS_TOKEN=met_initial" not in text
    assert "MYMCP_METRICS_TOKEN=tok_" in text


def test_token_disable_metrics_blanks_env(tmp_path):
    env, _ = _bootstrap(tmp_path)
    rc = _run("token", "disable-metrics", env_file=str(env))
    assert rc == 0
    text = env.read_text()
    assert "MYMCP_METRICS_TOKEN=\n" in text or "MYMCP_METRICS_TOKEN=" in text
    assert "MYMCP_METRICS_TOKEN=met_initial" not in text
```

- [ ] **Step 2: Run, expect failure**

```bash
.venv/bin/pytest tests/test_token_cli.py -v --tb=short
```

- [ ] **Step 3: Add token subcommand handlers in `cli.py`**

Add these functions:

```python
def _resolve_env_path() -> str:
    from mymcp.config import _discover_env_file
    p = _discover_env_file()
    if not p:
        print("error: no .env file found (set MYMCP_ENV_FILE or run install-service first)",
              file=sys.stderr)
        raise SystemExit(2)
    return p


def cmd_token_list(_args: argparse.Namespace) -> int:
    env_path = _resolve_env_path()
    from mymcp.config import reset_settings_cache
    os.environ["MYMCP_ENV_FILE"] = env_path
    reset_settings_cache()
    from mymcp import config
    from mymcp.auth import TokenStore
    s = config.get_settings()

    print(f"admin token:   {'set' if s.admin_token else 'NOT SET'}")
    print(f"metrics token: {'set' if s.metrics_token else 'disabled (empty)'}")
    print()
    if not s.token_file or not os.path.exists(s.token_file):
        print("(no tokens.json yet)")
        return 0
    store = TokenStore(s.token_file, s.admin_token)
    tokens = store.list_tokens()
    if not tokens:
        print("(no ro/rw tokens)")
        return 0
    for t, info in tokens.items():
        marker = "x" if info.get("enabled", True) else " "
        print(f"[{marker}] {info.get('role','rw'):2}  {info.get('name','-'):20}  {t}")
    return 0


def cmd_token_add(args: argparse.Namespace) -> int:
    env_path = _resolve_env_path()
    os.environ["MYMCP_ENV_FILE"] = env_path
    from mymcp.config import reset_settings_cache, get_settings
    reset_settings_cache()
    s = get_settings()
    from mymcp.auth import TokenStore
    store = TokenStore(s.token_file, s.admin_token)
    new = store.create_token(args.name, role=args.role)
    print(new)
    return 0


def cmd_token_revoke(args: argparse.Namespace) -> int:
    env_path = _resolve_env_path()
    os.environ["MYMCP_ENV_FILE"] = env_path
    from mymcp.config import reset_settings_cache, get_settings
    reset_settings_cache()
    s = get_settings()
    from mymcp.auth import TokenStore
    store = TokenStore(s.token_file, s.admin_token)
    if store.revoke_token(args.token):
        print(f"revoked {args.token}")
        return 0
    print(f"not found: {args.token}", file=sys.stderr)
    return 1


def _rotate_in_env(env_path: str, key: str) -> str:
    from mymcp.deploy.setup import make_token, update_env_file
    new = make_token()
    update_env_file(env_path, {key: new})
    return new


def cmd_token_rotate_admin(_args: argparse.Namespace) -> int:
    env_path = _resolve_env_path()
    new = _rotate_in_env(env_path, "MYMCP_ADMIN_TOKEN")
    print(new)
    return 0


def cmd_token_rotate_metrics(_args: argparse.Namespace) -> int:
    env_path = _resolve_env_path()
    new = _rotate_in_env(env_path, "MYMCP_METRICS_TOKEN")
    print(new)
    return 0


def cmd_token_disable_metrics(_args: argparse.Namespace) -> int:
    env_path = _resolve_env_path()
    from mymcp.deploy.setup import update_env_file
    update_env_file(env_path, {"MYMCP_METRICS_TOKEN": ""})
    print("metrics endpoint disabled.")
    return 0
```

Then in `build_parser`:

```python
    p_token = sub.add_parser("token", help="Manage tokens")
    p_token_sub = p_token.add_subparsers(dest="token_cmd", required=True)

    p_tok_list = p_token_sub.add_parser("list")
    p_tok_list.set_defaults(func=cmd_token_list)

    p_tok_add = p_token_sub.add_parser("add")
    p_tok_add.add_argument("--name", required=True)
    p_tok_add.add_argument("--role", choices=["ro", "rw"], required=True)
    p_tok_add.set_defaults(func=cmd_token_add)

    p_tok_rev = p_token_sub.add_parser("revoke")
    p_tok_rev.add_argument("token")
    p_tok_rev.set_defaults(func=cmd_token_revoke)

    p_tok_ra = p_token_sub.add_parser("rotate-admin")
    p_tok_ra.set_defaults(func=cmd_token_rotate_admin)

    p_tok_rm = p_token_sub.add_parser("rotate-metrics")
    p_tok_rm.set_defaults(func=cmd_token_rotate_metrics)

    p_tok_dm = p_token_sub.add_parser("disable-metrics")
    p_tok_dm.set_defaults(func=cmd_token_disable_metrics)
```

- [ ] **Step 4: Verify all token tests pass**

```bash
.venv/bin/pytest tests/test_token_cli.py -v
```

- [ ] **Step 5: Commit**

```bash
git add tests/test_token_cli.py src/mymcp/cli.py
git commit -m "feat(cli): add token list/add/revoke/rotate-admin/rotate-metrics/disable-metrics"
```

---

### Task 8: migrate-from-legacy + doctor + final tests

**Files:** `src/mymcp/deploy/migrate.py`, `tests/test_deploy_migrate.py`, `tests/test_doctor.py`, `src/mymcp/cli.py`

- [ ] **Step 1: Migrate tests**

```python
# tests/test_deploy_migrate.py
import json
from pathlib import Path


def test_rewrite_env_keys_replaces_mcp_with_mymcp():
    from mymcp.deploy.migrate import rewrite_env_keys
    src = (
        "MCP_HOST=0.0.0.0\n"
        "MCP_PORT=8765\n"
        "MCP_ADMIN_TOKEN=adm\n"
        "MCP_AUDIT_ENABLED=true\n"
        "MCP_APP_DIR=/opt/mymcp\n"
    )
    out = rewrite_env_keys(src)
    assert "MYMCP_HOST=0.0.0.0" in out
    assert "MYMCP_PORT=8765" in out
    assert "MYMCP_ADMIN_TOKEN=adm" in out
    assert "MYMCP_AUDIT_ENABLED=true" in out
    # APP_DIR is dropped
    assert "APP_DIR" not in out


def test_rewrite_env_keys_preserves_unknown_lines():
    from mymcp.deploy.migrate import rewrite_env_keys
    src = "# comment\n\nMCP_HOST=1.2.3.4\nFOO=bar\n"
    out = rewrite_env_keys(src)
    assert "# comment" in out
    assert "FOO=bar" in out
    assert "MYMCP_HOST=1.2.3.4" in out


def test_legacy_dir_present(tmp_path):
    from mymcp.deploy.migrate import legacy_dir_present
    assert legacy_dir_present(tmp_path) is False
    (tmp_path / ".env").write_text("MCP_HOST=0.0.0.0\n")
    assert legacy_dir_present(tmp_path) is True
```

- [ ] **Step 2: Doctor tests**

```python
# tests/test_doctor.py
def test_doctor_runs_and_returns_zero(monkeypatch, capsys):
    from mymcp.cli import main
    monkeypatch.setattr("mymcp.deploy.service.systemd_available", lambda: True)
    rc = main(["doctor"])
    assert rc == 0
    out = capsys.readouterr().out.lower()
    assert "python" in out
```

- [ ] **Step 3: Run, expect failure**

```bash
.venv/bin/pytest tests/test_deploy_migrate.py tests/test_doctor.py -v --tb=short
```

- [ ] **Step 4: Implement `src/mymcp/deploy/migrate.py`**

```python
"""Migrate a 1.x install at /opt/mymcp to the 2.0 layout."""
from __future__ import annotations

import re
import shutil
from pathlib import Path

_RENAME_RE = re.compile(r"^\s*MCP_([A-Z_][A-Z0-9_]*)\s*=", re.MULTILINE)
_DROP_KEYS = {"APP_DIR"}


def legacy_dir_present(path: Path | str) -> bool:
    return (Path(path) / ".env").is_file()


def rewrite_env_keys(text: str) -> str:
    """Replace `MCP_FOO=` with `MYMCP_FOO=` line by line. Drop legacy keys."""
    out_lines = []
    for raw in text.splitlines():
        line = raw
        m = re.match(r"^\s*MCP_([A-Z_][A-Z0-9_]*)\s*=(.*)$", raw)
        if m:
            key, val = m.group(1), m.group(2)
            if key in _DROP_KEYS:
                continue
            line = f"MYMCP_{key}={val}"
        out_lines.append(line)
    return "\n".join(out_lines) + ("\n" if not out_lines or out_lines[-1] != "" else "")


def copy_tokens(legacy_dir: Path, target_path: Path) -> None:
    src = legacy_dir / "tokens.json"
    if src.exists():
        target_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, target_path)
```

- [ ] **Step 5: Add migrate + doctor subcommands in `cli.py`**

```python
def cmd_migrate_from_legacy(args: argparse.Namespace) -> int:
    if not _require_root():
        return 2
    from mymcp.deploy import migrate as mig
    from mymcp.deploy import service, setup
    legacy = pathlib_Path(args.legacy_dir)
    if not mig.legacy_dir_present(legacy):
        print(f"error: no legacy install at {legacy} (.env missing).", file=sys.stderr)
        return 2

    new_cfg = pathlib_Path("/etc/mymcp")
    new_env = new_cfg / ".env"
    new_tokens = new_cfg / "tokens.json"

    src_text = (legacy / ".env").read_text()
    rewritten = mig.rewrite_env_keys(src_text)

    plan_lines = [
        f"  rewrite {legacy / '.env'} → {new_env} (MCP_*→MYMCP_*)",
        f"  copy   {legacy / 'tokens.json'} → {new_tokens}",
        f"  install systemd unit /etc/systemd/system/mymcp.service",
        f"  systemctl daemon-reload && systemctl restart mymcp",
    ]
    if args.dry_run:
        print("[dry-run] would do:")
        for l in plan_lines:
            print(l)
        return 0

    setup.ensure_directory(new_cfg, mode=0o750)
    new_env.write_text(rewritten)
    import os as _os
    _os.chmod(new_env, 0o600)
    mig.copy_tokens(legacy, new_tokens)

    if not service.systemd_available():
        print("warning: systemd not detected; skipping unit install.", file=sys.stderr)
    else:
        exec_path = service.resolve_mymcp_executable()
        unit = service.render_service_unit(
            service_user="root",
            env_file=str(new_env),
            exec_start=f"{exec_path} serve --env-file {new_env}",
        )
        service.write_systemd_unit(unit)
        service.stop_service(check=False)
        service.daemon_reload()
        service.enable_service()
    print("Migration complete. Verify the new service then remove the old install:")
    print(f"  sudo systemctl status mymcp")
    print(f"  sudo rm -rf {legacy}")
    return 0


def cmd_doctor(_args: argparse.Namespace) -> int:
    import platform
    import shutil as _sh
    from mymcp.deploy import service
    print(f"python:    {platform.python_version()}")
    print(f"mymcp:     {__version__}")
    print(f"ripgrep:   {'ok' if _sh.which('rg') else 'missing'}")
    print(f"systemd:   {'ok' if service.systemd_available() else 'unavailable'}")
    return 0
```

Add at top of cli.py (next to other imports):

```python
from pathlib import Path as pathlib_Path
```

Then in `build_parser`:

```python
    p_mig = sub.add_parser("migrate-from-legacy", help="Migrate a /opt/mymcp 1.x install to 2.0")
    p_mig.add_argument("--legacy-dir", default="/opt/mymcp")
    p_mig.add_argument("--dry-run", action="store_true")
    p_mig.set_defaults(func=cmd_migrate_from_legacy)

    p_doc = sub.add_parser("doctor", help="System diagnostics")
    p_doc.set_defaults(func=cmd_doctor)
```

- [ ] **Step 6: Run new tests**

```bash
.venv/bin/pytest tests/test_deploy_migrate.py tests/test_doctor.py -v
```

- [ ] **Step 7: Run full suite**

```bash
.venv/bin/pytest tests/ -q --no-header --benchmark-disable --tb=no
```

- [ ] **Step 8: Commit**

```bash
git add src/mymcp/deploy/migrate.py tests/test_deploy_migrate.py tests/test_doctor.py src/mymcp/cli.py
git commit -m "feat(cli): add migrate-from-legacy and doctor subcommands"
```

---

### Task 9: Lint + final smoke

- [ ] **Step 1: Ruff + mypy**

```bash
.venv/bin/ruff check . && .venv/bin/ruff format --check . && .venv/bin/mypy src/mymcp
```

Fix any errors inline. Most common will be unused imports or mypy `Any` returns from the deploy modules — annotate.

- [ ] **Step 2: Smoke `mymcp --help` shows all subcommands**

```bash
.venv/bin/mymcp --help
```

Expected: lists `serve`, `version`, `install-service`, `uninstall-service`, `token`, `migrate-from-legacy`, `doctor`.

- [ ] **Step 3: Commit any lint fixes if needed**

```bash
git add -u && git diff --staged --quiet || git commit -m "style: ruff/mypy cleanup after Plan 2"
```

---

## Self-review

Spec coverage check:

| Spec section | Task |
|---|---|
| §4 install-service | Tasks 1-6 |
| §4 uninstall-service | Task 6 |
| §2 token list/add/revoke/rotate-*/disable-metrics | Task 7 |
| §5 migrate-from-legacy + dry-run | Task 8 |
| §2 doctor | Task 8 |

No placeholders. Function names consistent across tasks (`render_service_unit`, `build_env_dict`, `make_token` declared in Task 3/5, used in Task 6). Plan 3 picks up release/PyPI/README/CHANGELOG.
