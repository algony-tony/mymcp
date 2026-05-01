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


def render_service_unit(
    *, service_user: str, env_file: str, exec_start: str, working_directory: str = "/etc/mymcp"
) -> str:
    return _read_template().format(
        service_user=service_user,
        working_directory=working_directory,
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
        check=True,
        capture_output=True,
        text=True,
    )


def install_ripgrep() -> bool:
    """Install ripgrep via apt/dnf/pacman if not already present.
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
