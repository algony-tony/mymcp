"""Tests for deploy/service.py — unit render, helpers, ripgrep install."""
import pytest


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
